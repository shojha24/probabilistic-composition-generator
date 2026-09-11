#!/usr/bin/env python3
"""Project ACR condition scores from one canonical all-role render."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from melody_module import melody_inclusion_plan
from sound_design import CONDITION_ROLES, config_sha256


_START = re.compile(r"^START_SONG_(\d+)$")
_END = "END_SONG"
_ROLE_TO_TRACK = {
    "V0": "chords",
    "V1": "bass",
    "V2": "melody",
    "V9": "percussion",
}
_FIXED_CONDITIONS = ("cb", "cbp", "cbm", "cbmp")
_ALL_ROLES = ("V0", "V1", "V2", "V9")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_score_blocks(path: Path) -> dict[int, str]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    blocks: dict[int, str] = {}
    index = 0
    while index < len(lines):
        start = lines[index].rstrip("\r\n")
        match = _START.fullmatch(start)
        if match is None:
            raise ValueError(f"{path}: expected song marker at line {index + 1}")
        ordinal = int(match.group(1))
        if ordinal in blocks:
            raise ValueError(f"{path}: duplicate song ordinal {ordinal}")
        if index + 2 >= len(lines):
            raise ValueError(f"{path}: incomplete song block {ordinal}")
        score_line = lines[index + 1]
        if not score_line.strip():
            raise ValueError(f"{path}: empty score line for song {ordinal}")
        if lines[index + 2].rstrip("\r\n") != _END:
            raise ValueError(f"{path}: missing END_SONG for song {ordinal}")
        blocks[ordinal] = "".join(lines[index:index + 3])
        index += 3
    return blocks


def _score_line(block: str) -> str:
    lines = block.splitlines()
    if len(lines) != 3:
        raise ValueError("canonical score block must contain three lines")
    return lines[1]


def _canonical_role_paths(
    canonical_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Path]:
    track_outputs = manifest.get("track_outputs")
    declared_paths = (
        track_outputs.get("paths")
        if isinstance(track_outputs, dict)
        else None
    )
    if not isinstance(declared_paths, dict):
        raise ValueError("canonical manifest has no track_outputs.paths")
    paths: dict[str, Path] = {}
    for track in _ROLE_TO_TRACK.values():
        raw_path = declared_paths.get(track)
        if not isinstance(raw_path, str):
            raise ValueError(
                f"canonical manifest is missing the {track} role path"
            )
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"canonical role file is missing: {path}")
        paths[track] = path
    mixed_path = declared_paths.get("mixed")
    if isinstance(mixed_path, str) and Path(mixed_path).resolve() != canonical_path.resolve():
        raise ValueError(
            "canonical score path does not match track_outputs.paths.mixed"
        )
    return paths


def _validate_canonical(
    canonical_path: Path,
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[int, str]], Path]:
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("canonical manifest records must be a list")
    expected_ordinals = list(range(len(records)))
    record_ordinals = [
        record.get("ordinal") if isinstance(record, dict) else None
        for record in records
    ]
    if record_ordinals != expected_ordinals:
        raise ValueError("canonical manifest records are not ordinally ordered")

    role_paths = _canonical_role_paths(canonical_path, manifest)
    blocks = {
        track: _read_score_blocks(path)
        for track, path in role_paths.items()
    }
    for track, track_blocks in blocks.items():
        if list(track_blocks) != expected_ordinals:
            raise ValueError(
                f"canonical {track} blocks do not match manifest ordinals"
            )
    for record in records:
        ordinal = record["ordinal"]
        track_hashes = record.get("track_hashes")
        if not isinstance(track_hashes, dict):
            continue
        for track, track_blocks in blocks.items():
            expected_hash = track_hashes.get(track)
            if expected_hash is None:
                continue
            actual_hash = _sha256_bytes(
                track_blocks[ordinal].encode("utf-8")
            )
            if actual_hash != expected_hash:
                raise ValueError(
                    f"canonical {track} hash mismatch for song {ordinal}"
                )
    return records, blocks


def _percussion_inclusion_plan(
    song_count: int,
    inclusion_percent: float,
    seed: int,
) -> list[bool]:
    if not isinstance(song_count, int) or isinstance(song_count, bool) or song_count < 0:
        raise ValueError("song_count must be a non-negative integer")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not isinstance(inclusion_percent, (int, float)) or isinstance(inclusion_percent, bool):
        raise ValueError("percussion_percent must be numeric")
    if not math.isfinite(float(inclusion_percent)) or not 0 <= inclusion_percent <= 100:
        raise ValueError("percussion_percent must be between 0 and 100")
    count = min(
        song_count,
        max(0, math.floor(song_count * inclusion_percent / 100.0 + 0.5)),
    )
    quota_seed = int.from_bytes(
        hashlib.sha256(
            f"percussion_quota:{seed!r}".encode("utf-8")
        ).digest()[:8],
        "big",
    )
    selected = set(random.Random(quota_seed).sample(range(song_count), count))
    return [index in selected for index in range(song_count)]


def _condition_roles(
    condition: str,
    records: list[dict[str, Any]],
    *,
    seed: int,
    melody_percent: float,
    percussion_percent: float,
) -> tuple[list[tuple[str, ...]], dict[str, Any]]:
    if condition in _FIXED_CONDITIONS:
        roles = tuple(CONDITION_ROLES[condition])
        return [roles] * len(records), {
            "selection_mode": "fixed",
            "melody_percent": 100.0 if "V2" in roles else 0.0,
            "percussion_percent": 100.0 if "V9" in roles else 0.0,
            "melody_target_count": len(records) if "V2" in roles else 0,
            "percussion_target_count": len(records) if "V9" in roles else 0,
        }

    source_ids = [record.get("source_id") for record in records]
    if any(
        isinstance(source_id, bool) or not isinstance(source_id, int)
        for source_id in source_ids
    ):
        raise ValueError("canonical records must contain integer source_id values")
    melody_plan = melody_inclusion_plan(
        len(records),
        melody_percent,
        seed,
        source_ids=source_ids,
    )
    percussion_plan = _percussion_inclusion_plan(
        len(records),
        percussion_percent,
        seed,
    )
    selected_roles = []
    joint_counts: Counter[str] = Counter()
    for melody_included, percussion_included in zip(
        melody_plan,
        percussion_plan,
    ):
        roles = ["V0", "V1"]
        if melody_included:
            roles.append("V2")
        if percussion_included:
            roles.append("V9")
        selected_roles.append(tuple(roles))
        joint_counts[
            f"melody_{'on' if melody_included else 'off'}_"
            f"percussion_{'on' if percussion_included else 'off'}"
        ] += 1
    return selected_roles, {
        "selection_mode": "exact_quota",
        "melody_percent": float(melody_percent),
        "percussion_percent": float(percussion_percent),
        "melody_target_count": sum(melody_plan),
        "percussion_target_count": sum(percussion_plan),
        "melody_selection_seed_domain": "melody-inclusion",
        "percussion_selection_seed_domain": "percussion_quota",
        "joint_inclusion_counts": dict(sorted(joint_counts.items())),
        "melody_plan": melody_plan,
        "percussion_plan": percussion_plan,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _project_one(
    *,
    condition: str,
    canonical_path: Path,
    canonical_manifest_path: Path,
    canonical_manifest: dict[str, Any],
    records: list[dict[str, Any]],
    blocks: dict[str, dict[int, str]],
    output_root: Path,
    seed: int,
    melody_percent: float,
    percussion_percent: float,
    stage: str,
    soundfont: str | Path | None,
    fluidsynth_bin: str,
    ffmpeg_bin: str,
) -> Path:
    selected_roles, selection = _condition_roles(
        condition,
        records,
        seed=seed,
        melody_percent=melody_percent,
        percussion_percent=percussion_percent,
    )
    condition_dir = output_root / condition
    condition_dir.mkdir(parents=True, exist_ok=True)
    output_path = condition_dir / "scores.txt"
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")

    output_blocks: list[str] = []
    projected_records: list[dict[str, Any]] = []
    for ordinal, record in enumerate(records):
        roles = selected_roles[ordinal]
        role_lines = [
            _score_line(blocks[_ROLE_TO_TRACK[role]][ordinal])
            for role in roles
        ]
        mixed_line = "  ".join(role_lines)
        block = f"START_SONG_{ordinal}\n{mixed_line}\nEND_SONG\n"
        output_blocks.append(block)
        projected_record = copy.deepcopy(record)
        projected_record.update({
            "condition_id": condition,
            "selected_roles": list(roles),
            "selected_voices": list(roles),
            "role_presence": {
                role: role in roles
                for role in _ALL_ROLES
            },
            "canonical_track_hashes": copy.deepcopy(
                record.get("track_hashes", {})
            ),
            "condition_role_block_hashes": {
                _ROLE_TO_TRACK[role]: hashlib.sha256(
                    blocks[_ROLE_TO_TRACK[role]][ordinal].encode("utf-8")
                ).hexdigest()
                for role in roles
            },
            "condition_score_block_sha256": _sha256_bytes(
                block.encode("utf-8")
            ),
            "condition_omission_reasons": {
                role: "condition_mask"
                for role in _ALL_ROLES
                if role not in roles
            },
        })
        projected_records.append(projected_record)

    output_text = "".join(output_blocks)
    output_path.write_text(output_text, encoding="utf-8")
    canonical_manifest_hash = _sha256_file(canonical_manifest_path)
    projection_config = {
        "condition_id": condition,
        "selected_roles": list(CONDITION_ROLES[condition]),
        "melody_percent": float(selection["melody_percent"]),
        "percussion_percent": float(selection["percussion_percent"]),
        "seed": seed,
        "canonical_manifest_sha256": canonical_manifest_hash,
    }
    manifest = copy.deepcopy(canonical_manifest)
    manifest["canonical_track_outputs"] = copy.deepcopy(
        canonical_manifest.get("track_outputs")
    )
    manifest.pop("track_outputs", None)
    manifest["condition_manifest_version"] = 1
    manifest["condition_id"] = condition
    manifest["selected_roles"] = list(CONDITION_ROLES[condition])
    manifest["selected_voices"] = list(CONDITION_ROLES[condition])
    manifest["condition_projection"] = {
        "source_score": str(canonical_path.resolve()),
        "source_manifest": str(canonical_manifest_path.resolve()),
        "source_manifest_sha256": canonical_manifest_hash,
        "source_role_paths": {
            track: str(
                Path(
                    canonical_manifest["track_outputs"]["paths"][track]
                ).resolve()
            )
            for track in _ROLE_TO_TRACK.values()
        },
        "selection": selection,
        "selected_roles_by_song": [
            list(roles) for roles in selected_roles
        ],
        "generation_reused": True,
        "symbolic_regeneration": False,
    }
    manifest["parameter_manifest"] = copy.deepcopy(
        canonical_manifest.get("parameter_manifest", {})
    )
    manifest["parameter_manifest"]["projection"] = projection_config
    manifest["parameter_manifest"]["effective_config_sha256"] = config_sha256(
        projection_config
    )
    manifest["output"] = str(output_path.resolve())
    manifest["condition_output"] = {
        "path": str(output_path.resolve()),
        "sha256": _sha256_file(output_path),
        "stage": "score",
        "ordinals": list(range(len(records))),
    }
    manifest["records"] = projected_records
    manifest["sound_design"] = copy.deepcopy(
        canonical_manifest.get("sound_design", {})
    )
    manifest["sound_design"]["audio_rendered"] = False
    manifest["sound_design"]["stage"] = "score"
    _write_json(manifest_path, manifest)

    if stage in {"midi", "audio"}:
        from render import render_midi_scores

        midi_output = condition_dir / "midi"
        midi_records = render_midi_scores(
            output_path,
            midi_output,
            seed=seed,
        )
        manifest["sound_design"]["stage"] = "midi"
        manifest["sound_design"]["midi"] = {
            "renderer": "HumanizedMidiRenderer.java",
            "seed_derivation_version": "fnv1a64-v1",
            "records": midi_records,
        }
        manifest["condition_output"]["midi"] = {
            "directory": str(midi_output.resolve()),
            "records": midi_records,
        }
        if stage == "audio":
            if soundfont is None:
                raise ValueError("stage 'audio' requires a SoundFont path")
            from audio_render import render_audio_bundle

            audio = render_audio_bundle(
                manifest,
                {"mixed": midi_records},
                condition_dir / "audio",
                soundfont=soundfont,
                seed=seed,
                fluidsynth_bin=fluidsynth_bin,
                ffmpeg_bin=ffmpeg_bin,
            )
            manifest["sound_design"]["stage"] = "audio"
            manifest["sound_design"]["audio_rendered"] = True
            manifest["sound_design"]["audio"] = audio
            manifest["condition_output"]["stage"] = "audio"
            manifest["condition_output"]["audio"] = {
                "directory": str((condition_dir / "audio").resolve()),
                "records": audio["records"],
            }
            for record in manifest["records"]:
                record["audio_rendered"] = True
                record["audio"] = audio["records"][str(record["ordinal"])]
        _write_json(manifest_path, manifest)
    return output_path


def project_conditions(
    canonical_path: str | Path,
    output_root: str | Path,
    *,
    conditions: Iterable[str] = CONDITION_ROLES,
    manifest_path: str | Path | None = None,
    seed: int | None = None,
    melody_percent: float = 70.0,
    percussion_percent: float = 70.0,
    stage: str = "score",
    soundfont: str | Path | None = None,
    fluidsynth_bin: str = "fluidsynth",
    ffmpeg_bin: str = "ffmpeg",
) -> list[Path]:
    """Project condition score views without calling symbolic generation."""
    canonical_path = Path(canonical_path)
    if not canonical_path.is_file():
        raise FileNotFoundError(f"canonical score is missing: {canonical_path}")
    if manifest_path is None:
        manifest_path = canonical_path.with_name(
            canonical_path.name + ".manifest.json"
        )
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("canonical manifest must be a JSON object")
    records, blocks = _validate_canonical(canonical_path, manifest)
    resolved_seed = manifest.get("seed") if seed is None else seed
    if not isinstance(resolved_seed, int) or isinstance(resolved_seed, bool):
        raise ValueError("canonical manifest or --seed must provide an integer seed")
    selected_conditions = list(conditions)
    if not selected_conditions:
        raise ValueError("at least one condition is required")
    unknown = [condition for condition in selected_conditions if condition not in CONDITION_ROLES]
    if unknown:
        raise ValueError(f"unsupported condition(s): {', '.join(unknown)}")
    if stage not in {"score", "midi", "audio"}:
        raise ValueError("stage must be 'score', 'midi', or 'audio'")
    if stage == "audio":
        if soundfont is None:
            raise ValueError("stage 'audio' requires a SoundFont path")
        if not Path(soundfont).expanduser().is_file():
            raise FileNotFoundError(f"SoundFont is missing: {soundfont}")
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    return [
        _project_one(
            condition=condition,
            canonical_path=canonical_path,
            canonical_manifest_path=manifest_path,
            canonical_manifest=manifest,
            records=records,
            blocks=blocks,
            output_root=output_root,
            seed=resolved_seed,
            melody_percent=melody_percent,
            percussion_percent=percussion_percent,
            stage=stage,
            soundfont=soundfont,
            fluidsynth_bin=fluidsynth_bin,
            ffmpeg_bin=ffmpeg_bin,
        )
        for condition in selected_conditions
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", required=True, help="Canonical all-role score.")
    parser.add_argument(
        "--manifest",
        help="Canonical manifest; defaults to <canonical>.manifest.json.",
    )
    parser.add_argument("--out-dir", required=True, help="Condition output directory.")
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=tuple(CONDITION_ROLES),
        default=list(CONDITION_ROLES),
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument("--melody-percent", type=float, default=70.0)
    parser.add_argument("--percussion-percent", type=float, default=70.0)
    parser.add_argument(
        "--stage",
        choices=("score", "midi", "audio"),
        default="score",
        help="Project scores, convert to MIDI, or render MIDI to audio.",
    )
    parser.add_argument(
        "--soundfont",
        help="SoundFont path required when --stage audio is selected.",
    )
    parser.add_argument(
        "--fluidsynth",
        default="fluidsynth",
        help="FluidSynth executable or full path.",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="ffmpeg executable or full path used for encoding and mixing.",
    )
    args = parser.parse_args()
    outputs = project_conditions(
        args.canonical,
        args.out_dir,
        conditions=args.conditions,
        manifest_path=args.manifest,
        seed=args.seed,
        melody_percent=args.melody_percent,
        percussion_percent=args.percussion_percent,
        stage=args.stage,
        soundfont=args.soundfont,
        fluidsynth_bin=args.fluidsynth,
        ffmpeg_bin=args.ffmpeg,
    )
    for output in outputs:
        print(f"Projected condition score: {output}")


if __name__ == "__main__":
    main()
