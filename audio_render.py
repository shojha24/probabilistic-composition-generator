"""Headless MIDI-to-audio rendering and role-aware mixing.

The audio stage deliberately keeps the synthesizer and mixer external. This
allows the repository to pin their executable names and input/output contract
without embedding a platform-specific audio engine.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from sound_design import (
    REFERENCE_RENDER_PROFILE,
    SEED_DERIVATION_VERSION,
    derive_seed,
    resolve_soundfonts,
    sample_detune_for_ordinal,
    sample_eq_tilt_for_ordinal,
    sample_gain_jitter_for_ordinal,
    sample_soundfont_for_ordinal,
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
    if result["channels"] not in (1, 2):
        raise ValueError("audio render profile channels must be 1 (mono) or 2 (stereo)")
    if result["bit_depth"] not in (16, 24):
        raise ValueError("audio render profile bit_depth must be 16 or 24")
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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        check=True,
    )


def _encode_flac(
    executable: str,
    wav_path: Path,
    flac_path: Path,
    profile: Mapping[str, Any],
) -> None:
    flac_path.parent.mkdir(parents=True, exist_ok=True)
    sample_fmt = "s16" if profile["bit_depth"] == 16 else "s32"
    subprocess.run(
        [
            executable,
            "-nostdin",
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
            sample_fmt,
            "-bits_per_raw_sample",
            str(profile["bit_depth"]),
            str(flac_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        check=True,
    )


def _generate_synthetic_rir(
    reverb_type: str,
    sample_rate_hz: int,
    out_dir: Path,
) -> Path:
    """Deterministically synthesize a room impulse response WAV file.

    Generates exponential decay noise e^{-t/tau} matching studio room acoustics.
    """
    import math
    import wave

    out_dir.mkdir(parents=True, exist_ok=True)
    rir_path = out_dir / f"rir_{reverb_type}_{sample_rate_hz}.wav"
    if rir_path.is_file():
        return rir_path

    # Duration and decay constants
    if reverb_type == "hall":
        duration_s = 1.2
        tau = 0.25
    elif reverb_type == "plate":
        duration_s = 0.8
        tau = 0.15
    else:  # room / studio default
        duration_s = 0.4
        tau = 0.08

    num_samples = int(duration_s * sample_rate_hz)
    # Deterministic pseudo-random sequence for RIR
    a = 1664525
    c = 1013904223
    m = 2**32
    state = 123456789

    samples = bytearray()
    for n in range(num_samples):
        state = (a * state + c) % m
        noise = (state / m) * 2.0 - 1.0
        t = n / sample_rate_hz
        envelope = math.exp(-t / tau)
        val = int(noise * envelope * 0.7 * 32767)
        val = max(-32768, min(32767, val))
        samples.extend(val.to_bytes(2, byteorder="little", signed=True))

    with wave.open(str(rir_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate_hz)
        wf.writeframes(samples)

    return rir_path


def _mix_flac(
    executable: str,
    wav_paths: list[tuple[str, Path]],
    flac_path: Path,
    profile: Mapping[str, Any],
    *,
    reverb: str | None = None,
    detune_cents: float = 0.0,
    production_fx: bool = False,
    eq_tilt_db: float = 0.0,
    temp_dir: Path | None = None,
) -> None:
    if not wav_paths:
        raise ValueError("cannot mix an empty role selection")

    input_args: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    gains = profile["role_gains_db"]
    sample_rate = profile["sample_rate_hz"]

    for index, (track, wav_path) in enumerate(wav_paths):
        input_args.extend(["-i", str(wav_path)])
        label = f"role{index}"
        role = TRACK_TO_ROLE[track]
        gain = float(gains[role])
        filters.append(
            f"[{index}:a]volume={gain:g}dB[{label}]"
        )
        labels.append(f"[{label}]")

    loudness = (
        f"loudnorm=I={float(profile['loudness_target_lufs']):g}:"
        f"TP={float(profile['true_peak_ceiling_dbtp']):g}:"
        "LRA=7:print_format=none"
    )

    if len(wav_paths) == 1:
        mix_label = labels[0]
    else:
        filters.append(
            "".join(labels)
            + (
                f"amix=inputs={len(wav_paths)}:duration=longest:"
                "dropout_transition=0:normalize=0[summed]"
            )
        )
        mix_label = "[summed]"

    current_label = mix_label

    # Studio convolution reverb via afir filter
    if reverb and reverb.lower() not in ("none", "off", "false"):
        rir_dir = temp_dir if temp_dir is not None else flac_path.parent
        rir_path = _generate_synthetic_rir(reverb.lower(), sample_rate, rir_dir)
        rir_index = len(wav_paths)
        input_args.extend(["-i", str(rir_path)])
        # asplit to dry and wet paths, convolve wet with RIR, then blend
        filters.append(
            f"{current_label}asplit[dry_sig][to_rev];"
            f"[to_rev][{rir_index}:a]afir=dry=0:wet=1[wet_sig];"
            f"[dry_sig][wet_sig]amix=inputs=2:weights=0.82 0.18[reverbed]"
        )
        current_label = "[reverbed]"

    # Micro-tuning pitch drift via asetrate & aresample
    if abs(detune_cents) > 0.001:
        factor = 2.0 ** (detune_cents / 1200.0)
        target_rate = int(round(sample_rate * factor))
        filters.append(
            f"{current_label}asetrate={target_rate},aresample={sample_rate}[detuned]"
        )
        current_label = "[detuned]"

    # Studio production FX (Spec 17 §4: EQ tilt, tape saturation, dynamic bus compand)
    if production_fx:
        if abs(eq_tilt_db) > 0.01:
            filters.append(
                f"{current_label}lowshelf=f=1500:g={eq_tilt_db:g}:t=q,highshelf=f=1500:g={-eq_tilt_db:g}:t=q[eq_tilted]"
            )
            current_label = "[eq_tilted]"
        filters.append(
            f"{current_label}aeval='val(0)-0.1*val(0)*val(0)*val(0)|val(1)-0.1*val(1)*val(1)*val(1)'[saturated]"
        )
        current_label = "[saturated]"
        filters.append(
            f"{current_label}compand=attacks=0.02:decays=0.15:points=-80/-80|-24/-24|-12/-8|0/-3[compressed]"
        )
        current_label = "[compressed]"

    # Final loudness normalization
    filters.append(f"{current_label}{loudness}[mix]")

    flac_path.parent.mkdir(parents=True, exist_ok=True)
    sample_fmt = "s16" if profile["bit_depth"] == 16 else "s32"
    subprocess.run(
        [
            executable,
            "-nostdin",
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
            sample_fmt,
            "-bits_per_raw_sample",
            str(profile["bit_depth"]),
            str(flac_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
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

    known_tracks = set(ROLE_TO_TRACK.values()) | {"mixed"}
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


def _render_song_audio(
    ordinal: int,
    selected_roles: tuple[str, ...],
    role_records: Mapping[str, Any],
    mixed_records: Mapping[str, Any] | None,
    root: Path,
    fluidsynth: str,
    ffmpeg: str,
    soundfont_path: Path,
    effective_profile: Mapping[str, Any],
    seed: int,
    render_mix: bool = True,
    gain_jitter_db: float = 0.0,
    reverb: str | None = None,
    detune_cents: float = 0.0,
    production_fx: bool = False,
) -> tuple[str, dict[str, Any]]:
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

        mix_record = None
        # Per-song dynamic gain jitter
        song_profile = dict(effective_profile)
        song_gains = dict(effective_profile["role_gains_db"])
        jitter = sample_gain_jitter_for_ordinal(seed, ordinal, gain_jitter_db)
        for role, delta in jitter.items():
            if role in song_gains:
                song_gains[role] = float(song_gains[role]) + delta
        song_profile["role_gains_db"] = song_gains

        # Per-song detune cents
        song_detune = sample_detune_for_ordinal(seed, ordinal, detune_cents) if detune_cents > 0.0 else 0.0
        song_eq_tilt = sample_eq_tilt_for_ordinal(seed, ordinal) if production_fx else 0.0

        if render_mix:
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
                if mixed_records is None:
                    raise ValueError("audio rendering requires mixed MIDI records")
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
            mix_kwargs: dict[str, Any] = {}
            if reverb is not None:
                mix_kwargs["reverb"] = reverb
            if abs(song_detune) > 0.001:
                mix_kwargs["detune_cents"] = song_detune
            if production_fx:
                mix_kwargs["production_fx"] = True
                mix_kwargs["eq_tilt_db"] = song_eq_tilt
            if temporary_dir is not None and (reverb is not None or abs(song_detune) > 0.001 or production_fx):
                mix_kwargs["temp_dir"] = temporary_dir
            _mix_flac(ffmpeg, mix_inputs, mix_path, song_profile, **mix_kwargs)
            mix_record = _file_record(mix_path)

        result_payload: dict[str, Any] = {
            "render_seed": derive_seed(seed, "audio-render", ordinal),
            "selected_roles": list(selected_roles),
            "stems": stem_records,
            "soundfont": soundfont_asset_manifest(soundfont_path),
            "format": effective_profile["format"],
            "sample_rate_hz": effective_profile["sample_rate_hz"],
            "bit_depth": effective_profile["bit_depth"],
            "channels": effective_profile["channels"],
            "tail_policy": effective_profile["tail_policy"],
            "role_gains_db": {
                role: song_gains[role]
                for role in selected_roles
            },
            "gain_jitter_applied_db": {
                role: jitter[role]
                for role in selected_roles
            },
            "detune_cents_applied": song_detune,
            "reverb_applied": reverb,
            "production_fx_applied": production_fx,
            "eq_tilt_db_applied": song_eq_tilt,
        }
        if mix_record is not None:
            result_payload["mix"] = mix_record
        return str(ordinal), result_payload
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=False)


def _render_song_audio_worker(args: tuple) -> tuple[str, dict[str, Any]]:
    return _render_song_audio(*args)


def render_audio_bundle(
    manifest: Mapping[str, Any],
    midi_records: Mapping[str, Any],
    output_dir: str | Path,
    *,
    soundfont: str | Path | None = None,
    soundfonts: list[str | Path] | None = None,
    soundfont_dir: str | Path | None = None,
    seed: int,
    fluidsynth_bin: str = "fluidsynth",
    ffmpeg_bin: str = "ffmpeg",
    profile: Mapping[str, Any] = REFERENCE_RENDER_PROFILE,
    render_mix: bool = True,
    gain_jitter_db: float = 0.0,
    reverb: str | None = None,
    detune_cents: float = 0.0,
    production_fx: bool = False,
) -> dict[str, Any]:
    """Render role stems and full mixes from a MIDI manifest."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("audio rendering seed must be an integer")
    effective_profile = _validate_profile(profile)

    available_soundfonts = resolve_soundfonts(
        soundfonts=soundfonts,
        soundfont_dir=soundfont_dir,
        soundfont=soundfont,
    )
    if not available_soundfonts:
        raise ValueError("at least one SoundFont is required for audio rendering")

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

    song_args = []
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            raise ValueError("manifest records must contain objects")
        ordinal = raw_record.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise ValueError("manifest record ordinal must be an integer")
        selected_roles = _selected_roles(raw_record, manifest)
        song_sf = sample_soundfont_for_ordinal(available_soundfonts, seed, ordinal)
        song_args.append((
            ordinal,
            selected_roles,
            role_records,
            mixed_records,
            root,
            fluidsynth,
            ffmpeg,
            song_sf,
            effective_profile,
            seed,
            render_mix,
            gain_jitter_db,
            reverb,
            detune_cents,
            production_fx,
        ))

    total = len(records)
    log_interval = max(10, total // 20) if total > 20 else max(1, total)
    completed = 0
    workers = min(14, total, os.cpu_count() or 1)
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for ordinal_key, record in executor.map(
                _render_song_audio_worker, song_args
            ):
                audio_records[ordinal_key] = record
                completed += 1
                if completed % log_interval == 0 or completed == total:
                    print(
                        f"  [audio:render] {completed}/{total} songs synthesized ({completed/total*100:.1f}%)",
                        flush=True,
                    )
    else:
        for args in song_args:
            ordinal_key, record = _render_song_audio(*args)
            audio_records[ordinal_key] = record
            completed += 1
            if completed % log_interval == 0 or completed == total:
                print(
                    f"  [audio:render] {completed}/{total} songs synthesized ({completed/total*100:.1f}%)",
                    flush=True,
                )

    assets = [soundfont_asset_manifest(sf) for sf in available_soundfonts]
    mixer_graph = "role_gain -> amix"
    if reverb and reverb.lower() not in ("none", "off", "false"):
        mixer_graph += " -> afir"
    if abs(detune_cents) > 0.001:
        mixer_graph += " -> asetrate/aresample"
    if production_fx:
        mixer_graph += " -> eq_tilt -> tape_sat -> compand"
    mixer_graph += " -> loudnorm"

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
            "graph": mixer_graph,
        },
        "asset": assets[0] if len(assets) == 1 else assets,
        "available_soundfonts": [str(sf) for sf in available_soundfonts],
        "profile": effective_profile,
        "profile_id": effective_profile["profile_id"],
        "seed_derivation_version": SEED_DERIVATION_VERSION,
        "output_dir": str(root),
        "gain_jitter_db": gain_jitter_db,
        "reverb": reverb,
        "detune_cents": detune_cents,
        "production_fx": production_fx,
        "records": audio_records,
    }


