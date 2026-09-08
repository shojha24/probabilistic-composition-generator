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
from melody_module import (
    MelodyGenerationConfig,
    build_melody_manifest,
    disabled_melody_record,
    derive_child_seed,
    generate_melody,
    get_melody_profile,
    melody_inclusion_plan,
    rest_melody_events,
    select_melody_instrument,
    serialize_melody_events,
)
from percussion_module import (
    PERCUSSION_OMISSION_PROBABILITY,
    PercussionModule,
)
from sound_design import (
    CONDITION_ROLES,
    REFERENCE_RENDER_PROFILE,
    derive_seed,
    parameter_manifest,
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
    melody_track: str | None = None


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
                "melody_module.py",
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


def _track_output_paths(
    output: str | Path,
    *,
    include_melody: bool = False,
) -> dict[str, Path]:
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
    }
    if include_melody:
        paths["melody"] = output_path.with_name(
            f"{output_path.stem}_melody{output_path.suffix}"
        )
    paths["percussion"] = output_path.with_name(
        f"{output_path.stem}_percussion{output_path.suffix}"
    )
    paths["manifest"] = _manifest_path(output_path)
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


def _melody_condition(value: str) -> str:
    if value not in {"none", "naturalistic"}:
        raise ValueError("melody_condition must be 'none' or 'naturalistic'")
    return value


def _condition(value: str | None) -> str | None:
    if value is not None and value not in CONDITION_ROLES:
        raise ValueError(
            f"condition must be one of {', '.join(CONDITION_ROLES)}"
        )
    return value


def _condition_options(
    condition: str | None,
    melody_condition: str,
    melody_percent: float | int,
    percussion_percent: float | int | None,
) -> tuple[str, float | int, float | int | None]:
    """Resolve a condition without allowing it to regenerate symbolic roles."""
    if condition is None:
        return melody_condition, melody_percent, percussion_percent
    roles = CONDITION_ROLES[condition]
    if condition != "naturalistic":
        melody_percent = 100.0 if "V2" in roles else 0.0
        percussion_percent = 100.0 if "V9" in roles else 0.0
    return "naturalistic", melody_percent, percussion_percent


def _melody_decoder(value: str) -> str:
    if value not in {"chord_centered_random_walk", "sequence_beam"}:
        raise ValueError(
            "melody_decoder must be 'chord_centered_random_walk' "
            "or 'sequence_beam'"
        )
    return value


def _melody_collapse_probability(value: float | int) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 1
    ):
        raise ValueError(
            "melody_collapse_probability must be a finite value from 0 to 1"
        )
    return float(value)


