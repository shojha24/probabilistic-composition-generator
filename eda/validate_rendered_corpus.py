"""Validate rendered JFugue scores against their manifest-paired labels."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chord_gen import DURATION_BEATS
from voicing.dct import (
    compute_dct,
    predicate_isolated,
    predicate_octave,
    predicate_top,
)
from voicing.types import EXT_SLOTS, SLOT_TO_ROLE, Song, resolve_degrees


_SOURCE_NAME = re.compile(r"^song_(\d+)\.json$")
_NOTE = re.compile(
    r"^([A-G])(#?)(-?\d+)"
    r"(?:(ww|w\.?|h\.?|q\.?|s)|/(\d+(?:\.\d+)?))"
    r"(?:A(\d+))?$"
)
_NOTE_PC = {
    "C": 0,
    "C#": 1,
    "D": 2,
    "D#": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "G": 7,
    "G#": 8,
    "A": 9,
    "A#": 10,
    "B": 11,
}
_VOICE = re.compile(r"^V\d+$")
_AT = re.compile(r"^@(\d+(?:\.\d+)?)$")
_ARPEGGIO_BOUNDARY = re.compile(r"^#ARPEVENT(\d+)$")
_ARPEGGIO_GRID_STEPS = {"sixteenth": 1, "eighth": 2}
_ARPEGGIO_MIN_SUSTAIN_SIXTEENTHS = 2


def _parse_note(token: str) -> int | None:
    if token.startswith("R"):
        if token[1:] not in {*DURATION_BEATS, "s"}:
            raise ValueError(f"invalid rest token {token!r}")
        return None
    match = _NOTE.fullmatch(token)
    if match is None:
        raise ValueError(f"invalid JFugue note token {token!r}")
    name = match.group(1) + match.group(2)
    octave = int(match.group(3))
    return 12 * (octave + 1) + _NOTE_PC[name]


def _standard_duration_sixteenths(duration: str) -> float:
    if duration == "s":
        return 1.0
    return DURATION_BEATS[duration] * 4


def _parse_track_details(tokens: list[str]) -> list[dict]:
    events = []
    pending_onset = None
    for token in tokens:
        if token.startswith(("T", "V", "I")):
            continue
        if token.startswith("@"):
            match = _AT.fullmatch(token)
            if match is None:
                raise ValueError(f"invalid absolute-time token {token!r}")
            if pending_onset is not None:
                raise ValueError(
                    f"absolute-time token {token!r} has no following event"
                )
            onset = float(match.group(1)) * 16
            pending_onset = (
                int(round(onset)) if onset.is_integer() else onset
            )
            continue
        if token.startswith("R"):
            _parse_note(token)
            events.append({
                "midis": [],
                "duration": token[1:],
                "duration_sixteenths": _standard_duration_sixteenths(token[1:]),
                "velocity": None,
                "onset_sixteenths": pending_onset,
                "is_rest": True,
                "token": token,
            })
            pending_onset = None
            continue
        if token.startswith("#"):
            if _ARPEGGIO_BOUNDARY.fullmatch(token) is None:
                raise ValueError(f"invalid arpeggio boundary marker {token!r}")
            events.append({
                "midis": [],
                "duration": None,
                "duration_sixteenths": 0.0,
                "velocity": None,
                "onset_sixteenths": pending_onset,
                "is_rest": False,
                "is_timeline_marker": True,
                "boundary_event_index": int(
                    _ARPEGGIO_BOUNDARY.fullmatch(token).group(1)
                ),
                "token": token,
            })
            pending_onset = None
            continue
        percussion_parts = token.split("+")
        if percussion_parts and all(
            re.fullmatch(r"\[[A-Z0-9_]+\]s", part)
            for part in percussion_parts
        ):
            events.append({
                "midis": [],
                "duration": "s",
                "duration_sixteenths": 1.0,
                "velocity": None,
                "onset_sixteenths": pending_onset,
                "is_rest": False,
                "percussion": True,
                "token": token,
            })
            pending_onset = None
            continue
        notes = []
        duration_values = []
        velocities = []
        for note_token in token.split("+"):
            match = _NOTE.fullmatch(note_token)
            if match is None:
                raise ValueError(f"invalid JFugue note token {note_token!r}")
            notes.append(_parse_note(note_token))
            duration = match.group(4)
            numeric_duration = match.group(5)
            duration_values.append(
                ("standard", duration)
                if duration is not None
                else ("numeric", float(numeric_duration))
            )
            velocities.append(
                int(match.group(6)) if match.group(6) is not None else None
            )
        if len(set(duration_values)) != 1:
            raise ValueError(
                f"chord token has mixed durations {token!r}"
            )
        duration_kind, duration_value = duration_values[0]
        events.append({
            "midis": notes,
            "duration": duration_value if duration_kind == "standard" else None,
            "duration_sixteenths": (
                _standard_duration_sixteenths(duration_value)
                if duration_kind == "standard"
                else duration_value * 16
            ),
            "velocity": velocities[0] if len(set(velocities)) == 1 else None,
            "velocities": velocities,
            "onset_sixteenths": pending_onset,
            "is_rest": False,
            "is_timeline_marker": False,
            "token": token,
        })
        pending_onset = None
    if pending_onset is not None:
        raise ValueError("absolute-time token has no following event")
    return events


def parse_score_line_voice_details(line: str) -> dict[str, list[dict]]:
    """Return detailed events for every voice in one score line."""
    tokens = line.split()
    voice_positions = [
        index for index, token in enumerate(tokens) if _VOICE.fullmatch(token)
    ]
    if not voice_positions:
        raise ValueError("score line must contain voice tracks")
    voices = {}
    for position_index, start in enumerate(voice_positions):
        voice = tokens[start]
        if voice in voices:
            raise ValueError(f"score line contains duplicate {voice} track")
        end = (
            voice_positions[position_index + 1]
            if position_index + 1 < len(voice_positions)
            else len(tokens)
        )
        voices[voice] = _parse_track_details(tokens[start + 1:end])
    if "V0" not in voices or "V1" not in voices:
        raise ValueError("score line must contain V0 and V1 tracks")
    return voices


def parse_score_line_detailed(line: str) -> tuple[list[dict], list[dict]]:
    """Return parsed chord and bass tokens, including duration metadata."""
    voices = parse_score_line_voice_details(line)
    return voices["V0"], voices["V1"]


def _parse_track(tokens: list[str]) -> list[list[int]]:
    return [
        event["midis"]
        for event in _parse_track_details(tokens)
        if not event.get("is_timeline_marker")
    ]


def parse_score_line(line: str) -> tuple[list[list[int]], list[list[int]]]:
    """Return chord-track and bass-track MIDI events from one score line."""
    chord_tokens, bass_tokens = parse_score_line_detailed(line)
    return (
        [event["midis"] for event in chord_tokens],
        [event["midis"] for event in bass_tokens],
    )


def parse_score_blocks(path: str | Path) -> dict[int, str]:
    """Parse START_SONG_N/END_SONG blocks without relying on source names."""
    blocks: dict[int, str] = {}
    current_id = None
    payload: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("START_SONG_"):
            if current_id is not None:
                raise ValueError("nested score song block")
            try:
                current_id = int(stripped.removeprefix("START_SONG_"))
            except ValueError as error:
                raise ValueError(f"malformed start marker {stripped!r}") from error
            payload = []
        elif stripped.startswith("END_SONG"):
            if current_id is None:
                raise ValueError("score has an END_SONG without a start marker")
            if current_id in blocks:
                raise ValueError(f"duplicate score block ordinal {current_id}")
            blocks[current_id] = " ".join(payload).strip()
            current_id = None
            payload = []
        elif current_id is not None and stripped:
            payload.append(stripped)
    if current_id is not None:
        raise ValueError(f"unterminated score block {current_id}")
    return blocks


def _parse_strict_score_blocks(path: Path) -> list[tuple[int, str]]:
    """Parse the exact three-line block shape used by multitrack exports."""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks = []
    cursor = 0
    while cursor < len(lines):
        start = lines[cursor]
        match = re.fullmatch(r"START_SONG_(\d+)", start)
        if match is None:
            raise ValueError(f"expected start marker at line {cursor + 1}")
        if cursor + 2 >= len(lines):
            raise ValueError(f"truncated score block at line {cursor + 1}")
        score_line = lines[cursor + 1]
        if not score_line.strip():
            raise ValueError(f"blank score line at line {cursor + 2}")
        if lines[cursor + 2] != "END_SONG":
            raise ValueError(
                f"expected END_SONG at line {cursor + 3}"
            )
        blocks.append((int(match.group(1)), score_line))
        cursor += 3
    return blocks


def _voice_token_segments(line: str) -> dict[str, list[str]]:
    tokens = line.split()
    positions = [
        index for index, token in enumerate(tokens)
        if _VOICE.fullmatch(token)
    ]
    if not positions:
        raise ValueError("score line contains no voice marker")
    voices: dict[str, list[str]] = {}
    for position_index, start in enumerate(positions):
        voice = tokens[start]
        if voice in voices:
            raise ValueError(f"score line contains duplicate {voice} track")
        end = (
            positions[position_index + 1]
            if position_index + 1 < len(positions)
            else len(tokens)
        )
        fragment_start = 0 if position_index == 0 else start
        voices[voice] = tokens[fragment_start:end]
    return voices


def _multitrack_block_hash(ordinal: int, line: str) -> str:
    canonical = f"START_SONG_{ordinal}\n{line}\nEND_SONG\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _multitrack_source_timeline(record: dict) -> float | None:
    source_dir = record.get("source_dir")
    source_file = record.get("source_file")
    if not isinstance(source_dir, str) or not isinstance(source_file, str):
        return None
    source_path = Path(source_dir) / source_file
    if not source_path.is_file():
        return None
    try:
        progression = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    total = 0.0
    for event in progression.get("chords", ()):
        duration = event.get("duration_token")
        if not isinstance(duration, str) or duration not in DURATION_BEATS:
            return None
        total += float(DURATION_BEATS[duration]) * 4.0
    return total


def validate_multitrack_outputs(
    mixed_path: str | Path,
    chords_path: str | Path,
    bass_path: str | Path,
    percussion_path: str | Path,
    manifest_path: str | Path,
) -> dict:
    """Validate synchronized role files and their declared manifest pairing."""
    paths = {
        "mixed": Path(mixed_path).resolve(),
        "chords": Path(chords_path).resolve(),
        "bass": Path(bass_path).resolve(),
        "percussion": Path(percussion_path).resolve(),
        "manifest": Path(manifest_path).resolve(),
    }
    report = {
        "valid": False,
        "mixed": str(paths["mixed"]),
        "chords": str(paths["chords"]),
        "bass": str(paths["bass"]),
        "percussion": str(paths["percussion"]),
        "manifest": str(paths["manifest"]),
        "song_count": 0,
        "issues": [],
        "hard_failure_count": 0,
    }

    def fail(category: str, detail: str) -> None:
        report["issues"].append({
            "category": category,
            "detail": detail,
        })
        report["hard_failure_count"] += 1

    file_bytes: dict[str, bytes] = {}
    file_text: dict[str, str] = {}
    for role in ("mixed", "chords", "bass", "percussion"):
        path = paths[role]
        if not path.is_file():
            fail("multitrack_missing_file", f"{role}: {path}")
            continue
        try:
            raw = path.read_bytes()
            file_bytes[role] = raw
            file_text[role] = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            fail("multitrack_missing_file", f"{role}: {error}")

    try:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail("multitrack_manifest_mismatch", str(error))
        return report
    if not isinstance(manifest, dict):
        fail("multitrack_manifest_mismatch", "manifest must be an object")
        return report

    track_outputs = manifest.get("track_outputs")
    records = manifest.get("records")
    if not isinstance(track_outputs, dict) or not isinstance(records, list):
        fail(
            "multitrack_manifest_mismatch",
            "manifest must declare track_outputs and records",
        )
        return report
    if track_outputs.get("schema_version") != 1:
        fail(
            "multitrack_manifest_mismatch",
            f"unsupported track_outputs.schema_version="
            f"{track_outputs.get('schema_version')!r}",
        )
    declared_output = manifest.get("output")
    if (
        not isinstance(declared_output, str)
        or Path(declared_output).resolve() != paths["mixed"]
    ):
        fail(
            "multitrack_manifest_mismatch",
            "manifest output does not match the mixed score path",
        )
    declared_paths = track_outputs.get("paths")
    expected_voices = {
        "mixed": ["V0", "V1", "V9"],
        "chords": ["V0"],
        "bass": ["V1"],
        "percussion": ["V9"],
    }
    if not isinstance(declared_paths, dict):
        fail("multitrack_manifest_mismatch", "track_outputs.paths is missing")
    else:
        for role in expected_voices:
            declared = declared_paths.get(role)
            if (
                not isinstance(declared, str)
                or Path(declared).resolve() != paths[role]
            ):
                fail(
                    "multitrack_manifest_mismatch",
                    f"{role} path does not match the declared output",
                )
    if track_outputs.get("voices") != expected_voices:
        fail(
            "multitrack_voice_mismatch",
            f"expected={expected_voices!r} "
            f"actual={track_outputs.get('voices')!r}",
        )

    declared_count = track_outputs.get("song_count")
    if (
        not isinstance(declared_count, int)
        or isinstance(declared_count, bool)
        or declared_count < 0
    ):
        fail("multitrack_manifest_mismatch", "invalid track_outputs.song_count")
        declared_count = len(records)
    report["song_count"] = declared_count
    if len(records) != declared_count:
        fail(
            "multitrack_song_count_mismatch",
            f"manifest records={len(records)} declared={declared_count}",
        )

    parsed: dict[str, list[tuple[int, str]]] = {}
    for role, text in file_text.items():
        try:
            parsed[role] = _parse_strict_score_blocks(paths[role])
        except ValueError as error:
            fail("multitrack_marker_mismatch", f"{role}: {error}")

    sequences = {
        role: [ordinal for ordinal, _line in blocks]
        for role, blocks in parsed.items()
    }
    mixed_ordinals = sequences.get("mixed")
    if mixed_ordinals is not None:
        for role, ordinals in sequences.items():
            if role != "mixed" and ordinals != mixed_ordinals:
                fail(
                    "multitrack_marker_mismatch",
                    f"{role} marker sequence differs from mixed",
                )
    declared_ordinals = track_outputs.get("ordinals")
    manifest_ordinals = [
        record.get("ordinal") if isinstance(record, dict) else None
        for record in records
    ]
    expected_ordinals = list(range(declared_count))
    if (
        declared_ordinals != expected_ordinals
        or manifest_ordinals != expected_ordinals
        or any(ordinals != expected_ordinals for ordinals in sequences.values())
    ):
        fail(
            "multitrack_ordinal_mismatch",
            f"expected={expected_ordinals!r}",
        )
    for role in ("mixed", "chords", "bass", "percussion"):
        blocks = parsed.get(role)
        if blocks is not None and len(blocks) != declared_count:
            fail(
                "multitrack_song_count_mismatch",
                f"{role} blocks={len(blocks)} declared={declared_count}",
            )

    complete_hashes = track_outputs.get("sha256")
    if not isinstance(complete_hashes, dict):
        fail("multitrack_manifest_mismatch", "track_outputs.sha256 is missing")
    else:
        for role, raw in file_bytes.items():
            actual = hashlib.sha256(raw).hexdigest()
            if complete_hashes.get(role) != actual:
                fail(
                    "multitrack_hash_mismatch",
                    f"{role}: manifest={complete_hashes.get(role)!r} "
                    f"actual={actual}",
                )

    records_by_ordinal = {
        record.get("ordinal"): record
        for record in records
        if isinstance(record, dict)
    }
    for role, blocks in parsed.items():
        if role not in expected_voices:
            continue
        required = set(expected_voices[role])
        for ordinal, line in blocks:
            try:
                segments = _voice_token_segments(line)
            except ValueError as error:
                fail(
                    "multitrack_voice_mismatch",
                    f"{role} ordinal={ordinal}: {error}",
                )
                continue
            if set(segments) != required:
                fail(
                    "multitrack_voice_mismatch",
                    f"{role} ordinal={ordinal}: expected={required!r} "
                    f"actual={set(segments)!r}",
                )

    mixed_blocks = dict(parsed.get("mixed", ()))
    role_blocks = {
        role: dict(parsed.get(role, ()))
        for role in ("chords", "bass", "percussion")
    }
    for ordinal, mixed_line in mixed_blocks.items():
        try:
            mixed_segments = _voice_token_segments(mixed_line)
        except ValueError:
            continue
        for role, voice in (
            ("chords", "V0"),
            ("bass", "V1"),
            ("percussion", "V9"),
        ):
            role_line = role_blocks[role].get(ordinal)
            if role_line is None:
                continue
            try:
                role_segments = _voice_token_segments(role_line)
            except ValueError:
                continue
            if mixed_segments.get(voice) != role_segments.get(voice):
                fail(
                    "multitrack_role_content_mismatch",
                    f"{role} ordinal={ordinal} does not match mixed {voice}",
                )

    record_hashes = {
        "mixed": mixed_blocks,
        "chords": role_blocks["chords"],
        "bass": role_blocks["bass"],
        "percussion": role_blocks["percussion"],
    }
    for ordinal, record in records_by_ordinal.items():
        if not isinstance(ordinal, int):
            continue
        declared_hashes = record.get("track_hashes")
        if not isinstance(declared_hashes, dict):
            fail(
                "multitrack_manifest_mismatch",
                f"record {ordinal} is missing track_hashes",
            )
            continue
        for role, blocks in record_hashes.items():
            line = blocks.get(ordinal)
            if line is None:
                continue
            actual = _multitrack_block_hash(ordinal, line)
            if declared_hashes.get(role) != actual:
                fail(
                    "multitrack_hash_mismatch",
                    f"record={ordinal} role={role}",
                )

    for ordinal, mixed_line in mixed_blocks.items():
        try:
            mixed_segments = _voice_token_segments(mixed_line)
            details = {
                voice: _parse_track_details(mixed_segments[voice])
                for voice in ("V0", "V1", "V9")
            }
        except (KeyError, ValueError) as error:
            fail(
                "multitrack_timeline_mismatch",
                f"mixed ordinal={ordinal}: {error}",
            )
            continue
        timelines = {
            voice: _track_timeline_sixteenths(
                tokens,
                absolute=voice == "V0",
            )
            for voice, tokens in details.items()
        }
        if len({round(value, 9) for value in timelines.values()}) != 1:
            fail(
                "multitrack_timeline_mismatch",
                f"mixed ordinal={ordinal}: timelines={timelines!r}",
            )
        record = records_by_ordinal.get(ordinal)
        if isinstance(record, dict):
            expected_timeline = _multitrack_source_timeline(record)
            if (
                expected_timeline is not None
                and not math.isclose(
                    timelines["V0"],
                    expected_timeline,
                    rel_tol=0,
                    abs_tol=1e-6,
                )
            ):
                fail(
                    "multitrack_timeline_mismatch",
                    f"ordinal={ordinal}: expected={expected_timeline} "
                    f"actual={timelines['V0']}",
                )
        for role, voice in (
            ("chords", "V0"),
            ("bass", "V1"),
            ("percussion", "V9"),
        ):
            role_line = role_blocks[role].get(ordinal)
            if role_line is None:
                continue
            try:
                role_segments = _voice_token_segments(role_line)
                role_details = _parse_track_details(role_segments[voice])
                role_timeline = _track_timeline_sixteenths(
                    role_details,
                    absolute=voice == "V0",
                )
                mixed_timeline = timelines[voice]
            except (KeyError, ValueError) as error:
                fail(
                    "multitrack_timeline_mismatch",
                    f"{role} ordinal={ordinal}: {error}",
                )
                continue
            if not math.isclose(
                role_timeline,
                mixed_timeline,
                rel_tol=0,
                abs_tol=1e-6,
            ):
                fail(
                    "multitrack_timeline_mismatch",
                    f"{role} ordinal={ordinal}: mixed={mixed_timeline} "
                    f"role={role_timeline}",
                )

    report["valid"] = not report["issues"]
    return report


def _label_dict(chord) -> dict:
    return {
        "is_no_chord": chord.is_no_chord,
        "root_interval": chord.root_interval,
        "triad": chord.triad,
        "bass_interval": chord.bass_interval,
        "seventh": chord.seventh,
        "ninth": chord.ninth,
        "eleventh": chord.eleventh,
        "thirteenth": chord.thirteenth,
    }


def _type_key(chord_type: tuple) -> str:
    return json.dumps(list(chord_type), separators=(",", ":"))


def _new_bucket() -> dict:
    return {
        "events": 0,
        "root_retained": 0,
        "root_omitted": 0,
        "dct_events": 0,
        "dct_exposed": 0,
        "extension_events": 0,
        "extension_retained": 0,
        "extension_drop_events": 0,
        "extension_drop_roles": 0,
        "permitted_omissions": 0,
        "bass_correct": 0,
        "combined_root_present": 0,
        "combined_active_degree_coverage": 0,
        "combined_missing_active_degrees": 0,
        "unrequested_pitch_classes": 0,
        "violations": 0,
    }


def _issue(
    issues: list[dict],
    category: str,
    record: dict,
    event_index: int | None = None,
    label: dict | None = None,
    midi: list[int] | None = None,
    detail: str | None = None,
) -> None:
    issues.append({
        "category": category,
        "source_file": record.get("source_file"),
        "ordinal": record.get("ordinal"),
        "event_index": event_index,
        "label": label,
        "rendered_midi": midi,
        "detail": detail,
    })


def _load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError(f"{path} must contain a records array")
    return data


def _arpeggio_slot_count(duration: object) -> int:
    if not isinstance(duration, str):
        raise ValueError(f"Unknown duration token {duration!r}")
    try:
        beats = DURATION_BEATS[duration]
    except KeyError:
        raise ValueError(f"Unknown duration token {duration!r}") from None
    slots = beats * 4
    if not float(slots).is_integer():
        raise ValueError(
            f"Duration token {duration!r} cannot be represented on a "
            "sixteenth-note arpeggio grid"
        )
    return int(slots)


def _track_timeline_sixteenths(
    track: list[dict],
    absolute: bool = False,
) -> float:
    if not absolute:
        return sum(float(event["duration_sixteenths"]) for event in track)
    cursor = 0.0
    for event in track:
        if event.get("is_timeline_marker"):
            onset = event.get("onset_sixteenths")
            if onset is not None:
                cursor = max(cursor, float(onset))
            continue
        onset = event.get("onset_sixteenths")
        duration = float(event["duration_sixteenths"])
        if onset is None:
            cursor += duration
        else:
            cursor = max(cursor, float(onset) + duration)
    return cursor


def _validate_timed_arpeggio_track(
    chord_tokens: list[dict],
    bass_tokens: list[dict],
    percussion_tokens: list[dict] | None,
    song: Song,
    record: dict,
    issues: list[dict],
) -> tuple[list[list[int]], int]:
    """Validate spec 13 timed attacks and return their source voicings."""
    failures = 0

    def fail(
        category: str,
        event_index: int | None = None,
        detail: str | None = None,
        midi: list[int] | None = None,
    ) -> None:
        nonlocal failures
        _issue(
            issues, category, record, event_index,
            midi=midi, detail=detail,
        )
        failures += 1

    info = record.get("arpeggio")
    if not isinstance(info, dict):
        fail("arpeggio_manifest_missing")
        info = {}

    profile_id = info.get("performance_profile")
    profile = None
    try:
        from instruments import ARPEGGIO_PROFILES
        profile = ARPEGGIO_PROFILES[profile_id]
    except (ImportError, KeyError, TypeError):
        fail(
            "arpeggio_profile_invalid",
            detail=f"profile={profile_id!r}",
        )
    if info.get("meter") != "4/4":
        fail(
            "arpeggio_meter_invalid",
            detail=f"expected='4/4' actual={info.get('meter')!r}",
        )
    gate_ratio = info.get("gate_ratio")
    if (
        isinstance(gate_ratio, bool)
        or not isinstance(gate_ratio, (int, float))
        or not math.isfinite(float(gate_ratio))
        or gate_ratio <= 0
    ):
        fail("arpeggio_gate_ratio_invalid")
        gate_ratio = 1.0
    elif profile is not None and not math.isclose(
        float(gate_ratio), profile.gate_ratio, rel_tol=0, abs_tol=1e-9
    ):
        fail(
            "arpeggio_gate_ratio_mismatch",
            detail=(
                f"profile={profile.gate_ratio} "
                f"manifest={gate_ratio}"
            ),
        )
    sustain_to_event_end = info.get("sustain_to_event_end") is True

    event_count = len(song.chords)

    def array(name: str) -> list:
        value = info.get(name)
        if not isinstance(value, list) or len(value) != event_count:
            fail(
                f"arpeggio_{name}_invalid",
                detail=f"expected {event_count} values",
            )
            return [None] * event_count
        return value

    def optional_array(name: str) -> list:
        value = info.get(name)
        if value is None:
            return [None] * event_count
        if not isinstance(value, list) or len(value) != event_count:
            fail(
                f"arpeggio_{name}_invalid",
                detail=f"expected {event_count} values",
            )
            return [None] * event_count
        return value

    subdivisions = array("event_subdivisions")
    onset_counts = array("event_onset_counts")
    attack_counts = optional_array("event_attack_counts")
    pair_counts = optional_array("event_pair_counts")
    pad_fallbacks = optional_array("event_pad_fallbacks")
    normalized_pad_fallbacks = []
    for event_index, value in enumerate(pad_fallbacks):
        if value is None:
            normalized_pad_fallbacks.append(False)
        elif isinstance(value, bool):
            normalized_pad_fallbacks.append(value)
        else:
            fail(
                "arpeggio_pad_fallback_invalid",
                event_index,
                detail=f"actual={value!r}",
            )
            normalized_pad_fallbacks.append(False)
    pad_fallbacks = normalized_pad_fallbacks
    pattern_families = array("event_pattern_families")
    motif_families = array("event_motif_families")
    start_phases = array("event_start_phases")

    raw_source = info.get("source_voicing_midis")
    source_shape_valid = (
        isinstance(raw_source, list)
        and len(raw_source) == event_count
    )
    if not source_shape_valid:
        fail(
            "arpeggio_source_voicing_count_mismatch",
            detail=f"expected {event_count} source voicing lists",
        )
    source_midis = [[] for _ in song.chords]
    if isinstance(raw_source, list):
        for event_index, value in enumerate(raw_source[:event_count]):
            if (
                not isinstance(value, list)
                or any(
                    not isinstance(pitch, int) or isinstance(pitch, bool)
                    for pitch in value
                )
            ):
                fail(
                    "arpeggio_source_voicing_invalid",
                    event_index,
                )
                continue
            source_midis[event_index] = list(value)

    source_strings = info.get("source_sounding_strings")
    if profile is not None and profile.family == "guitar":
        if not isinstance(source_strings, list) or len(source_strings) != event_count:
            fail(
                "arpeggio_guitar_provenance_invalid",
                detail=f"expected {event_count} string lists",
            )
            source_strings = [[] for _ in song.chords]
        for event_index, chord in enumerate(song.chords):
            strings = source_strings[event_index]
            if chord.is_no_chord:
                if strings != []:
                    fail(
                        "arpeggio_guitar_provenance_invalid",
                        event_index,
                        detail="no-chord events must have no sounding strings",
                    )
                continue
            if (
                not isinstance(strings, list)
                or len(strings) != len(source_midis[event_index])
                or any(
                    isinstance(string, bool)
                    or not isinstance(string, int)
                    or string < 1
                    or string > 6
                    for string in strings
                )
                or len(set(strings)) != len(strings)
            ):
                fail(
                    "arpeggio_guitar_provenance_invalid",
                    event_index,
                    detail="sounding_strings must be distinct integers 1..6",
                )

    expected_counts = []
    event_starts = []
    event_ends = []
    absolute_position = 0
    for event_index, chord in enumerate(song.chords):
        event_start = absolute_position
        try:
            source_units = _arpeggio_slot_count(chord.duration_token)
        except ValueError as error:
            fail(
                "arpeggio_source_duration_invalid",
                event_index,
                detail=str(error),
            )
            source_units = 0
        absolute_position += source_units
        event_starts.append(event_start)
        event_ends.append(absolute_position)
        subdivision = subdivisions[event_index]
        declared_count = onset_counts[event_index]
        declared_count_valid = (
            isinstance(declared_count, int)
            and not isinstance(declared_count, bool)
            and declared_count >= 0
        )
        pad_fallback = pad_fallbacks[event_index]
        if chord.is_no_chord:
            expected_count = 0
            if pad_fallback:
                fail(
                    "arpeggio_pad_fallback_invalid",
                    event_index,
                    detail="no-chord events cannot use pad fallback",
                )
            if subdivision is not None:
                fail(
                    "arpeggio_subdivision_invalid",
                    event_index,
                    detail="no-chord events must declare null subdivision",
                )
            if source_midis[event_index]:
                fail(
                    "arpeggio_no_chord_source_voicing",
                    event_index,
                    midi=source_midis[event_index],
                )
        else:
            if pad_fallback:
                expected_count = 1
                if subdivision is not None:
                    fail(
                        "arpeggio_pad_fallback_subdivision_invalid",
                        event_index,
                    )
                if declared_count_valid and declared_count != 0:
                    fail(
                        "arpeggio_pad_fallback_onset_count_invalid",
                        event_index,
                        detail=f"actual={declared_count}",
                    )
            elif subdivision not in {"sixteenth", "eighth"}:
                fail(
                    "arpeggio_subdivision_invalid",
                    event_index,
                    detail=f"subdivision={subdivision!r}",
                )
                expected_count = 0
            else:
                onsets_per_beat = 4 if subdivision == "sixteenth" else 2
                count = DURATION_BEATS[chord.duration_token] * onsets_per_beat
                expected_count = int(count) if float(count).is_integer() else 0
                if not float(count).is_integer():
                    fail(
                        "arpeggio_subdivision_duration_invalid",
                        event_index,
                    )
            if not source_midis[event_index]:
                fail("arpeggio_source_voicing_empty", event_index)
            if sustain_to_event_end and declared_count_valid and not pad_fallback:
                step = _ARPEGGIO_GRID_STEPS.get(subdivision)
                minimum_count = math.ceil(len(source_midis[event_index]) / 2)
                if declared_count < minimum_count:
                    fail(
                        "arpeggio_onset_count_too_small",
                        event_index,
                        detail=f"minimum={minimum_count} actual={declared_count}",
                    )
                if declared_count > len(source_midis[event_index]):
                    fail(
                        "arpeggio_onset_count_too_large",
                        event_index,
                        detail=(
                            f"maximum={len(source_midis[event_index])} "
                            f"actual={declared_count}"
                        ),
                    )
                if step is not None and declared_count * step > source_units:
                    fail(
                        "arpeggio_onset_grid_exceeds_event",
                        event_index,
                    )
                if (
                    step is not None
                    and (
                        max(0, declared_count - 1) * step
                        + _ARPEGGIO_MIN_SUSTAIN_SIXTEENTHS
                        > source_units
                    )
                ):
                    fail(
                        "arpeggio_sustain_tail_unavailable",
                        event_index,
                    )
                if (
                    subdivision == "eighth"
                    and declared_count * 2 > source_units
                ):
                    fail(
                        "arpeggio_eighth_grid_exceeds_event",
                        event_index,
                    )
                expected_count = declared_count
        expected_counts.append(expected_count)
        if (
            not declared_count_valid
            or (not sustain_to_event_end and declared_count != expected_count)
        ):
            fail(
                "arpeggio_onset_count_mismatch",
                event_index,
                detail=(
                    f"expected={expected_count} actual={declared_count!r}"
                ),
            )
        if sustain_to_event_end and attack_counts[event_index] is not None:
            declared_attacks = attack_counts[event_index]
            expected_attacks = (
                0
                if chord.is_no_chord or pad_fallback
                else len(source_midis[event_index])
            )
            if (
                not isinstance(declared_attacks, int)
                or isinstance(declared_attacks, bool)
                or declared_attacks != expected_attacks
            ):
                fail(
                    "arpeggio_attack_count_mismatch",
                    event_index,
                    detail=(
                        f"expected={expected_attacks} "
                        f"actual={declared_attacks!r}"
                    ),
                )
        if sustain_to_event_end and pair_counts[event_index] is not None:
            declared_pairs = pair_counts[event_index]
            if (
                not isinstance(declared_pairs, int)
                or isinstance(declared_pairs, bool)
                or declared_pairs < 0
                or declared_pairs > expected_count
                or (pad_fallback and declared_pairs != 0)
            ):
                fail(
                    "arpeggio_pair_count_invalid",
                    event_index,
                )
        if chord.is_no_chord or pad_fallback:
            if pattern_families[event_index] is not None:
                fail(
                    "arpeggio_pattern_provenance_invalid",
                    event_index,
                )
            if motif_families[event_index] is not None:
                fail(
                    "arpeggio_motif_provenance_invalid",
                    event_index,
                )
            if start_phases[event_index] is not None:
                fail(
                    "arpeggio_phase_provenance_invalid",
                    event_index,
                )
        else:
            if pattern_families[event_index] not in {
                "up", "down", "up_down", "outside_in"
            }:
                fail(
                    "arpeggio_pattern_provenance_invalid",
                    event_index,
                )
            if motif_families[event_index] not in {
                "straight", "bass_return", "top_return", "light_alternate"
            }:
                fail(
                    "arpeggio_motif_provenance_invalid",
                    event_index,
                )
            if (
                not isinstance(start_phases[event_index], int)
                or isinstance(start_phases[event_index], bool)
                or start_phases[event_index] < 0
            ):
                fail(
                    "arpeggio_phase_provenance_invalid",
                    event_index,
                )

    if info.get("event_supercycle_lengths") is not None:
        supercycle_lengths = array("event_supercycle_lengths")
    else:
        supercycle_lengths = [None] * event_count

    epsilon = 1e-6
    boundary_markers = [
        token for token in chord_tokens
        if token.get("is_timeline_marker")
    ]
    attack_tokens = [
        token for token in chord_tokens
        if not token.get("is_timeline_marker")
    ]
    if boundary_markers:
        if len(boundary_markers) != event_count:
            fail(
                "arpeggio_boundary_marker_count_mismatch",
                detail=(
                    f"expected={event_count} "
                    f"actual={len(boundary_markers)}"
                ),
            )
        marker_indices = [
            marker.get("boundary_event_index") for marker in boundary_markers
        ]
        if marker_indices != list(range(event_count)):
            fail(
                "arpeggio_boundary_marker_order_invalid",
                detail=f"actual={marker_indices!r}",
            )
        for marker in boundary_markers:
            marker_index = marker.get("boundary_event_index")
            if (
                not isinstance(marker_index, int)
                or isinstance(marker_index, bool)
                or not 0 <= marker_index < event_count
                or marker.get("onset_sixteenths") is None
                or not math.isclose(
                    float(marker["onset_sixteenths"]),
                    event_ends[marker_index],
                    rel_tol=0,
                    abs_tol=epsilon,
                )
            ):
                fail(
                    "arpeggio_boundary_marker_invalid",
                    detail=f"marker={marker.get('token')!r}",
                )

    expected_token_count = sum(
        1 if chord.is_no_chord or pad_fallbacks[event_index] else count
        for event_index, (chord, count) in enumerate(
            zip(song.chords, expected_counts)
        )
    )
    if len(attack_tokens) != expected_token_count:
        fail(
            "arpeggio_token_count_mismatch",
            detail=(
                f"expected={expected_token_count} "
                f"actual={len(attack_tokens)}"
            ),
        )
    if len(bass_tokens) != event_count:
        fail(
            "arpeggio_bass_timeline_mismatch",
            detail=(
                f"expected={event_count} bass events "
                f"actual={len(bass_tokens)}"
            ),
        )
    if percussion_tokens is None:
        fail("arpeggio_percussion_timeline_missing")
    cursor = 0
    for event_index, (chord, expected_count) in enumerate(
        zip(song.chords, expected_counts)
    ):
        pad_fallback = pad_fallbacks[event_index]
        token_count = (
            1
            if chord.is_no_chord or pad_fallback
            else expected_count
        )
        available = min(token_count, max(0, len(attack_tokens) - cursor))
        group = attack_tokens[cursor:cursor + available]
        cursor += available
        if len(group) != token_count:
            fail(
                "arpeggio_event_token_count_mismatch",
                event_index,
                detail=f"expected={token_count} actual={len(group)}",
            )
            continue
        if chord.is_no_chord:
            token = group[0]
            if (
                not token["is_rest"]
                or token["duration"] != chord.duration_token
                or token.get("onset_sixteenths") is None
                or not math.isclose(
                    float(token["onset_sixteenths"]),
                    event_starts[event_index],
                    rel_tol=0,
                    abs_tol=epsilon,
                )
            ):
                fail(
                    "arpeggio_no_chord_rest_mismatch",
                    event_index,
                    detail=f"expected @ {event_starts[event_index]} R{chord.duration_token}",
                )
            continue
        if pad_fallback:
            token = group[0]
            if (
                token["is_rest"]
                or token["duration"] != chord.duration_token
                or token.get("onset_sixteenths") is None
                or not math.isclose(
                    float(token["onset_sixteenths"]),
                    event_starts[event_index],
                    rel_tol=0,
                    abs_tol=epsilon,
                )
                or sorted(token["midis"]) != sorted(source_midis[event_index])
            ):
                fail(
                    "arpeggio_pad_fallback_token_mismatch",
                    event_index,
                    detail=(
                        f"expected @ {event_starts[event_index]} "
                        f"{chord.duration_token} "
                        f"{source_midis[event_index]}"
                    ),
                )
            continue

        subdivision = subdivisions[event_index]
        step = _ARPEGGIO_GRID_STEPS.get(subdivision)
        attacked_midis = []
        actual_pair_count = 0
        for slot, token in enumerate(group):
            onset = token.get("onset_sixteenths")
            allowed_widths = {1, 2} if sustain_to_event_end else {1}
            if token["is_rest"] or len(token["midis"]) not in allowed_widths:
                fail(
                    "arpeggio_token_shape",
                    event_index,
                    detail=(
                        "timed arpeggio attacks must contain "
                        f"{'one or two' if sustain_to_event_end else 'one'} notes"
                    ),
                )
                continue
            if len(token["midis"]) == 2:
                actual_pair_count += 1
            attacked_midis.extend(token["midis"])
            for pitch in token["midis"]:
                if pitch not in source_midis[event_index]:
                    fail(
                        "arpeggio_pitch_not_in_source_voicing",
                        event_index,
                        midi=[pitch],
                        detail=f"source={source_midis[event_index]}",
                    )
            if onset is None:
                fail(
                    "arpeggio_onset_missing",
                    event_index,
                )
                continue
            if onset < event_starts[event_index] or onset >= event_ends[event_index]:
                fail(
                    "arpeggio_onset_outside_event",
                    event_index,
                    detail=(
                        f"onset={onset} event="
                        f"[{event_starts[event_index]}, {event_ends[event_index]})"
                    ),
                )
            if slot == 0 and not math.isclose(
                float(onset), event_starts[event_index],
                rel_tol=0, abs_tol=epsilon,
            ):
                fail(
                    "arpeggio_first_onset_mismatch",
                    event_index,
                    detail=f"expected={event_starts[event_index]} actual={onset}",
                )
            if step is not None:
                expected_onset = event_starts[event_index] + slot * step
                if not math.isclose(
                    float(onset), expected_onset,
                    rel_tol=0, abs_tol=epsilon,
                ):
                    fail(
                        "arpeggio_onset_grid_mismatch",
                        event_index,
                        detail=f"expected={expected_onset} actual={onset}",
                    )
            duration_sixteenths = token.get("duration_sixteenths")
            if (
                isinstance(duration_sixteenths, bool)
                or not isinstance(duration_sixteenths, (int, float))
                or not math.isfinite(float(duration_sixteenths))
                or duration_sixteenths <= 0
            ):
                fail("arpeggio_note_duration_invalid", event_index)
                continue
            if token.get("duration") is not None:
                fail(
                    "arpeggio_timed_duration_missing",
                    event_index,
                )
            velocities = token.get("velocities", ())
            if (
                len(velocities) != len(token["midis"])
                or any(
                    isinstance(velocity, bool)
                    or not isinstance(velocity, int)
                    or not 1 <= velocity <= 127
                    for velocity in velocities
                )
            ):
                fail("arpeggio_velocity_invalid", event_index)
            note_off = float(onset) + float(duration_sixteenths)
            if note_off <= float(onset):
                fail("arpeggio_note_off_invalid", event_index)
            if note_off > event_ends[event_index] + epsilon:
                fail(
                    "arpeggio_note_off_beyond_event",
                    event_index,
                    detail=(
                        f"note_off={note_off} "
                        f"event_end={event_ends[event_index]}"
                    ),
                )
            if sustain_to_event_end:
                if (
                    float(event_ends[event_index]) - float(onset)
                    < _ARPEGGIO_MIN_SUSTAIN_SIXTEENTHS - epsilon
                ):
                    fail(
                        "arpeggio_sustain_too_short",
                        event_index,
                    )
                if not math.isclose(
                    note_off,
                    float(event_ends[event_index]),
                    rel_tol=0,
                    abs_tol=epsilon,
                ):
                    fail(
                        "arpeggio_note_not_sustained_to_event_end",
                        event_index,
                        detail=(
                            f"note_off={note_off} "
                            f"event_end={event_ends[event_index]}"
                        ),
                    )
            elif step is not None and duration_sixteenths > (
                step * float(gate_ratio) + epsilon
            ):
                fail("arpeggio_gate_invalid", event_index)

        if sustain_to_event_end:
            if sorted(attacked_midis) != sorted(source_midis[event_index]):
                fail(
                    "arpeggio_voicing_coverage_mismatch",
                    event_index,
                    detail=(
                        f"source={source_midis[event_index]} "
                        f"attacked={attacked_midis}"
                    ),
                )
            declared_pairs = pair_counts[event_index]
            if (
                declared_pairs is not None
                and declared_pairs != actual_pair_count
            ):
                fail(
                    "arpeggio_pair_count_mismatch",
                    event_index,
                    detail=(
                        f"expected={declared_pairs} "
                        f"actual={actual_pair_count}"
                    ),
                )

        supercycle = supercycle_lengths[event_index]
        if (
            isinstance(supercycle, int)
            and not isinstance(supercycle, bool)
            and supercycle > 0
            and len(group) >= supercycle
            and len(source_midis[event_index]) > 0
        ):
            covered = {
                pitch
                for token in group
                if not token["is_rest"]
                for pitch in token["midis"]
            }
            if not set(source_midis[event_index]) <= covered:
                fail(
                    "arpeggio_motif_coverage",
                    event_index,
                    detail=(
                        f"source={source_midis[event_index]} "
                        f"attacked={sorted(covered)}"
                    ),
                )

    if cursor != len(attack_tokens):
        fail(
            "arpeggio_extra_tokens",
            detail=f"unconsumed={len(attack_tokens) - cursor}",
        )

    for event_index, chord in enumerate(song.chords):
        if event_index >= len(bass_tokens):
            continue
        token = bass_tokens[event_index]
        if (
            token["is_rest"] != chord.is_no_chord
            or token["duration"] != chord.duration_token
            or (not chord.is_no_chord and len(token["midis"]) != 1)
        ):
            fail(
                "arpeggio_bass_timeline_mismatch",
                event_index,
                detail=f"expected duration={chord.duration_token!r}",
            )

    expected_timeline = float(absolute_position)
    v0_timeline = _track_timeline_sixteenths(chord_tokens, absolute=True)
    v1_timeline = _track_timeline_sixteenths(bass_tokens)
    if not math.isclose(v0_timeline, expected_timeline, rel_tol=0, abs_tol=epsilon):
        fail(
            "arpeggio_v0_timeline_mismatch",
            detail=f"expected={expected_timeline} actual={v0_timeline}",
        )
    if not math.isclose(v1_timeline, expected_timeline, rel_tol=0, abs_tol=epsilon):
        fail(
            "arpeggio_bass_timeline_mismatch",
            detail=f"expected={expected_timeline} actual={v1_timeline}",
        )
    if percussion_tokens is not None:
        v9_timeline = _track_timeline_sixteenths(percussion_tokens)
        if not math.isclose(
            v9_timeline, expected_timeline, rel_tol=0, abs_tol=epsilon
        ):
            fail(
                "arpeggio_percussion_timeline_mismatch",
                detail=f"expected={expected_timeline} actual={v9_timeline}",
            )
    return source_midis, failures


def _validate_legacy_arpeggio_track(
    chord_tokens: list[dict],
    bass_tokens: list[dict],
    song: Song,
    record: dict,
    issues: list[dict],
) -> tuple[list[list[int]], int]:
    """Validate arpeggio expansion and return its source voicings."""
    failures = 0

    def fail(
        category: str,
        event_index: int | None = None,
        detail: str | None = None,
        midi: list[int] | None = None,
    ) -> None:
        nonlocal failures
        _issue(
            issues, category, record, event_index,
            midi=midi, detail=detail,
        )
        failures += 1

    info = record.get("arpeggio")
    if not isinstance(info, dict):
        fail("arpeggio_manifest_missing")
        info = {}
    if info.get("subdivision") != "sixteenth":
        fail(
            "arpeggio_subdivision_invalid",
            detail=f"expected='sixteenth' actual={info.get('subdivision')!r}",
        )

    raw_counts = info.get("event_slot_counts")
    counts_valid = (
        isinstance(raw_counts, list)
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            and value >= 0
            for value in raw_counts
        )
    )
    if not counts_valid:
        fail("arpeggio_slot_counts_invalid")
        raw_counts = []

    raw_source = info.get("source_voicing_midis")
    source_shape_valid = (
        isinstance(raw_source, list)
        and len(raw_source) == len(song.chords)
    )
    if not source_shape_valid:
        fail(
            "arpeggio_source_voicing_count_mismatch",
            detail=f"expected {len(song.chords)} source voicing lists",
        )
    source_midis = [[] for _ in song.chords]
    if isinstance(raw_source, list):
        for event_index, value in enumerate(raw_source[:len(song.chords)]):
            if (
                not isinstance(value, list)
                or any(
                    not isinstance(pitch, int) or isinstance(pitch, bool)
                    for pitch in value
                )
            ):
                fail(
                    "arpeggio_source_voicing_invalid",
                    event_index,
                )
                continue
            source_midis[event_index] = list(value)

    expected_counts = []
    for event_index, chord in enumerate(song.chords):
        try:
            slots = _arpeggio_slot_count(chord.duration_token)
        except ValueError as error:
            fail(
                "arpeggio_source_duration_invalid",
                event_index,
                detail=str(error),
            )
            slots = 0
        expected_counts.append(0 if chord.is_no_chord else slots)
        if chord.is_no_chord and source_midis[event_index]:
            fail(
                "arpeggio_no_chord_source_voicing",
                event_index,
                midi=source_midis[event_index],
            )
        elif not chord.is_no_chord and not source_midis[event_index]:
            fail("arpeggio_source_voicing_empty", event_index)

    if counts_valid and raw_counts != expected_counts:
        fail(
            "arpeggio_slot_count_mismatch",
            detail=f"expected={expected_counts} actual={raw_counts}",
        )

    expected_token_count = sum(
        1 if chord.is_no_chord else count
        for chord, count in zip(song.chords, expected_counts)
    )
    if len(chord_tokens) != expected_token_count:
        fail(
            "arpeggio_token_count_mismatch",
            detail=(
                f"expected={expected_token_count} "
                f"actual={len(chord_tokens)}"
            ),
        )
    if len(bass_tokens) != len(song.chords):
        fail(
            "arpeggio_bass_timeline_mismatch",
            detail=(
                f"expected={len(song.chords)} bass events "
                f"actual={len(bass_tokens)}"
            ),
        )

    cursor = 0
    for event_index, (chord, slot_count) in enumerate(
        zip(song.chords, expected_counts)
    ):
        token_count = 1 if chord.is_no_chord else slot_count
        available = min(token_count, max(0, len(chord_tokens) - cursor))
        group = chord_tokens[cursor:cursor + available]
        cursor += available
        if len(group) != token_count:
            fail(
                "arpeggio_event_token_count_mismatch",
                event_index,
                detail=f"expected={token_count} actual={len(group)}",
            )
            continue
        if chord.is_no_chord:
            token = group[0]
            if (
                not token["is_rest"]
                or token["duration"] != chord.duration_token
            ):
                fail(
                    "arpeggio_no_chord_rest_mismatch",
                    event_index,
                    detail=f"expected R{chord.duration_token}",
                )
            continue
        for token in group:
            if (
                token["is_rest"]
                or token["duration"] != "s"
                or len(token["midis"]) != 1
            ):
                fail(
                    "arpeggio_token_shape",
                    event_index,
                    detail="playable arpeggio slots must be one sixteenth note",
                )
                continue
            pitch = token["midis"][0]
            if pitch not in source_midis[event_index]:
                fail(
                    "arpeggio_pitch_not_in_source_voicing",
                    event_index,
                    midi=[pitch],
                    detail=(
                        f"source={source_midis[event_index]}"
                    ),
                )
    if cursor != len(chord_tokens):
        fail(
            "arpeggio_extra_tokens",
            detail=f"unconsumed={len(chord_tokens) - cursor}",
        )
    for event_index, chord in enumerate(song.chords):
        if event_index >= len(bass_tokens):
            continue
        token = bass_tokens[event_index]
        expected_duration = chord.duration_token
        if (
            token["is_rest"] != chord.is_no_chord
            or token["duration"] != expected_duration
            or (not chord.is_no_chord and len(token["midis"]) != 1)
        ):
            fail(
                "arpeggio_bass_timeline_mismatch",
                event_index,
                detail=f"expected duration={expected_duration!r}",
            )
    return source_midis, failures


def _validate_arpeggio_track(
    chord_tokens: list[dict],
    bass_tokens: list[dict],
    song: Song,
    record: dict,
    issues: list[dict],
    percussion_tokens: list[dict] | None = None,
) -> tuple[list[list[int]], int]:
    """Validate either the spec 12 or spec 13 arpeggio manifest shape."""
    info = record.get("arpeggio")
    if isinstance(info, dict) and (
        "event_onset_counts" in info
        or "performance_profile" in info
    ):
        return _validate_timed_arpeggio_track(
            chord_tokens,
            bass_tokens,
            percussion_tokens,
            song,
            record,
            issues,
        )
    return _validate_legacy_arpeggio_track(
        chord_tokens, bass_tokens, song, record, issues
    )


def _label_roots(
    labels_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
) -> tuple[Path, ...]:
    if isinstance(labels_dir, (str, Path)):
        labels_dir = [labels_dir]
    roots = tuple(Path(directory).resolve() for directory in labels_dir)
    if not roots:
        raise ValueError("at least one labels directory is required")
    if len(set(roots)) != len(roots):
        raise ValueError("labels directories must be unique")
    return roots


def validate_corpus(
    labels_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
    score_path: str | Path,
    manifest_path: str | Path | None = None,
    expected_genre: str | None = None,
) -> dict:
    """Validate one rendered corpus and return a JSON-serializable report."""
    labels_roots = _label_roots(labels_dir)
    score_path = Path(score_path).resolve()
    manifest_path = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else score_path.with_name(score_path.name + ".manifest.json")
    )
    manifest = _load_manifest(manifest_path)
    records = manifest["records"]
    blocks = parse_score_blocks(score_path)
    issues: list[dict] = []
    metrics = {
        "genre": defaultdict(_new_bucket),
        "voicer_family": defaultdict(_new_bucket),
        "chord_type": defaultdict(_new_bucket),
        "tonic": defaultdict(_new_bucket),
        "relaxation_level": defaultdict(_new_bucket),
    }
    report = {
        "labels_dir": (
            str(labels_roots[0])
            if len(labels_roots) == 1
            else [str(root) for root in labels_roots]
        ),
        "score": str(score_path),
        "manifest": str(manifest_path),
        "records": len(records),
        "score_blocks": len(blocks),
        "events": 0,
        "issues": issues,
        "metrics": metrics,
        "pairing_errors": 0,
        "hard_failure_count": 0,
        "extension_drop_rates": {},
        "no_chord_events": 0,
        "no_chord_duration_seconds": 0.0,
        "duration_seconds": 0.0,
    }
    if "track_outputs" in manifest:
        declared_paths = (
            manifest.get("track_outputs", {}).get("paths", {})
            if isinstance(manifest.get("track_outputs"), dict)
            else {}
        )

        def declared_or_derived(role: str) -> Path:
            value = declared_paths.get(role)
            if isinstance(value, str):
                return Path(value)
            if role == "mixed":
                return score_path
            return score_path.with_name(
                f"{score_path.stem}_{role}{score_path.suffix}"
            )

        multitrack = validate_multitrack_outputs(
            score_path,
            declared_or_derived("chords"),
            declared_or_derived("bass"),
            declared_or_derived("percussion"),
            manifest_path,
        )
        report["multitrack"] = multitrack
        report["issues"].extend(multitrack["issues"])
        report["hard_failure_count"] += multitrack["hard_failure_count"]
    extension_counts = defaultdict(lambda: {
        "events": 0,
        "extension_events": 0,
        "drop_events": 0,
        "drop_roles": 0,
    })

    seen_ordinals = set()
    if len(records) != len(blocks):
        _issue(
            issues, "manifest_score_block_count", {},
            detail=f"manifest={len(records)} score_blocks={len(blocks)}",
        )
    for record in records:
        ordinal = record.get("ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal in seen_ordinals:
            _issue(issues, "duplicate_or_invalid_manifest_ordinal", record)
            report["pairing_errors"] += 1
        seen_ordinals.add(ordinal)

    for expected_ordinal, record in enumerate(records):
        ordinal = record.get("ordinal")
        if ordinal != expected_ordinal:
            _issue(
                issues, "manifest_ordinal_order", record,
                detail=f"expected {expected_ordinal}",
            )
            report["pairing_errors"] += 1
        if ordinal not in blocks:
            _issue(issues, "missing_score_block", record)
            report["pairing_errors"] += 1
            continue

        source_name = record.get("source_file")
        source_path = None
        if isinstance(source_name, str):
            record_source_dir = record.get("source_dir")
            if isinstance(record_source_dir, str):
                record_root = Path(record_source_dir).resolve()
                if record_root in labels_roots:
                    source_path = record_root / source_name
            elif len(labels_roots) == 1:
                source_path = labels_roots[0] / source_name
            else:
                candidates = [
                    root / source_name
                    for root in labels_roots
                    if (root / source_name).is_file()
                ]
                if len(candidates) == 1:
                    source_path = candidates[0]
        if source_path is None or source_path.name != source_name or \
                source_path.parent.resolve() not in labels_roots:
            _issue(issues, "invalid_manifest_source_path", record)
            report["pairing_errors"] += 1
            continue
        if not source_path.is_file():
            _issue(issues, "missing_source_file", record)
            report["pairing_errors"] += 1
            continue
        raw = source_path.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        if source_hash != record.get("source_sha256"):
            _issue(
                issues, "source_hash_mismatch", record,
                detail=f"manifest={record.get('source_sha256')} actual={source_hash}",
            )
            report["pairing_errors"] += 1
        source_match = _SOURCE_NAME.fullmatch(source_name)
        source_id = int(source_match.group(1)) if source_match else None
        if source_id != record.get("source_id"):
            _issue(
                issues, "source_id_mismatch", record,
                detail=f"filename_id={source_id} manifest_id={record.get('source_id')}",
            )
            report["pairing_errors"] += 1

        progression = json.loads(raw.decode("utf-8"))
        report["duration_seconds"] += sum(
            float(event.get("duration_seconds", 0.0))
            for event in progression.get("chords", [])
        )
        song = Song.from_dict(progression)
        actual_genre = progression.get("genre")
        family = record.get("voicer_family") or "unknown"
        if expected_genre is not None and actual_genre != expected_genre:
            _issue(
                issues, "genre_mismatch", record,
                detail=f"expected={expected_genre} actual={actual_genre}",
            )
        for field in ("genre", "tonic_pc", "bpm", "num_chords"):
            expected = len(song.chords) if field == "num_chords" and field not in progression \
                else progression.get(field, 120 if field == "bpm" else None)
            if record.get(field) != expected:
                _issue(
                    issues, "manifest_metadata_mismatch", record,
                    detail=f"{field}: manifest={record.get(field)!r} source={expected!r}",
                )
                report["pairing_errors"] += 1
        if record.get("num_chords") != len(song.chords):
            _issue(
                issues, "label_event_count_mismatch", record,
                detail=f"metadata={record.get('num_chords')} actual={len(song.chords)}",
            )
            report["pairing_errors"] += 1

        render_mode = record.get("render_mode", "pads")
        if render_mode not in {"pads", "arpeggios"}:
            _issue(
                issues, "invalid_render_mode", record,
                detail=f"render_mode={render_mode!r}",
            )
            report["hard_failure_count"] += 1
            render_mode = "pads"

        try:
            voice_details = parse_score_line_voice_details(blocks[ordinal])
            chord_token_details = voice_details["V0"]
            bass_token_details = voice_details["V1"]
            percussion_token_details = voice_details.get("V9")
        except ValueError as error:
            _issue(issues, "malformed_score_block", record, detail=str(error))
            report["pairing_errors"] += 1
            continue
        chord_events = [event["midis"] for event in chord_token_details]
        bass_events = [event["midis"] for event in bass_token_details]
        if render_mode == "arpeggios":
            source_voicing_midis, arpeggio_failures = _validate_arpeggio_track(
                chord_token_details,
                bass_token_details,
                song,
                record,
                issues,
                percussion_token_details,
            )
            report["hard_failure_count"] += arpeggio_failures
            if len(bass_events) != len(song.chords):
                _issue(
                    issues, "score_event_count_mismatch", record,
                    detail=(
                        f"labels={len(song.chords)} chord_track=arpeggio "
                        f"bass_track={len(bass_events)}"
                    ),
                )
                report["pairing_errors"] += 1
        else:
            source_voicing_midis = chord_events
            if (
                len(chord_events) != len(song.chords)
                or len(bass_events) != len(song.chords)
            ):
                _issue(
                    issues, "score_event_count_mismatch", record,
                    detail=(
                        f"labels={len(song.chords)} "
                        f"chord_track={len(chord_events)} "
                        f"bass_track={len(bass_events)}"
                    ),
                )
                report["pairing_errors"] += 1

        summary = record.get("voicing_summary") or {}
        for level, count in (summary.get("relaxation_levels") or {}).items():
            metrics["relaxation_level"][str(level)]["events"] += int(count)

        for event_index, chord in enumerate(song.chords):
            if (
                event_index >= len(source_voicing_midis)
                or event_index >= len(bass_events)
            ):
                continue
            midi = source_voicing_midis[event_index]
            bass_midi = bass_events[event_index]
            label = _label_dict(chord)
            if chord.is_no_chord:
                report["events"] += 1
                report["no_chord_events"] += 1
                report["no_chord_duration_seconds"] += float(
                    progression["chords"][event_index].get("duration_seconds", 0.0)
                )
                if midi:
                    _issue(issues, "no_chord_chord_notes", record, event_index, label, midi)
                    report["hard_failure_count"] += 1
                if bass_midi:
                    _issue(issues, "no_chord_bass_notes", record, event_index, label, bass_midi)
                    report["hard_failure_count"] += 1
                continue
            root_pc = (song.tonic_pc + chord.root_interval) % 12
            bass_pc = (root_pc + chord.bass_interval) % 12
            degrees = resolve_degrees(chord)
            degree_by_role = {degree.role: degree for degree in degrees}
            active_pcs = {
                (root_pc + degree.semitone) % 12 for degree in degrees
            }
            chord_pcs = {pitch % 12 for pitch in midi}
            failures: list[str] = []

            if midi != sorted(midi):
                failures.append("unsorted_midi")
                _issue(issues, "unsorted_midi", record, event_index, label, midi)
            if len(midi) != len(set(midi)):
                failures.append("duplicate_midi")
                _issue(issues, "duplicate_midi", record, event_index, label, midi)
            if any(pitch % 12 not in active_pcs for pitch in midi):
                failures.append("unrequested_pitch_class")
                _issue(
                    issues, "unrequested_pitch_class", record, event_index,
                    label, midi, f"active={sorted(active_pcs)}",
                )
            if len(midi) > 1:
                from voicing.spacing import low_interval_limit_ok
                if not low_interval_limit_ok(midi):
                    failures.append("low_interval_limit")
                    _issue(issues, "low_interval_limit", record, event_index, label, midi)

            if len(bass_midi) != 1:
                failures.append("bass_event_shape")
                _issue(
                    issues, "bass_event_shape", record, event_index,
                    label, bass_midi,
                )
                actual_bass_pc = None
            else:
                actual_bass_pc = bass_midi[0] % 12
                if actual_bass_pc != bass_pc:
                    failures.append("bass_pitch_class")
                    _issue(
                        issues, "bass_pitch_class", record, event_index,
                        label, bass_midi, f"expected_pc={bass_pc}",
                    )

            root_retained = root_pc in chord_pcs
            rootless_allowed = (
                actual_genre == "jazz"
                and chord.bass_interval == 0
                and actual_bass_pc == root_pc
            )
            if not root_retained:
                if actual_genre == "jazz" and not rootless_allowed:
                    failures.append("root_omission_gate")
                    _issue(
                        issues, "root_omission_gate", record, event_index,
                        label, midi,
                        "jazz root omission requires an active root bass and root-position label",
                    )

            missing_roles = []
            missing_extension_roles = []
            permitted_omissions = 0
            dct_role, _secondary = compute_dct(chord, degrees)
            extension_roles = {
                SLOT_TO_ROLE[slot] for slot in EXT_SLOTS
                if getattr(chord, slot) != "N" and SLOT_TO_ROLE[slot] in degree_by_role
            }
            for degree in degrees:
                degree_pc = (root_pc + degree.semitone) % 12
                if degree_pc in chord_pcs:
                    continue
                role = degree.role
                if role == "root" and (rootless_allowed or actual_genre != "jazz"):
                    permitted_omissions += 1
                elif role == "5th" and chord.triad in {"major", "minor"}:
                    permitted_omissions += 1
                else:
                    if role in extension_roles:
                        missing_extension_roles.append(role)
                    if role in extension_roles and family == "guitar" and role != \
                            dct_role:
                        permitted_omissions += 1
                        continue
                    missing_roles.append(role)

            dct_pc = None
            if dct_role is not None:
                dct_degree = degree_by_role.get(dct_role)
                if dct_degree is None:
                    failures.append("dct_role_unresolved")
                    _issue(
                        issues, "dct_role_unresolved", record, event_index,
                        label, midi, f"role={dct_role}",
                    )
                    dct_pitches = []
                else:
                    dct_pc = (root_pc + dct_degree.semitone) % 12
                    dct_pitches = [pitch for pitch in midi if pitch % 12 == dct_pc]
                if not dct_pitches:
                    failures.append("dct_missing")
                    _issue(
                        issues, "dct_missing", record, event_index,
                        label, midi, f"role={dct_role} pc={dct_pc}",
                    )
                elif not any(
                    predicate_top(pitch, midi)
                    or predicate_isolated(pitch, midi)
                    or predicate_octave(pitch, midi)
                    for pitch in dct_pitches
                ):
                    failures.append("dct_not_exposed")
                    _issue(
                        issues, "dct_not_exposed", record, event_index,
                        label, midi, f"role={dct_role} pc={dct_pc}",
                    )

            if missing_roles:
                failures.append("missing_active_degree")
                _issue(
                    issues, "missing_active_degree", record, event_index,
                    label, midi, f"roles={missing_roles}",
                )
            extension_seen = not missing_extension_roles
            combined_pcs = set(chord_pcs)
            if actual_bass_pc is not None:
                combined_pcs.add(actual_bass_pc)
            combined_missing_roles = []
            for degree in degrees:
                degree_pc = (root_pc + degree.semitone) % 12
                if degree_pc in combined_pcs:
                    continue
                role = degree.role
                if role == "root" and rootless_allowed:
                    continue
                if role == "5th" and chord.triad in {"major", "minor"}:
                    continue
                if role in extension_roles and family == "guitar" and role != dct_role:
                    continue
                combined_missing_roles.append(role)
            if combined_missing_roles:
                failures.append("combined_missing_active_degree")
                _issue(
                    issues, "combined_missing_active_degree", record,
                    event_index, label, midi,
                    f"roles={combined_missing_roles}",
                )
            if root_pc not in combined_pcs:
                failures.append("combined_root_missing")
                _issue(
                    issues, "combined_root_missing", record,
                    event_index, label, midi,
                    f"expected_pc={root_pc}",
                )

            report["events"] += 1
            type_key = _type_key(chord.chord_type())
            dimension_keys = {
                "genre": str(actual_genre),
                "voicer_family": str(family),
                "chord_type": type_key,
                "tonic": str(song.tonic_pc),
            }
            for dimension, key in dimension_keys.items():
                bucket = metrics[dimension][key]
                bucket["events"] += 1
                bucket["root_retained" if root_retained else "root_omitted"] += 1
                if dct_role is not None:
                    bucket["dct_events"] += 1
                    if "dct_missing" not in failures and "dct_not_exposed" not in failures:
                        bucket["dct_exposed"] += 1
                if extension_roles:
                    bucket["extension_events"] += 1
                    if extension_seen:
                        bucket["extension_retained"] += 1
                    if missing_extension_roles:
                        bucket["extension_drop_events"] += 1
                        bucket["extension_drop_roles"] += len(missing_extension_roles)
                bucket["permitted_omissions"] += permitted_omissions
                if actual_bass_pc == bass_pc:
                    bucket["bass_correct"] += 1
                if root_pc in combined_pcs:
                    bucket["combined_root_present"] += 1
                covered = len(degrees) - len(combined_missing_roles)
                bucket["combined_active_degree_coverage"] += covered
                bucket["combined_missing_active_degrees"] += len(
                    combined_missing_roles
                )
                bucket["unrequested_pitch_classes"] += sum(
                    1 for pitch in chord_pcs if pitch not in active_pcs
                )
                bucket["violations"] += len(failures)
            pair = extension_counts[(str(actual_genre), str(family))]
            pair["events"] += 1
            if extension_roles:
                pair["extension_events"] += 1
                if missing_extension_roles:
                    pair["drop_events"] += 1
                    pair["drop_roles"] += len(missing_extension_roles)
            if failures:
                report["hard_failure_count"] += len(failures)

    for (genre, family), counts in sorted(extension_counts.items()):
        limit = 0.02 if genre == "pop_rock" and family == "guitar" else \
            0.03 if genre == "jazz" and family == "guitar" else None
        denominator = counts["events"]
        rate = counts["drop_events"] / denominator if denominator else 0.0
        key = f"{genre}:{family}"
        report["extension_drop_rates"][key] = {
            **counts,
            "rate": rate,
            "limit": limit,
        }
        if limit is not None and rate >= limit:
            _issue(
                issues, "extension_drop_budget", {},
                detail=f"{key} rate={rate:.6f} limit<{limit:.6f}",
            )

    report["metrics"] = {
        dimension: dict(values) for dimension, values in metrics.items()
    }
    report["no_chord_event_rate"] = (
        report["no_chord_events"] / report["events"] if report["events"] else 0.0
    )
    report["no_chord_duration_rate"] = (
        report["no_chord_duration_seconds"] / report["duration_seconds"]
        if report["duration_seconds"] else 0.0
    )
    return report


def _default_corpora(root: Path) -> tuple[tuple[str, Path, Path], ...]:
    return (
        ("jazz", root / "gen" / "target-jazz-labels", root / "gen" / "jazz_scores.txt"),
        (
            "pop_rock",
            root / "gen" / "target-pop-rock-labels",
            root / "gen" / "pop_rock_scores.txt",
        ),
    )


def _print_report(report: dict) -> None:
    print(
        f"{report['score']}: {report['records']} blocks, "
        f"{report['events']} events, {report['hard_failure_count']} hard failures"
    )
    for issue in report["issues"]:
        label = json.dumps(issue.get("label"), sort_keys=True, separators=(",", ":"))
        print(
            f"ERROR {issue['category']}: source={issue.get('source_file')} "
            f"ordinal={issue.get('ordinal')} event={issue.get('event_index')} "
            f"label={label} rendered_midi={issue.get('rendered_midi')} "
            f"{issue.get('detail') or ''}".rstrip()
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--labels-dir", nargs="+")
    parser.add_argument("--scores")
    parser.add_argument("--manifest")
    parser.add_argument("--genre", choices=("jazz", "pop_rock"))
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    explicit = any(value is not None for value in (args.labels_dir, args.scores, args.manifest))
    if explicit:
        if args.labels_dir is None or args.scores is None:
            parser.error("--labels-dir and --scores are required together")
        corpora = [(
            args.genre,
            [Path(labels_dir) for labels_dir in args.labels_dir],
            Path(args.scores),
        )]
        manifest_by_corpus = [Path(args.manifest) if args.manifest else None]
    else:
        defaults = _default_corpora(root)
        corpora = [(genre, labels, scores) for genre, labels, scores in defaults]
        manifest_by_corpus = [None] * len(corpora)

    reports = []
    try:
        for (genre, labels, scores), manifest in zip(corpora, manifest_by_corpus):
            reports.append(validate_corpus(labels, scores, manifest, genre))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR validator setup: {error}", file=sys.stderr)
        return 2

    for report in reports:
        _print_report(report)
    if args.json_out:
        output = {
            "reports": reports,
            "hard_failure_count": sum(report["hard_failure_count"] for report in reports),
        }
        Path(args.json_out).write_text(
            json.dumps(output, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 1 if any(report["issues"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
