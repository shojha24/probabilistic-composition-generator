"""Headless MIDI-to-audio rendering and role-aware mixing.

The audio stage deliberately keeps the synthesizer and mixer external. This
allows the repository to pin their executable names and input/output contract
without embedding a platform-specific audio engine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from sound_design import (
    REFERENCE_RENDER_PROFILE,
    SEED_DERIVATION_VERSION,
    derive_seed,
    soundfont_asset_manifest,
)


ROLE_TO_TRACK = {
    "V0": "chords",
    "V1": "bass",
    "V2": "melody",
    "V9": "percussion",
}
TRACK_TO_ROLE = {track: role for role, track in ROLE_TO_TRACK.items()}


def _validate_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(profile)
    if result.get("format") != "flac":
        raise ValueError("audio render profile format must be 'flac'")
    for key in ("sample_rate_hz", "bit_depth", "channels"):
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"audio render profile {key} must be positive")
    if result["channels"] != 2:
        raise ValueError("audio render profile must use stereo output")
    gains = result.get("role_gains_db")
    if not isinstance(gains, Mapping):
        raise ValueError("audio render profile must define role_gains_db")
    for role in ROLE_TO_TRACK:
        value = gains.get(role)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"missing numeric gain for {role}")
    for key in ("loudness_target_lufs", "true_peak_ceiling_dbtp"):
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"audio render profile {key} must be numeric")
    return result


def _resolve_executable(binary: str, label: str) -> str:
    resolved = shutil.which(binary)
    if resolved is None:
        raise FileNotFoundError(
            f"{label} executable was not found: {binary!r}. "
            f"Install {label} and add it to PATH, or pass its full path."
        )
    return resolved


def _tool_version(executable: str, argument: str) -> str:
    result = subprocess.run(
        [executable, argument],
        check=True,
        capture_output=True,
        text=True,
    )
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if output else "unknown"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_record(path: Path) -> dict[str, str]:
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_file(path),
    }


def _render_midi_to_wav(
    executable: str,
    midi_path: Path,
    wav_path: Path,
    soundfont: Path,
    sample_rate_hz: int,
) -> None:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            executable,
            "-ni",
            "-F",
            str(wav_path),
            "-r",
            str(sample_rate_hz),
            str(soundfont),
            str(midi_path),
        ],
        check=True,
    )


def _encode_flac(
    executable: str,
    wav_path: Path,
    flac_path: Path,
    profile: Mapping[str, Any],
) -> None:
    flac_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav_path),
            "-ar",
            str(profile["sample_rate_hz"]),
            "-ac",
            str(profile["channels"]),
            "-c:a",
            "flac",
            "-sample_fmt",
            "s32",
            "-bits_per_raw_sample",
            str(profile["bit_depth"]),
            str(flac_path),
        ],
        check=True,
    )


def _mix_flac(
    executable: str,
    wav_paths: list[tuple[str, Path]],
    flac_path: Path,
    profile: Mapping[str, Any],
) -> None:
    if not wav_paths:
        raise ValueError("cannot mix an empty role selection")

    input_args: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    gains = profile["role_gains_db"]
    for index, (track, wav_path) in enumerate(wav_paths):
        input_args.extend(["-i", str(wav_path)])
        label = f"role{index}"
        role = TRACK_TO_ROLE[track]
        filters.append(
            f"[{index}:a]volume={float(gains[role]):g}dB[{label}]"
        )
        labels.append(f"[{label}]")

    loudness = (
        f"loudnorm=I={float(profile['loudness_target_lufs']):g}:"
        f"TP={float(profile['true_peak_ceiling_dbtp']):g}:"
        "LRA=7:print_format=none"
    )
    filters.append(
        "".join(labels)
        + (
            f"amix=inputs={len(wav_paths)}:duration=longest:"
            "dropout_transition=0:normalize=0,"
            f"{loudness}[mix]"
        )
    )
    flac_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *input_args,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[mix]",
            "-ar",
            str(profile["sample_rate_hz"]),
            "-ac",
            str(profile["channels"]),
            "-c:a",
            "flac",
            "-sample_fmt",
            "s32",
            "-bits_per_raw_sample",
            str(profile["bit_depth"]),
            str(flac_path),
        ],
        check=True,
    )


def _normalize_midi_records(
    midi_records: Mapping[str, Any],
) -> dict[str, Mapping[str, Mapping[str, str]]]:
    nested = midi_records.get("roles")
    if isinstance(nested, Mapping):
        result = {
            str(key): value
            for key, value in nested.items()
            if isinstance(value, Mapping)
        }
        mixed = midi_records.get("mixed")
        if isinstance(mixed, Mapping):
            result["mixed"] = mixed
        return result

    known_tracks = set(ROLE_TO_TRACK.values())
    if any(key in known_tracks for key in midi_records):
        return {
            str(key): value
            for key, value in midi_records.items()
            if isinstance(value, Mapping)
        }
    return {"mixed": midi_records}


def _midi_record(
    records: Mapping[str, Mapping[str, str]],
    ordinal: int,
    track: str,
) -> Path:
    value = records.get(str(ordinal))
    if not isinstance(value, Mapping) or not isinstance(value.get("path"), str):
        raise ValueError(
            f"MIDI records for {track} are missing song ordinal {ordinal}"
        )
    path = Path(value["path"])
    if not path.is_file():
        raise FileNotFoundError(f"MIDI file is missing: {path}")
    return path


def _selected_roles(
    record: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> tuple[str, ...]:
    selected = record.get("selected_roles")
    if not isinstance(selected, (list, tuple)):
        selected = manifest.get("selected_roles")
    if not isinstance(selected, (list, tuple)):
        selected = tuple(ROLE_TO_TRACK)
    result = tuple(str(role) for role in selected)
    unknown = set(result) - set(ROLE_TO_TRACK)
    if unknown:
        raise ValueError(
            f"manifest contains unsupported audio role(s): {sorted(unknown)}"
        )
    if not result:
        raise ValueError("manifest selected_roles cannot be empty")
    return result


def render_audio_bundle(
    manifest: Mapping[str, Any],
    midi_records: Mapping[str, Any],
    output_dir: str | Path,
    *,
    soundfont: str | Path,
    seed: int,
    fluidsynth_bin: str = "fluidsynth",
    ffmpeg_bin: str = "ffmpeg",
    profile: Mapping[str, Any] = REFERENCE_RENDER_PROFILE,
) -> dict[str, Any]:
    """Render role stems and full mixes from a MIDI manifest."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("audio rendering seed must be an integer")
    effective_profile = _validate_profile(profile)
    soundfont_path = Path(soundfont).expanduser().resolve()
    asset = soundfont_asset_manifest(soundfont_path)
    fluidsynth = _resolve_executable(fluidsynth_bin, "FluidSynth")
    ffmpeg = _resolve_executable(ffmpeg_bin, "ffmpeg")
    fluidsynth_version = _tool_version(fluidsynth, "--version")
    ffmpeg_version = _tool_version(ffmpeg, "-version")

    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("audio rendering requires non-empty manifest records")
    normalized = _normalize_midi_records(midi_records)
    role_records = {
        track: value
        for track, value in normalized.items()
        if track in TRACK_TO_ROLE
    }
    mixed_records = normalized.get("mixed")
    if not role_records and not isinstance(mixed_records, Mapping):
        raise ValueError("audio rendering requires role or mixed MIDI records")

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    audio_records: dict[str, Any] = {}

    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            raise ValueError("manifest records must contain objects")
        ordinal = raw_record.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise ValueError("manifest record ordinal must be an integer")
        selected_roles = _selected_roles(raw_record, manifest)
        song_dir = root / f"song_{ordinal}"
        song_dir.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(
            tempfile.mkdtemp(prefix=f".song_{ordinal}.", dir=root)
        )
        stem_wavs: dict[str, Path] = {}
        stem_records: dict[str, dict[str, str]] = {}
        try:
            for track, track_records in sorted(role_records.items()):
                midi_path = _midi_record(track_records, ordinal, track)
                wav_path = temporary_dir / f"{track}.wav"
                _render_midi_to_wav(
                    fluidsynth,
                    midi_path,
                    wav_path,
                    soundfont_path,
                    effective_profile["sample_rate_hz"],
                )
                stem_wavs[track] = wav_path
                stem_path = song_dir / f"{track}.flac"
                _encode_flac(ffmpeg, wav_path, stem_path, effective_profile)
                stem_records[track] = _file_record(stem_path)

            if role_records:
                selected_tracks = [
                    ROLE_TO_TRACK[role]
                    for role in selected_roles
                ]
                missing = [
                    track for track in selected_tracks
                    if track not in stem_wavs
                ]
                if missing:
                    raise ValueError(
                        f"selected roles have no MIDI stems for song "
                        f"{ordinal}: {missing}"
                    )
                mix_inputs = [
                    (track, stem_wavs[track])
                    for track in selected_tracks
                ]
            else:
                mixed_path = _midi_record(mixed_records, ordinal, "mixed")
                mixed_wav = temporary_dir / "mixed.wav"
                _render_midi_to_wav(
                    fluidsynth,
                    mixed_path,
                    mixed_wav,
                    soundfont_path,
                    effective_profile["sample_rate_hz"],
                )
                mix_inputs = [("chords", mixed_wav)]

            mix_path = song_dir / "mix.flac"
            _mix_flac(ffmpeg, mix_inputs, mix_path, effective_profile)
            audio_records[str(ordinal)] = {
                "render_seed": derive_seed(seed, "audio-render", ordinal),
                "selected_roles": list(selected_roles),
                "stems": stem_records,
                "mix": _file_record(mix_path),
                "format": effective_profile["format"],
                "sample_rate_hz": effective_profile["sample_rate_hz"],
                "bit_depth": effective_profile["bit_depth"],
                "channels": effective_profile["channels"],
                "tail_policy": effective_profile["tail_policy"],
                "role_gains_db": {
                    role: effective_profile["role_gains_db"][role]
                    for role in selected_roles
                },
            }
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=False)

    return {
        "renderer": {
            "engine": "FluidSynth",
            "executable": fluidsynth,
            "version": fluidsynth_version,
        },
        "mixer": {
            "engine": "ffmpeg",
            "executable": ffmpeg,
            "version": ffmpeg_version,
            "graph": "role_gain -> amix -> loudnorm",
        },
        "asset": asset,
        "profile": effective_profile,
        "profile_id": effective_profile["profile_id"],
        "seed_derivation_version": SEED_DERIVATION_VERSION,
        "output_dir": str(root),
        "records": audio_records,
    }