def _percussion_inclusion_plan(
    file_count: int,
    inclusion_percent: float,
    seed: int,
) -> list[bool]:
    """Select an exact, deterministic nearest-whole-song percussion quota."""
    if (
        isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or file_count < 0
    ):
        raise ValueError("file_count must be a non-negative integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    inclusion_percent = _percentage(
        inclusion_percent, "percussion_percent"
    )
    inclusion_count = min(
        file_count,
        max(
            0,
            math.floor(
                file_count * inclusion_percent / 100.0 + 0.5
            ),
        ),
    )
    quota_seed = int.from_bytes(
        hashlib.sha256(
            f"percussion_quota:{seed!r}".encode("utf-8")
        ).digest()[:8],
        "big",
    )
    included_indexes = set(
        random.Random(quota_seed).sample(range(file_count), inclusion_count)
    )
    return [
        index in included_indexes
        for index in range(file_count)
    ]


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
    melody_condition: str = "none",
    melody_profile: str = "lead-high-sparse",
    melody_percent: float = 70.0,
    melody_collapse_probability: float = 0.25,
    melody_decoder: str = "sequence_beam",
) -> str:
    percussion_percent = _percussion_inclusion_percent(percussion_percent)
    command = [
        sys.executable, "render.py", "--in-dir", *input_dirs,
        "--out", output, "--mode", mode, "--seed", str(seed),
        "--percussion-percent", f"{percussion_percent:g}",
    ]
    if melody_condition != "none":
        command.extend([
            "--melody-condition", melody_condition,
            "--melody-profile", melody_profile,
            "--melody-percent", f"{melody_percent:g}",
            "--melody-collapse-probability",
            f"{melody_collapse_probability:g}",
            "--melody-decoder", melody_decoder,
        ])
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
    percussion_included: bool | None = None,
    melody_condition: str = "none",
    melody_profile: str = "lead-high-sparse",
    melody_included: bool | None = None,
    melody_percent: float | int | None = None,
    melody_selection_mode: str = "single_song_resolved",
    melody_omission_reason: str | None = None,
    melody_collapse_probability: float = 0.25,
    melody_decoder: str = "sequence_beam",
) -> dict:
    percussion_percent = _percussion_inclusion_percent(percussion_percent)
    melody_condition = _melody_condition(melody_condition)
    profile_obj = get_melody_profile(melody_profile)
    melody_collapse_probability = _melody_collapse_probability(
        melody_collapse_probability
    )
    melody_decoder = _melody_decoder(melody_decoder)
    if (
        percussion_included is not None
        and not isinstance(percussion_included, bool)
    ):
        raise ValueError("percussion_included must be a boolean or None")
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
        omission_probability=(
            (
                0.0 if percussion_included else 1.0
            )
            if percussion_included is not None
            else 1.0 - percussion_percent / 100.0
        ),
    )
    percussion_track = percussion_module.render(progression)

    melody_track = None
    melody_record = None
    if melody_condition == "none":
        melody_included = False
    else:
        if melody_included is None:
            melody_included = True
        if not isinstance(melody_included, bool):
            raise ValueError("melody_included must be a boolean or None")
        melody_seed = (
            derive_child_seed(seed, "melody-profile")
            if isinstance(seed, int) and not isinstance(seed, bool)
            else random.SystemRandom().getrandbits(128)
        )
        if melody_included:
            generation_config = MelodyGenerationConfig(
                decoder=melody_decoder,
                melody_collapse_probability=melody_collapse_probability,
            )
            realized_voicings = [
                {
                    "midi": midis,
                    "roles": roles,
                }
                for midis, roles in zip(
                    chord_module.last_voiced_midis,
                    chord_module.last_voiced_roles,
                )
            ]
            melody_result = generate_melody(
                progression,
                seed=melody_seed,
                profile=profile_obj,
                config=generation_config,
                realized_voicings=realized_voicings,
            )
            instrument_selection = select_melody_instrument(
                profile_obj,
                melody_seed,
                chord_instrument=chord_module.selected_instrument,
                collapse_probability=melody_collapse_probability,
            )
            melody_track = serialize_melody_events(
                melody_result.events,
                progression.get("bpm", 120),
                int(instrument_selection["instrument_program"]),
            )
            melody_record = build_melody_manifest(
                melody_result,
                profile_obj,
                included=True,
                inclusion_percent=melody_percent,
                selection_mode=melody_selection_mode,
                instrument_selection=instrument_selection,
                collapse_probability=melody_collapse_probability,
            )
            melody_record["render_seed"] = seed
        else:
            rest_events = rest_melody_events(progression)
            melody_track = serialize_melody_events(
                rest_events,
                progression.get("bpm", 120),
                profile_obj.instrument_program,
            )
            melody_record = disabled_melody_record(
                condition=melody_condition,
                profile=profile_obj.name,
                inclusion_percent=melody_percent,
                included=False,
                selection_mode=melody_selection_mode,
                omission_reason=melody_omission_reason or "corpus_quota",
                collapse_probability=melody_collapse_probability,
            )
    tracks = RenderedSongTracks(
        ordinal=ordinal,
        chord_track=chord_track,
        bass_track=bass_track,
        percussion_track=percussion_track,
        mixed_line="  ".join(
            (
                (chord_track, bass_track, melody_track, percussion_track)
                if melody_track is not None
                else (chord_track, bass_track, percussion_track)
            )
        ),
        melody_track=melody_track,
    )
    return {
        "tracks": tracks,
        "chord_track": tracks.chord_track,
        "bass_track": tracks.bass_track,
        "percussion_track": tracks.percussion_track,
        "percussion_included": percussion_module.last_included,
        "percussion_feel": percussion_module.last_feel,
        "percussion_inclusion_percent": percussion_percent,
        "melody_track": tracks.melody_track,
        "melody": melody_record,
        "melody_included": melody_included,
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
    if len(args) == 8:
        (
            index,
            raw,
            seed,
            mode,
            requested_order,
            percussion_percent,
            percussion_included,
            melody_options,
        ) = args
    else:
        index, raw, seed, mode, *extras = args
        percussion_included = None
        if extras and isinstance(extras[-1], bool):
            percussion_included = extras.pop()
        voicer_orders = extras
        percussion_percent = None
        if (
            extras
            and isinstance(extras[-1], (int, float))
            and not isinstance(extras[-1], bool)
        ):
            percussion_percent = extras[-1]
            voicer_orders = extras[:-1]
        requested_order = voicer_orders[0] if voicer_orders else None
        melody_options = {}
    progression = json.loads(raw.decode("utf-8"))
    melody_options = dict(melody_options)
    if isinstance(requested_order, (list, tuple)):
        result = _render_progression(
            progression,
            index,
            seed + index,
            mode,
            voicer_order=requested_order,
            percussion_percent=percussion_percent,
            percussion_included=percussion_included,
            **melody_options,
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
            percussion_included=percussion_included,
            **melody_options,
        )
    return result


def render_song_tracks(
    progression: dict,
    seed: int | None = None,
    mode: str = "pads",
    *,
    percussion_percent: float | int | None = None,
    melody_condition: str = "none",
    melody_profile: str = "lead-high-sparse",
    melody_included: bool = True,
    melody_collapse_probability: float = 0.25,
    melody_decoder: str = "sequence_beam",
) -> RenderedSongTracks:
    """Render one progression once and retain all synchronized role strings."""
    result = _render_progression(
        progression,
        0,
        seed,
        mode,
        preferred_family=progression.get("voicer_family"),
        percussion_percent=percussion_percent,
        melody_condition=melody_condition,
        melody_profile=melody_profile,
        melody_included=melody_included,
        melody_selection_mode="single_song_resolved",
        melody_collapse_probability=melody_collapse_probability,
        melody_decoder=melody_decoder,
    )
    return result["tracks"]


def render_song(
    progression: dict,
    seed: int | None = None,
    mode: str = "pads",
    *,
    percussion_percent: float | int | None = None,
    melody_condition: str = "none",
    melody_profile: str = "lead-high-sparse",
    melody_included: bool = True,
    melody_collapse_probability: float = 0.25,
    melody_decoder: str = "sequence_beam",
) -> str:
    return render_song_tracks(
        progression,
        seed=seed,
        mode=mode,
        percussion_percent=percussion_percent,
        melody_condition=melody_condition,
        melody_profile=melody_profile,
        melody_included=melody_included,
        melody_collapse_probability=melody_collapse_probability,
        melody_decoder=melody_decoder,
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


def _install_output_set(
    staged: dict[Path, Path],
    *,
    remove_paths: tuple[Path, ...] = (),
) -> None:
    """Install all staged files together and restore the previous set on error."""
    backups: dict[Path, Path] = {}
    installed: list[Path] = []
    all_targets = [*staged, *remove_paths]
    if len(set(all_targets)) != len(all_targets):
        raise ValueError("staged and removed output paths must be unique")
    try:
        for target in all_targets:
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


def render_midi_scores(
    score_path: str | Path,
    output_dir: str | Path,
    *,
    seed: int,
) -> dict[str, dict[str, str]]:
    """Convert score blocks to deterministic MIDI using the pinned Java renderer."""
    repo_root = Path(__file__).resolve().parent
    score_path = Path(score_path)
    output_dir = Path(output_dir)
    jar = repo_root / "jfugue-5.0.9.jar"
    source = repo_root / "HumanizedMidiRenderer.java"
    class_file = repo_root / "HumanizedMidiRenderer.class"
    if not jar.is_file():
        raise FileNotFoundError(f"Missing JFugue dependency: {jar}")
    if not class_file.is_file() or class_file.stat().st_mtime < source.stat().st_mtime:
        subprocess.run(
            ["javac", "-cp", str(jar), str(source)],
            cwd=repo_root,
            check=True,
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "java", "-cp", os.pathsep.join((".", str(jar))),
            "HumanizedMidiRenderer", str(score_path.resolve()),
            str(output_dir.resolve()), str(seed),
        ],
        cwd=repo_root,
        check=True,
    )
    records = {}
    for ordinal in parse_score_ordinals(score_path):
        midi_path = output_dir / f"START_SONG_{ordinal}.mid"
        if not midi_path.is_file():
            raise RuntimeError(f"MIDI renderer did not produce {midi_path.name}")
        records[str(ordinal)] = {
            "path": str(midi_path.resolve()),
            "sha256": hashlib.sha256(midi_path.read_bytes()).hexdigest(),
            "humanization_seed": str(derive_seed(seed, "humanization", ordinal)),
        }
    return records


def parse_score_ordinals(score_path: str | Path) -> list[int]:
    """Read score ordinals without importing the corpus validator."""
    ordinals = []
    for line in Path(score_path).read_text(encoding="utf-8").splitlines():
        if line.startswith("START_SONG_"):
            ordinals.append(int(line.removeprefix("START_SONG_")))
    return ordinals


def render_directory(
    input_dir: str | Path | list[str] | tuple[str, ...],
    output: str,
    seed: int | None = None,
    mode: str = "pads",
    *,
    arpeggio_percent: float | None = None,
    pad_percent: float | None = None,
    percussion_percent: float | int | None = None,
    melody_condition: str = "none",
    melody_profile: str = "lead-high-sparse",
    melody_percent: float | int = 70.0,
    melody_collapse_probability: float = 0.25,
    melody_decoder: str = "sequence_beam",
    condition: str | None = None,
    stage: str = "score",
    midi_output: str | Path | None = None,
) -> None:
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("render_directory requires an explicit integer seed")
    if stage not in {"score", "midi"}:
        raise ValueError("stage must be 'score' or 'midi'")
    if stage == "midi" and midi_output is None:
        midi_output = Path(output).with_name(f"{Path(output).stem}_midi")
    output = str(output)
    input_dir_list = _input_dir_list(input_dir)
    files = _source_files(input_dir_list)
    condition = _condition(condition)
    melody_condition, melody_percent, percussion_percent = _condition_options(
        condition, melody_condition, melody_percent, percussion_percent
    )
    melody_condition = _melody_condition(melody_condition)
    profile_obj = get_melody_profile(melody_profile)
    melody_percent = _percentage(melody_percent, "melody_percent")
    melody_collapse_probability = _melody_collapse_probability(
        melody_collapse_probability
    )
    melody_decoder = _melody_decoder(melody_decoder)
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
    percussion_inclusion_plan = _percussion_inclusion_plan(
        len(files),
        percussion_inclusion_percent,
        seed,
    )
    melody_inclusion_plan_values = (
        melody_inclusion_plan(
            len(files),
            melody_percent,
            seed,
            source_ids=[item[0] for item in files],
        )
        if melody_condition == "naturalistic"
        else [False] * len(files)
    )
    render_mode_counts = Counter(render_modes)
    source_dirs = [
        str(Path(input_dir).resolve()) for input_dir in input_dir_list
    ]
    revision, dirty = _generator_revision()
    output_paths = _track_output_paths(
        output,
        include_melody=melody_condition == "naturalistic",
    )
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
    if melody_condition == "naturalistic":
        score_blocks["melody"] = []
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
                        percussion_inclusion_plan[batch_start + offset],
                        {
                            "melody_condition": melody_condition,
                            "melody_profile": profile_obj.name,
                            "melody_included": melody_inclusion_plan_values[
                                batch_start + offset
                            ],
                            "melody_percent": melody_percent,
                            "melody_selection_mode": (
                                "exact_quota"
                                if melody_condition == "naturalistic"
                                else "disabled"
                            ),
                            "melody_omission_reason": (
                                None
                                if melody_inclusion_plan_values[
                                    batch_start + offset
                                ]
                                else "corpus_quota"
                            ),
                            "melody_collapse_probability": (
                                melody_collapse_probability
                            ),
                            "melody_decoder": melody_decoder,
                        },
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
                        "chords": tracks.chord_track,
                        "bass": tracks.bass_track,
                        "percussion": tracks.percussion_track,
                    }
                    if melody_condition == "naturalistic":
                        if tracks.melody_track is None:
                            raise RuntimeError(
                                "naturalistic rendering did not produce V2"
                            )
                        role_lines["melody"] = tracks.melody_track
                    if condition is None:
                        role_lines["mixed"] = tracks.mixed_line
                    else:
                        voice_to_track = {
                            "V0": tracks.chord_track,
                            "V1": tracks.bass_track,
                            "V2": tracks.melody_track,
                            "V9": tracks.percussion_track,
                        }
                        role_lines["mixed"] = "  ".join(
                            voice_to_track[voice]
                            for voice in CONDITION_ROLES[condition]
                        )
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
                        "mode": progression.get("mode"),
                        "scale_pcs": progression.get("scale_pcs"),
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
                        "melody": result["melody"],
                        "seed": seed + index,
                        "randomness": {
                            "seed_derivation_version": "fnv1a64-v1",
                            "humanization_seed": derive_seed(
                                seed, "humanization", index
                            ),
                            "sound_design_seed": derive_seed(
                                seed, "sound-design", index
                            ),
                        },
                        "condition_id": condition or "all_roles",
                        "selected_roles": list(
                            CONDITION_ROLES[condition]
                            if condition is not None
                            else ("V0", "V1", "V2", "V9")
                        ),
                        "role_presence": {
                            "V0": True,
                            "V1": True,
                            "V2": bool(result["melody_included"]),
                            "V9": bool(result["percussion_included"]),
                        },
                        "audio_rendered": False,
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
                                *(
                                    ("melody",)
                                    if melody_condition == "naturalistic"
                                    else ()
                                ),
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

        effective_config = {
            "condition": condition or "all_roles",
            "render_mode": effective_mode,
            "percussion_percent": percussion_inclusion_percent,
            "melody_condition": melody_condition,
            "melody_percent": melody_percent,
            "melody_profile": melody_profile,
            "melody_decoder": melody_decoder,
            "render_profile": REFERENCE_RENDER_PROFILE["profile_id"],
        }
        manifest = {
            "manifest_version": 2,
            "command": _render_command(
                input_dir_list,
                output,
                seed,
                effective_mode,
                render_mode_targets if effective_mode == "mixed" else None,
                percussion_inclusion_percent,
                melody_condition,
                melody_profile,
                melody_percent,
                melody_collapse_probability,
                melody_decoder,
            ),
            "seed": seed,
            "source_dir": source_dirs[0] if len(source_dirs) == 1 else None,
            "source_dirs": source_dirs,
            "output": str(output_path.resolve()),
            "generator_revision": revision,
            "generator_revision_dirty": dirty,
            "parameter_manifest": parameter_manifest(
                config=effective_config,
                source_manifests=[
                    {
                        "path": str(path.resolve()),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                    for _source_id, path, raw, _progression, _source_dir in files
                ],
            ),
            "sound_design": {
                "render_profile": REFERENCE_RENDER_PROFILE,
                "audio_rendered": False,
                "stage": "score",
            },
            "condition_id": condition or "all_roles",
            "selected_roles": list(
                CONDITION_ROLES[condition]
                if condition is not None
                else ("V0", "V1", "V2", "V9")
            ),
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
            "percussion_inclusion_target_fraction": (
                percussion_inclusion_percent / 100.0
            ),
            "percussion_selection_mode": "exact_corpus_quota",
            "percussion_target_count": sum(percussion_inclusion_plan),
            "percussion_included_count": percussion_included_count,
            "percussion_realized_percent": (
                100.0 * percussion_included_count / len(render_modes)
                if render_modes else 0.0
            ),
            "melody_inclusion": (
                {
                    "requested_percent": melody_percent,
                    "eligible_song_count": len(files),
                    "target_song_count": sum(melody_inclusion_plan_values),
                    "realized_song_count": sum(
                        bool(record.get("melody", {}).get("included"))
                        if isinstance(record.get("melody"), dict)
                        else False
                        for record in manifest_records
                    ),
                    "realized_percent": (
                        100.0 * sum(melody_inclusion_plan_values) / len(files)
                        if files else 0.0
                    ),
                    "selection_mode": "exact_quota",
                }
                if melody_condition == "naturalistic"
                else None
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
                    "mixed": (
                        list(CONDITION_ROLES[condition])
                        if condition is not None else
                        ["V0", "V1", "V2", "V9"]
                        if melody_condition == "naturalistic"
                        else ["V0", "V1", "V9"]
                    ),
                    "chords": ["V0"],
                    "bass": ["V1"],
                    **(
                        {"melody": ["V2"]}
                        if melody_condition == "naturalistic"
                        else {}
                    ),
                    "percussion": ["V9"],
                },
                "paths": {
                    role: str(output_paths[role].resolve())
                    for role in (
                        "mixed",
                        "chords",
                        "bass",
                        *(
                            ("melody",)
                            if melody_condition == "naturalistic"
                            else ()
                        ),
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
                        *(
                            ("melody",)
                            if melody_condition == "naturalistic"
                            else ()
                        ),
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
        for role in score_blocks:
            staged[output_paths[role]] = _stage_text(
                output_paths[role],
                "".join(score_blocks[role]),
            )
        manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        staged[manifest_path] = _stage_text(manifest_path, manifest_text)
        stale_outputs = ()
        if melody_condition == "none":
            stale_outputs = (
                _track_output_paths(
                    output,
                    include_melody=True,
                )["melody"],
            )
        _install_output_set(staged, remove_paths=stale_outputs)
    except BaseException:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()
        raise

    if stage == "midi":
        midi_records = render_midi_scores(output_path, midi_output, seed=seed)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sound_design"]["stage"] = "midi"
        manifest["sound_design"]["midi"] = {
            "renderer": "HumanizedMidiRenderer.java",
            "seed_derivation_version": "fnv1a64-v1",
            "records": midi_records,
        }
        _install_output_set({
            manifest_path: _stage_text(
                manifest_path,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            )
        })

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
            "Target percentage of songs with audible percussion; "
            "default is 70."
        ),
    )
    parser.add_argument(
        "--melody-condition",
        choices=("none", "naturalistic"),
        default="none",
        help="Disable melody or add a chord-conditioned V2 melody.",
    )
    parser.add_argument(
        "--melody-profile",
        choices=("lead-high-sparse", "lead-mid-neutral", "lead-mid-active"),
        default="lead-high-sparse",
    )
    parser.add_argument(
        "--melody-percent",
        type=float,
        default=70.0,
        help="Exact naturalistic melody inclusion target percentage.",
    )
    parser.add_argument(
        "--melody-collapse-probability",
        type=float,
        default=0.25,
        help="Probability of reusing the selected V0 chord instrument on V2.",
    )
    parser.add_argument(
        "--melody-decoder",
        choices=("chord_centered_random_walk", "sequence_beam"),
        default="sequence_beam",
    )
    parser.add_argument(
        "--condition",
        choices=tuple(CONDITION_ROLES),
        help=(
            "Render a fixed role ablation from the canonical symbolic roles. "
            "This controls role selection and does not regenerate the song."
        ),
    )
    parser.add_argument(
        "--stage",
        choices=("score", "midi"),
        default="score",
        help="Stop after score generation or also create deterministic MIDI.",
    )
    parser.add_argument(
        "--midi-output",
        help="Directory for MIDI output when --stage midi is selected.",
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
        melody_condition=args.melody_condition,
        melody_profile=args.melody_profile,
        melody_percent=args.melody_percent,
        melody_collapse_probability=args.melody_collapse_probability,
        melody_decoder=args.melody_decoder,
        condition=args.condition,
        stage=args.stage,
        midi_output=args.midi_output,
    )


if __name__ == "__main__":
    main()