def _mix_song_from_stems(
    ordinal: int,
    selected_roles: tuple[str, ...],
    stem_paths: Mapping[str, Path],
    song_dir: Path,
    ffmpeg: str,
    effective_profile: Mapping[str, Any],
    seed: int,
    gain_jitter_db: float = 0.0,
    reverb: str | None = None,
    detune_cents: float = 0.0,
    production_fx: bool = False,
) -> tuple[str, dict[str, Any]]:
    song_dir.mkdir(parents=True, exist_ok=True)
    selected_tracks = [
        ROLE_TO_TRACK[role]
        for role in selected_roles
    ]
    missing = [
        track for track in selected_tracks
        if track not in stem_paths or not Path(stem_paths[track]).is_file()
    ]
    if missing:
        raise ValueError(
            f"selected roles have no stem audio for song {ordinal}: {missing}"
        )
    mix_inputs = [
        (track, Path(stem_paths[track]))
        for track in selected_tracks
    ]
    song_profile = dict(effective_profile)
    song_gains = dict(effective_profile["role_gains_db"])
    jitter = sample_gain_jitter_for_ordinal(seed, ordinal, gain_jitter_db)
    for role, delta in jitter.items():
        if role in song_gains:
            song_gains[role] = float(song_gains[role]) + delta
    song_profile["role_gains_db"] = song_gains
    song_detune = sample_detune_for_ordinal(seed, ordinal, detune_cents) if detune_cents > 0.0 else 0.0
    song_eq_tilt = sample_eq_tilt_for_ordinal(seed, ordinal) if production_fx else 0.0

    mix_path = song_dir / "mix.flac"
    mix_kwargs: dict[str, Any] = {}
    if reverb is not None:
        mix_kwargs["reverb"] = reverb
    if abs(song_detune) > 0.001:
        mix_kwargs["detune_cents"] = song_detune
    if production_fx:
        mix_kwargs["production_fx"] = True
        mix_kwargs["eq_tilt_db"] = song_eq_tilt
    _mix_flac(ffmpeg, mix_inputs, mix_path, song_profile, **mix_kwargs)
    stem_records = {
        track: _file_record(Path(stem_paths[track]))
        for track in selected_tracks
    }
    return str(ordinal), {
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
            role: song_gains[role]
            for role in selected_roles
        },
        "gain_jitter_applied_db": {
            role: jitter[role]
            for role in selected_roles
        },
        "detune_cents_applied": song_detune,
        "reverb_applied": reverb,
        "production_fx_applied": production_fx,
        "eq_tilt_db_applied": song_eq_tilt,
    }