def _load_midi_records(
    manifest: Mapping[str, Any],
    midi_dir: Path,
) -> dict[str, Any]:
    sound_design = manifest.get("sound_design", {})
    midi = sound_design.get("midi", {}) if isinstance(sound_design, Mapping) else {}
    if isinstance(midi, Mapping):
        roles = midi.get("roles")
        if isinstance(roles, Mapping):
            result: dict[str, Any] = {
                str(key): value for key, value in roles.items()
            }
            if isinstance(midi.get("records"), Mapping):
                result["mixed"] = midi["records"]
            return result
        records = midi.get("records")
        if isinstance(records, Mapping):
            return {"mixed": records}

    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("manifest has no records")
    mixed: dict[str, dict[str, str]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("ordinal"), int):
            raise ValueError("manifest records must contain integer ordinals")
        ordinal = record["ordinal"]
        path = midi_dir / f"START_SONG_{ordinal}.mid"
        mixed[str(ordinal)] = {"path": str(path.resolve())}
    return {"mixed": mixed}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render MIDI stems and mixes with FluidSynth and ffmpeg."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--midi-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--soundfont", required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--fluidsynth", default="fluidsynth")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seed = manifest.get("seed") if args.seed is None else args.seed
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("audio rendering requires an integer seed")
    midi_records = _load_midi_records(manifest, Path(args.midi_dir))
    audio = render_audio_bundle(
        manifest,
        midi_records,
        args.output_dir,
        soundfont=args.soundfont,
        seed=seed,
        fluidsynth_bin=args.fluidsynth,
        ffmpeg_bin=args.ffmpeg,
    )
    manifest["sound_design"] = dict(manifest.get("sound_design", {}))
    manifest["sound_design"]["audio_rendered"] = True
    manifest["sound_design"]["stage"] = "audio"
    manifest["sound_design"]["audio"] = audio
    for record in manifest["records"]:
        ordinal = str(record["ordinal"])
        record["audio_rendered"] = True
        record["audio"] = audio["records"][ordinal]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
