"""Render generated progression JSON files as synchronized JFugue score text."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bass_module import BassModule
from chord_module import ChordModule, POLICIES
from instruments import ARPEGGIO_PROFILES
from percussion_module import (
    PERCUSSION_OMISSION_PROBABILITY,
    PercussionModule,
)


_SONG_FILENAME = re.compile(r"^song_(\d+)\.json$")
_RENDER_MODES = ("pads", "arpeggios")
_SOURCE_GENRES = ("jazz", "pop_rock")
_VOICER_FAMILIES = ("piano", "guitar", "synth")
_VOICER_ID_BY_GENRE_FAMILY = {
    (genre, family): policy.voicer_id
    for (genre, family), policy in POLICIES.items()
}
_VOICER_FAMILY_BY_ID = {
    voicer: family
    for (genre, family), voicer in _VOICER_ID_BY_GENRE_FAMILY.items()
}
_VOICER_IDS = tuple(
    _VOICER_ID_BY_GENRE_FAMILY[(genre, family)]
    for genre in _SOURCE_GENRES
    for family in _VOICER_FAMILIES
)
_VOICER_ORDER = {
    voicer: index for index, voicer in enumerate(_VOICER_IDS)
}
_DEFAULT_PERCUSSION_PERCENT = (
    100.0 * (1.0 - PERCUSSION_OMISSION_PROBABILITY)
)


@dataclass(frozen=True)
class RenderedSongTracks:
    """The retained role strings for one rendered source song."""

    ordinal: int
    chord_track: str
    bass_track: str
    percussion_track: str
    mixed_line: str


def _input_dir_list(
    input_dirs: str | Path | list[str] | tuple[str, ...],
) -> list[str]:
    if isinstance(input_dirs, (str, Path)):
        input_dirs = [str(input_dirs)]
    else:
        input_dirs = [str(input_dir) for input_dir in input_dirs]
    if not input_dirs:
        raise ValueError("at least one input directory is required")
    resolved = [str(Path(input_dir).resolve()) for input_dir in input_dirs]
    if len(set(resolved)) != len(resolved):
        raise ValueError("input directories must be unique")
    return input_dirs


def _source_files(
    input_dir: str | Path | list[str] | tuple[str, ...],
) -> list[tuple[int, Path, bytes, dict, Path]]:
    records = []
    for input_dir in _input_dir_list(input_dir):
        input_path = Path(input_dir)
        candidates = [
            path for path in input_path.glob("song_*.json")
            if path.is_file()
        ]
        if not candidates:
            raise FileNotFoundError(
                f"No song_*.json files found in {input_dir}"
            )

        seen_ids = {}
        directory_records = []
        for path in candidates:
            match = _SONG_FILENAME.fullmatch(path.name)
            if match is None:
                raise ValueError(
                    f"Malformed song filename {path.name!r}; "
                    "expected song_<integer>.json"
                )
            source_id = int(match.group(1))
            if source_id in seen_ids:
                raise ValueError(
                    f"Duplicate numeric song id {source_id}: "
                    f"{seen_ids[source_id].name} and {path.name}"
                )
            seen_ids[source_id] = path
            raw = path.read_bytes()
            progression = json.loads(raw.decode("utf-8"))
            if not isinstance(progression, dict):
                raise ValueError(f"{path} must contain a JSON object")
            directory_records.append((
                source_id, path, raw, progression, input_path,
            ))
        records.extend(sorted(
            directory_records, key=lambda item: item[0]
        ))
    return records


def _generator_revision() -> tuple[str, bool]:
    repo_root = Path(__file__).resolve().parent
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            [
                "git", "status", "--porcelain", "--untracked-files=normal",
                "--", "chord_module.py", "instruments.py", "render.py",
                "voicing", "eda", "percussion_module.py",
                "HumanizedMidiRenderer.java",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() != ""
        return revision, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", False


def _manifest_path(output: str | Path) -> Path:
    output_path = Path(output)
    return output_path.with_name(output_path.name + ".manifest.json")


def _track_output_paths(output: str | Path) -> dict[str, Path]:
    """Derive and validate the complete score output set."""
    output_path = Path(output)
    paths = {
        "mixed": output_path,
        "chords": output_path.with_name(
            f"{output_path.stem}_chords{output_path.suffix}"
        ),
        "bass": output_path.with_name(
            f"{output_path.stem}_bass{output_path.suffix}"
        ),
        "percussion": output_path.with_name(
            f"{output_path.stem}_percussion{output_path.suffix}"
        ),
        "manifest": _manifest_path(output_path),
    }
    resolved = [path.resolve() for path in paths.values()]
    if len(set(resolved)) != len(resolved):
        raise ValueError("derived render output paths must be unique")
    return paths


def _percentage(value: float | int, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 100
    ):
        raise ValueError(f"{name} must be a finite percentage between 0 and 100")
    return float(value)


def _percussion_inclusion_percent(value: float | int | None) -> float:
    if value is None:
        return _DEFAULT_PERCUSSION_PERCENT
    return _percentage(value, "percussion_percent")


def _render_mode_plan(
    mode: str,
    file_count: int,
    seed: int,
    arpeggio_percent: float | None = None,
    pad_percent: float | None = None,
) -> tuple[list[str], str, dict[str, float]]:
    """Return deterministic per-song modes and the requested split."""
    if mode not in (*_RENDER_MODES, "mixed"):
        raise ValueError(
            "mode must be 'pads', 'arpeggios', or 'mixed'"
        )
    if (
        isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or file_count < 0
    ):
        raise ValueError("file_count must be a non-negative integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    percentages_requested = (
        arpeggio_percent is not None or pad_percent is not None
    )
    if not percentages_requested:
        if mode == "mixed":
            raise ValueError(
                "mixed mode requires --arpeggio-percent or --pad-percent"
            )
        percentages = {
            "arpeggios": 100.0 if mode == "arpeggios" else 0.0,
            "pads": 100.0 if mode == "pads" else 0.0,
        }
        return [mode] * file_count, mode, percentages

    if mode == "arpeggios":
        raise ValueError(
            "percentage options cannot be combined with --mode arpeggios; "
            "use --mode mixed"
        )
    arpeggios = (
        _percentage(arpeggio_percent, "arpeggio_percent")
        if arpeggio_percent is not None else None
    )
    pads = (
        _percentage(pad_percent, "pad_percent")
        if pad_percent is not None else None
    )
    if arpeggios is None:
        arpeggios = 100.0 - pads
    if pads is None:
        pads = 100.0 - arpeggios
    if not math.isclose(arpeggios + pads, 100.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError("arpeggio_percent and pad_percent must sum to 100")
    pads = 100.0 - arpeggios

    arpeggio_count = min(
        file_count,
        max(0, math.floor(file_count * arpeggios / 100.0 + 0.5)),
    )
    arpeggio_indexes = set(
        random.Random(seed).sample(range(file_count), arpeggio_count)
    )
    modes = [
        "arpeggios" if index in arpeggio_indexes else "pads"
        for index in range(file_count)
    ]
    return modes, "mixed", {
        "arpeggios": arpeggios,
        "pads": pads,
    }


def _render_command(
    input_dirs: list[str],
    output: str,
    seed: int,
    mode: str,
    render_mode_percentages: dict[str, float] | None = None,
    percussion_percent: float = _DEFAULT_PERCUSSION_PERCENT,
) -> str:
    percussion_percent = _percussion_inclusion_percent(percussion_percent)
    command = [
        sys.executable, "render.py", "--in-dir", *input_dirs,
        "--out", output, "--mode", mode, "--seed", str(seed),
        "--percussion-percent", f"{percussion_percent:g}",
    ]
    if mode == "mixed":
        if render_mode_percentages is None:
            raise ValueError("mixed render commands require percentage targets")
        command.extend([
            "--arpeggio-percent",
            f"{render_mode_percentages['arpeggios']:g}",
            "--pad-percent",
            f"{render_mode_percentages['pads']:g}",
        ])
    return shlex.join(command)


def _voicing_summary(chord_module: ChordModule) -> dict:
    diagnostics = chord_module.last_voicing_diagnostics
    levels = Counter(str(item.get("level", "unknown")) for item in diagnostics)
    omission = Counter(
        item.get("root_omission", "unknown") for item in diagnostics
    )
    dct_exposure = Counter(
        item.get("dct_exposure") or "none" for item in diagnostics
    )
    omitted_roles = Counter()
    extension_drops = 0
    extension_drop_records = []
    for event_index, item in enumerate(diagnostics):
        omitted_roles.update(item.get("omitted_roles") or ())
        dropped_roles = item.get("extensions_dropped") or ()
        extension_drops += len(dropped_roles)
        for role in dropped_roles:
            extension_drop_records.append({
                "event_index": event_index,
                "chord_type": item.get("chord_type"),
                "shape_id": item.get("shape_id"),
                "role": role,
            })
    return {
        "relaxation_levels": dict(sorted(levels.items())),
        "root_omission": dict(sorted(omission.items())),
        "dct_exposure": dict(sorted(dct_exposure.items())),
        "omitted_roles": dict(sorted(omitted_roles.items())),
        "extension_drop_count": extension_drops,
        "extension_drops": extension_drop_records,
    }


def _arpeggio_manifest(chord_module: ChordModule) -> dict:
    diagnostics = chord_module.last_arpeggio_diagnostics
    profile_id = chord_module.last_arpeggio_profile
    if profile_id not in ARPEGGIO_PROFILES:
        raise ValueError(
            f"Missing arpeggio profile for rendered chord module: {profile_id!r}"
        )
    profile = ARPEGGIO_PROFILES[profile_id]
    result = {
        "performance_profile": profile_id,
        "meter": "4/4",
        "event_subdivisions": [
            item.get("subdivision") for item in diagnostics
        ],
        "event_onset_counts": [
            int(item.get("onset_count", 0)) for item in diagnostics
        ],
        "event_attack_counts": [
            int(item.get("attack_count", 0)) for item in diagnostics
        ],
        "event_pair_counts": [
            int(item.get("pair_count", 0)) for item in diagnostics
        ],
        "event_pad_fallbacks": [
            item.get("pad_fallback") is True for item in diagnostics
        ],
        "event_pattern_families": [
            item.get("pattern_family") for item in diagnostics
        ],
        "event_motif_families": [
            item.get("motif_family") for item in diagnostics
        ],
        "event_start_phases": [
            item.get("start_phase") for item in diagnostics
        ],
        "sustain_to_event_end": True,
        "gate_ratio": profile.gate_ratio,
        "eighth_probability": profile.eighth_probability,
        "pair_probability": profile.pair_probability,
        "source_voicing_midis": [
            list(midis) for midis in chord_module.last_voiced_midis
        ],
        "source_sounding_strings": [
            list(item.get("source_sounding_strings") or ())
            for item in diagnostics
        ],
        "event_supercycle_lengths": [
            item.get("supercycle_length") for item in diagnostics
        ],
        "event_source_index_cycles": [
            item.get("source_index_cycle") for item in diagnostics
        ],
    }
    return result


def _no_chord_summary(progression: dict) -> dict:
    events = progression.get("chords", ())
    no_chord_events = [
        event for event in events
        if event.get("is_no_chord", event.get("harte") == "N")
    ]
    total_duration = sum(float(event.get("duration_seconds", 0.0)) for event in events)
    no_chord_duration = sum(
        float(event.get("duration_seconds", 0.0)) for event in no_chord_events
    )
    return {
        "event_count": len(no_chord_events),
        "duration_seconds": no_chord_duration,
        "event_rate": len(no_chord_events) / len(events) if events else 0.0,
        "duration_rate": no_chord_duration / total_duration if total_duration else 0.0,
    }


def _least_used_voicer(voicers: list[str], counts: Counter) -> str:
    return min(
        voicers,
        key=lambda voicer: (counts[voicer], _VOICER_ORDER[voicer]),
    )


def _voicers_for_genre(genre: str) -> list[str]:
    try:
        return [
            _VOICER_ID_BY_GENRE_FAMILY[(genre, family)]
            for family in _VOICER_FAMILIES
        ]
    except KeyError:
        raise ValueError(
            f"Unsupported source genre {genre!r}; "
            f"expected one of {_SOURCE_GENRES}"
        ) from None


def _voicer_family(voicer: str) -> str:
    try:
        return _VOICER_FAMILY_BY_ID[voicer]
    except KeyError:
        raise ValueError(f"Unknown voicer {voicer!r}") from None


def _preferred_family(progression: dict) -> str | None:
    hint = progression.get("voicer_family")
    if not isinstance(hint, str):
        return None
    if hint in _VOICER_FAMILIES:
        return hint
    return _VOICER_FAMILY_BY_ID.get(hint)


def _unique_in_order(values: list[str | None]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value is not None and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _voicer_order(
    genre: str,
    counts: Counter,
    preferred_family: str | None,
) -> list[str]:
    own_genre = _voicers_for_genre(genre)
    other_genre = _voicers_for_genre(
        _SOURCE_GENRES[1 - _SOURCE_GENRES.index(genre)]
    )
    own_ranked = sorted(
        own_genre,
        key=lambda voicer: (counts[voicer], _VOICER_ORDER[voicer]),
    )
    other_ranked = sorted(
        other_genre,
        key=lambda voicer: (counts[voicer], _VOICER_ORDER[voicer]),
    )
    preferred_own = (
        _VOICER_ID_BY_GENRE_FAMILY.get((genre, preferred_family))
        if preferred_family is not None else None
    )
    other_genre_name = (
        _SOURCE_GENRES[1 - _SOURCE_GENRES.index(genre)]
    )
    preferred_other = (
        _VOICER_ID_BY_GENRE_FAMILY.get((other_genre_name, preferred_family))
        if preferred_family is not None else None
    )
    return _unique_in_order([
        own_ranked[0],
        preferred_own,
        *own_ranked,
        other_ranked[0],
        preferred_other,
        *other_ranked,
    ])


def _render_progression(
    progression: dict,
    ordinal: int,
    seed: int | None,
    mode: str,
    *,
    preferred_family: str | None = None,
    voicer_order: list[str] | tuple[str, ...] | None = None,
    percussion_percent: float | int | None = None,
) -> dict:
    percussion_percent = _percussion_inclusion_percent(percussion_percent)
    chord_module = ChordModule(mode=mode, seed=seed)
    if voicer_order is not None:
        chord_track = chord_module.render(
            progression,
            bass_module_active=True,
            voicer_order=voicer_order,
        )
    else:
        chord_track = chord_module.render(
            progression,
            preferred_family=preferred_family,
            bass_module_active=True,
        )
    bass_module = BassModule(seed=seed)
    bass_track = bass_module.render(
        progression,
        pad_instrument=chord_module.selected_instrument,
        pad_mode=mode == "pads",
        chord_midis=chord_module.last_voiced_midis,
    )
    percussion_module = PercussionModule(
        seed=seed,
        omission_probability=1.0 - percussion_percent / 100.0,
    )
    percussion_track = percussion_module.render(progression)
    tracks = RenderedSongTracks(
        ordinal=ordinal,
        chord_track=chord_track,
        bass_track=bass_track,
        percussion_track=percussion_track,
        mixed_line="  ".join(
            (chord_track, bass_track, percussion_track)
        ),
    )
    return {
        "tracks": tracks,
        "chord_track": tracks.chord_track,
        "bass_track": tracks.bass_track,
        "percussion_track": tracks.percussion_track,
        "percussion_included": percussion_module.last_included,
        "percussion_feel": percussion_module.last_feel,
        "percussion_inclusion_percent": percussion_percent,
        "voicer": chord_module.last_voicer or "unknown",
        "voicer_genre": chord_module.last_voicer_genre,
        "voicer_family": chord_module.last_voicer_family,
        "instrument": chord_module.last_instrument,
        "instrument_program": chord_module.last_instrument_program,
        "bass_instrument": bass_module.last_instrument.name,
        "bass_program": bass_module.last_instrument.program,
        "pad_collapse": bass_module.collapsed_to_pad,
        "voicing_summary": _voicing_summary(chord_module),
        "render_mode": mode,
        "arpeggio": (
            _arpeggio_manifest(chord_module)
            if mode == "arpeggios" else None
        ),
    }


def _render_source(args: tuple) -> dict:
    """Render one source record in an isolated worker."""
    index, raw, seed, mode, *extras = args
    voicer_orders = extras
    percussion_percent = _DEFAULT_PERCUSSION_PERCENT
    if (
        extras
        and isinstance(extras[-1], (int, float))
        and not isinstance(extras[-1], bool)
    ):
        percussion_percent = _percussion_inclusion_percent(extras[-1])
        voicer_orders = extras[:-1]
    progression = json.loads(raw.decode("utf-8"))
    requested_order = voicer_orders[0] if voicer_orders else None
    if isinstance(requested_order, (list, tuple)):
        result = _render_progression(
            progression,
            index,
            seed + index,
            mode,
            voicer_order=requested_order,
            percussion_percent=percussion_percent,
        )
    else:
        result = _render_progression(
            progression,
            index,
            seed + index,
            mode,
            preferred_family=(
                requested_order
                if requested_order is not None
                else progression.get("voicer_family")
            ),
            percussion_percent=percussion_percent,
        )
    return result


def render_song_tracks(
    progression: dict,
    seed: int | None = None,
    mode: str = "pads",
    *,
    percussion_percent: float | int | None = None,
) -> RenderedSongTracks:
    """Render one progression once and retain all synchronized role strings."""
    result = _render_progression(
        progression,
        0,
        seed,
        mode,
        preferred_family=progression.get("voicer_family"),
        percussion_percent=percussion_percent,
    )
    return result["tracks"]


def render_song(
    progression: dict,
    seed: int | None = None,
    mode: str = "pads",
    *,
    percussion_percent: float | int | None = None,
) -> str:
    return render_song_tracks(
        progression,
        seed=seed,
        mode=mode,
        percussion_percent=percussion_percent,
    ).mixed_line


def _canonical_score_block(ordinal: int, line: str) -> str:
    return f"START_SONG_{ordinal}\n{line}\nEND_SONG\n"


def _stage_text(path: Path, text: str) -> Path:
    temporary = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(text.encode("utf-8"))
    except BaseException:
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    return temporary_path


def _reserve_backup(path: Path) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".bak",
        delete=False,
    )
    backup = Path(handle.name)
    handle.close()
    backup.unlink()
    return backup


def _install_output_set(staged: dict[Path, Path]) -> None:
    """Install all staged files together and restore the previous set on error."""
    backups: dict[Path, Path] = {}
    installed: list[Path] = []
    try:
        for target in staged:
            if target.exists():
                backup = _reserve_backup(target)
                backups[target] = backup
                os.replace(target, backup)
        for target, temporary in staged.items():
            os.replace(temporary, target)
            installed.append(target)
    except BaseException:
        for target in reversed(installed):
            if target.exists():
                target.unlink()
        for target, backup in backups.items():
            if backup.exists():
                os.replace(backup, target)
        raise
    finally:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()
        for backup in backups.values():
            if backup.exists():
                backup.unlink()


def render_directory(
    input_dir: str | Path | list[str] | tuple[str, ...],
    output: str,
    seed: int | None = None,
    mode: str = "pads",
    *,
    arpeggio_percent: float | None = None,
    pad_percent: float | None = None,
    percussion_percent: float | int | None = None,
) -> None:
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("render_directory requires an explicit integer seed")
    output = str(output)
    input_dir_list = _input_dir_list(input_dir)
    files = _source_files(input_dir_list)
    render_modes, effective_mode, render_mode_targets = _render_mode_plan(
        mode,
        len(files),
        seed,
        arpeggio_percent,
        pad_percent,
    )
    percussion_inclusion_percent = _percussion_inclusion_percent(
        percussion_percent
    )
    render_mode_counts = Counter(render_modes)
    source_dirs = [
        str(Path(input_dir).resolve()) for input_dir in input_dir_list
    ]
    revision, dirty = _generator_revision()
    output_paths = _track_output_paths(output)
    output_path = output_paths["mixed"]
    manifest_path = output_paths["manifest"]
    voicer_counts = Counter()
    family_counts_by_genre = {}
    manifest_records = []
    no_chord_by_genre = Counter()
    no_chord_duration_by_genre = Counter()
    percussion_included_count = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    score_blocks = {
        "mixed": [],
        "chords": [],
        "bass": [],
        "percussion": [],
    }
    staged: dict[Path, Path] = {}
    try:
        workers = min(8, len(files), os.cpu_count() or 1)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for batch_start in range(0, len(files), workers):
                batch = files[batch_start:batch_start + workers]
                projected_counts = Counter(voicer_counts)
                voicer_orders = []
                for (
                    _source_id, _path, _raw, progression, _source_dir
                ) in batch:
                    genre = progression.get("genre")
                    order = _voicer_order(
                        genre,
                        projected_counts,
                        _preferred_family(progression),
                    )
                    projected_counts[order[0]] += 1
                    voicer_orders.append(order)
                render_args = (
                    (
                        batch_start + offset,
                        raw,
                        seed,
                        render_modes[batch_start + offset],
                        voicer_order,
                        percussion_inclusion_percent,
                    )
                    for offset, (
                        (_source_id, _path, raw, _progression, _source_dir),
                        voicer_order,
                    ) in enumerate(zip(batch, voicer_orders))
                )
                rendered = executor.map(_render_source, render_args, chunksize=1)
                for offset, result in enumerate(rendered):
                    index = batch_start + offset
                    (
                        source_id, path, raw, progression, source_dir
                    ) = batch[offset]
                    voicer = result["voicer"]
                    voicer_counts[voicer] += 1
                    genre = progression.get("genre")
                    family_counts_by_genre.setdefault(
                        genre, Counter()
                    )[result["voicer_family"]] += 1
                    no_chord = _no_chord_summary(progression)
                    no_chord_by_genre[genre] += no_chord["event_count"]
                    no_chord_duration_by_genre[genre] += no_chord["duration_seconds"]
                    if result["percussion_included"]:
                        percussion_included_count += 1
                    tracks = result["tracks"]
                    role_lines = {
                        "mixed": tracks.mixed_line,
                        "chords": tracks.chord_track,
                        "bass": tracks.bass_track,
                        "percussion": tracks.percussion_track,
                    }
                    for role, line in role_lines.items():
                        score_blocks[role].append(
                            _canonical_score_block(index, line)
                        )
                    manifest_records.append({
                        "ordinal": index,
                        "source_dir": str(source_dir.resolve()),
                        "source_file": path.name,
                        "source_id": source_id,
                        "source_sha256": hashlib.sha256(raw).hexdigest(),
                        "genre": progression.get("genre"),
                        "tonic_pc": progression.get("tonic_pc"),
                        "bpm": progression.get("bpm", 120),
                        "num_chords": progression.get(
                            "num_chords", len(progression.get("chords", ()))
                        ),
                        "preferred_voicer": voicer_orders[offset][0],
                        "preferred_voicer_family": _voicer_family(
                            voicer_orders[offset][0]
                        ),
                        "voicer_order": voicer_orders[offset],
                        "voicer": voicer,
                        "voicer_genre": result["voicer_genre"],
                        "voicer_family": result["voicer_family"],
                        "render_mode": result["render_mode"],
                        "arpeggio": result["arpeggio"],
                        "percussion_included": result["percussion_included"],
                        "percussion_feel": result["percussion_feel"],
                        "percussion_inclusion_percent": (
                            percussion_inclusion_percent
                        ),
                        "seed": seed + index,
                        "voicing_summary": result["voicing_summary"],
                        "no_chord": no_chord,
                        "track_hashes": {
                            role: hashlib.sha256(
                                score_blocks[role][-1].encode("utf-8")
                            ).hexdigest()
                            for role in (
                                "mixed",
                                "chords",
                                "bass",
                                "percussion",
                            )
                        },
                    })
                    print(
                        f"Rendered song {index}: {source_dir.name}/{path.name} "
                        f"({voicer}, chord={result['instrument']} "
                        f"[I{result['instrument_program']}], "
                        f"bass={result['bass_instrument']} "
                        f"[I{result['bass_program']}]"
                        f"{' (pad collapse)' if result['pad_collapse'] else ''}, "
                        f"{result['render_mode']})"
                        f", percussion={'on' if result['percussion_included'] else 'off'}"
                    )

        manifest = {
            "manifest_version": 1,
            "command": _render_command(
                input_dir_list,
                output,
                seed,
                effective_mode,
                render_mode_targets if effective_mode == "mixed" else None,
                percussion_inclusion_percent,
            ),
            "seed": seed,
            "source_dir": source_dirs[0] if len(source_dirs) == 1 else None,
            "source_dirs": source_dirs,
            "output": str(output_path.resolve()),
            "generator_revision": revision,
            "generator_revision_dirty": dirty,
            "render_mode": effective_mode,
            "render_mode_counts": {
                render_mode: render_mode_counts.get(render_mode, 0)
                for render_mode in _RENDER_MODES
            },
            "render_mode_percentages": {
                render_mode: (
                    100.0 * render_mode_counts.get(render_mode, 0) / len(render_modes)
                    if render_modes else 0.0
                )
                for render_mode in _RENDER_MODES
            },
            "render_mode_targets": (
                render_mode_targets if effective_mode == "mixed" else None
            ),
            "percussion_inclusion_percent": percussion_inclusion_percent,
            "percussion_inclusion_probability": (
                percussion_inclusion_percent / 100.0
            ),
            "percussion_included_count": percussion_included_count,
            "percussion_realized_percent": (
                100.0 * percussion_included_count / len(render_modes)
                if render_modes else 0.0
            ),
            "voicer_counts": {
                voicer: voicer_counts.get(voicer, 0)
                for voicer in _VOICER_IDS
            },
            "voicer_family_counts": {
                genre: {
                    family: counts.get(family, 0)
                    for family in _VOICER_FAMILIES
                }
                for genre, counts in sorted(family_counts_by_genre.items())
            },
            "no_chord_by_genre": {
                genre: {
                    "event_count": no_chord_by_genre[genre],
                    "duration_seconds": no_chord_duration_by_genre[genre],
                }
                for genre in sorted(no_chord_by_genre)
            },
            "track_outputs": {
                "schema_version": 1,
                "song_count": len(files),
                "voices": {
                    "mixed": ["V0", "V1", "V9"],
                    "chords": ["V0"],
                    "bass": ["V1"],
                    "percussion": ["V9"],
                },
                "paths": {
                    role: str(output_paths[role].resolve())
                    for role in (
                        "mixed",
                        "chords",
                        "bass",
                        "percussion",
                    )
                },
                "sha256": {
                    role: hashlib.sha256(
                        "".join(score_blocks[role]).encode("utf-8")
                    ).hexdigest()
                    for role in (
                        "mixed",
                        "chords",
                        "bass",
                        "percussion",
                    )
                },
                "ordinals": list(range(len(files))),
                "block_hashes": (
                    "sha256 of each canonical UTF-8 score block, including "
                    "START_SONG_N, its score line, END_SONG, and final newline"
                ),
            },
            "records": manifest_records,
        }
        for role in ("mixed", "chords", "bass", "percussion"):
            staged[output_paths[role]] = _stage_text(
                output_paths[role],
                "".join(score_blocks[role]),
            )
        manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        staged[manifest_path] = _stage_text(manifest_path, manifest_text)
        _install_output_set(staged)
    except BaseException:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()
        raise

    print(f"Rendered {len(files)} song(s) to {output}")
    print("Voicer summary:")
    for voicer in _VOICER_IDS:
        print(f"  {voicer}: {voicer_counts.get(voicer, 0)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--in-dir",
        action="extend",
        nargs="+",
        required=True,
        metavar="DIR",
        help="One or more directories containing song_<integer>.json files.",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--mode",
        choices=("pads", "arpeggios", "mixed"),
        default="pads",
        help="Render all songs as pads, arpeggios, or a percentage mix.",
    )
    parser.add_argument(
        "--arpeggio-percent",
        "--arpeggio-percentage",
        dest="arpeggio_percent",
        type=float,
        help="Target percentage of songs rendered as arpeggios.",
    )
    parser.add_argument(
        "--pad-percent",
        "--pad-percentage",
        dest="pad_percent",
        type=float,
        help="Target percentage of songs rendered as pads.",
    )
    parser.add_argument(
        "--percussion-percent",
        "--percussion-percentage",
        "--percussion-inclusion-percent",
        dest="percussion_percent",
        type=float,
        help=(
            "Per-song probability percentage for audible percussion; "
            "default is 70."
        ),
    )
    args = parser.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    render_directory(
        args.in_dir,
        args.out,
        args.seed,
        args.mode,
        arpeggio_percent=args.arpeggio_percent,
        pad_percent=args.pad_percent,
        percussion_percent=args.percussion_percent,
    )


if __name__ == "__main__":
    main()