def _mix_song_from_stems_worker(args: tuple) -> tuple[str, dict[str, Any]]:
    return _mix_song_from_stems(*args)


def mix_audio_bundle_from_stems(
    manifest: Mapping[str, Any],
    stems_records: Mapping[str, Mapping[str, Any]],
    output_dir: str | Path,
    *,
    seed: int,
    ffmpeg_bin: str = "ffmpeg",
    profile: Mapping[str, Any] = REFERENCE_RENDER_PROFILE,
    renderer_info: Mapping[str, Any] | None = None,
    asset_info: Mapping[str, Any] | None = None,
    gain_jitter_db: float = 0.0,
    reverb: str | None = None,
    detune_cents: float = 0.0,
    production_fx: bool = False,
) -> dict[str, Any]:
    """Mix pre-rendered role stems into full condition mixes."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("audio rendering seed must be an integer")
    effective_profile = _validate_profile(profile)
    ffmpeg = _resolve_executable(ffmpeg_bin, "ffmpeg")
    ffmpeg_version = _tool_version(ffmpeg, "-version")

    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("audio mixing requires non-empty manifest records")

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    audio_records: dict[str, Any] = {}

    song_args = []
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            raise ValueError("manifest records must contain objects")
        ordinal = raw_record.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise ValueError("manifest record ordinal must be an integer")
        selected_roles = _selected_roles(raw_record, manifest)
        ord_str = str(ordinal)

        stem_paths_for_ordinal: dict[str, Path] = {}
        for track in ROLE_TO_TRACK.values():
            val = None
            if ord_str in stems_records and isinstance(stems_records[ord_str], Mapping):
                val = stems_records[ord_str].get(track)
            elif track in stems_records and isinstance(stems_records[track], Mapping):
                val = stems_records[track].get(ord_str)
            if val is not None:
                if isinstance(val, Mapping) and "path" in val:
                    stem_paths_for_ordinal[track] = Path(val["path"])
                elif isinstance(val, (str, Path)):
                    stem_paths_for_ordinal[track] = Path(val)

        song_dir = root / f"song_{ordinal}"
        song_args.append((
            ordinal,
            selected_roles,
            stem_paths_for_ordinal,
            song_dir,
            ffmpeg,
            effective_profile,
            seed,
            gain_jitter_db,
            reverb,
            detune_cents,
            production_fx,
        ))

    total = len(records)
    log_interval = max(10, total // 20) if total > 20 else max(1, total)
    completed = 0
    workers = min(14, total, os.cpu_count() or 1)
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for ordinal_key, record in executor.map(
                _mix_song_from_stems_worker, song_args
            ):
                audio_records[ordinal_key] = record
                completed += 1
                if completed % log_interval == 0 or completed == total:
                    print(
                        f"  [audio:mix] {completed}/{total} songs mixed ({completed/total*100:.1f}%)",
                        flush=True,
                    )
    else:
        for args in song_args:
            ordinal_key, record = _mix_song_from_stems(*args)
            audio_records[ordinal_key] = record
            completed += 1
            if completed % log_interval == 0 or completed == total:
                print(
                    f"  [audio:mix] {completed}/{total} songs mixed ({completed/total*100:.1f}%)",
                    flush=True,
                )

    mixer_graph = "role_gain -> amix"
    if reverb and reverb.lower() not in ("none", "off", "false"):
        mixer_graph += " -> afir"
    if abs(detune_cents) > 0.001:
        mixer_graph += " -> asetrate/aresample"
    if production_fx:
        mixer_graph += " -> eq_tilt -> tape_sat -> compand"
    mixer_graph += " -> loudnorm"

    result = {
        "mixer": {
            "engine": "ffmpeg",
            "executable": ffmpeg,
            "version": ffmpeg_version,
            "graph": mixer_graph,
        },
        "profile": effective_profile,
        "profile_id": effective_profile["profile_id"],
        "seed_derivation_version": SEED_DERIVATION_VERSION,
        "output_dir": str(root),
        "gain_jitter_db": gain_jitter_db,
        "reverb": reverb,
        "detune_cents": detune_cents,
        "production_fx": production_fx,
        "records": audio_records,
    }
    if renderer_info is not None:
        if isinstance(renderer_info, Mapping):
            result["renderer"] = dict(renderer_info)
        elif isinstance(renderer_info, list):
            result["renderer"] = [dict(r) if isinstance(r, Mapping) else r for r in renderer_info]
        else:
            result["renderer"] = renderer_info
    else:
        result["renderer"] = {
            "engine": "FluidSynth",
            "reused_stems": True,
        }
    if asset_info is not None:
        if isinstance(asset_info, Mapping):
            result["asset"] = dict(asset_info)
        elif isinstance(asset_info, list):
            result["asset"] = [dict(a) if isinstance(a, Mapping) else a for a in asset_info]
        else:
            result["asset"] = asset_info
    return result


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
    parser.add_argument("--soundfont", default=None)
    parser.add_argument("--soundfonts", nargs="*", default=None)
    parser.add_argument("--soundfont-dir", default=None)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--fluidsynth", default="fluidsynth")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--gain-jitter-db", type=float, default=0.0)
    parser.add_argument("--reverb", default=None, help="Convolution reverb type ('studio', 'room', 'hall', 'plate')")
    parser.add_argument("--detune-cents", type=float, default=0.0)
    parser.add_argument(
        "--no-mix",
        action="store_true",
        help="Skip rendering full mix.flac (stems only).",
    )
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
        soundfonts=args.soundfonts,
        soundfont_dir=args.soundfont_dir,
        seed=seed,
        fluidsynth_bin=args.fluidsynth,
        ffmpeg_bin=args.ffmpeg,
        render_mix=not args.no_mix,
        gain_jitter_db=args.gain_jitter_db,
        reverb=args.reverb,
        detune_cents=args.detune_cents,
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
