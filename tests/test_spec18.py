"""Unit and integration tests for Spec 18:
Bass Acoustic Fidelity, Low-End Mixing Dynamics, and Corpus Scale Optimization.
"""
from __future__ import annotations

import os
import random
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest

from audio_render import _mix_flac, _validate_profile
from bass_module import BassModule, resolve_bass_pitch, slice_chord_events
from instruments import (
    BASS_INSTRUMENTS,
    BASS_INSTRUMENT_WEIGHTS,
    sample_bass_instrument,
)
from percussion_module import PercussionModule
from sound_design import REFERENCE_RENDER_PROFILE
from target_corpus_gen import GenerationQuota, TargetChordGenerator
from voicing.types import ChordEvent


def test_spec18_render_profile_rebalancing():
    """Verify stem level gains are rebalanced per Spec 18 §2.3."""
    gains = REFERENCE_RENDER_PROFILE["role_gains_db"]
    assert gains["V0"] == 0.0
    assert gains["V1"] == 1.0   # Reduced from +3.0 dB to +1.0 dB
    assert gains["V2"] == -1.0
    assert gains["V9"] == -1.5  # Increased from -6.0 dB to -1.5 dB (stronger kick masking)

    validated = _validate_profile(REFERENCE_RENDER_PROFILE)
    assert validated["role_gains_db"]["V1"] == 1.0
    assert validated["role_gains_db"]["V9"] == -1.5


def test_spec18_v1_filter_and_split_band_reverb():
    """Verify FFmpeg filter graph includes V1 amp drive + cabinet filter and split-band 150 Hz reverb highpass."""
    captured = {}

    def fake_run(command, *args, **kwargs):
        captured["command"] = command

    import audio_render
    orig_run = audio_render.subprocess.run
    audio_render.subprocess.run = fake_run

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            wav_paths = [
                ("chords", tmp_path / "chords.wav"),
                ("bass", tmp_path / "bass.wav"),
                ("percussion", tmp_path / "percussion.wav"),
            ]
            flac_path = tmp_path / "mix.flac"

            _mix_flac(
                "ffmpeg",
                wav_paths,
                flac_path,
                REFERENCE_RENDER_PROFILE,
                reverb="studio",
                temp_dir=tmp_path,
            )

            cmd_str = " ".join(captured["command"])

            # 1. V1 bass filter before summing: soft cubic saturation, lowshelf warmth, lowpass 3500Hz
            assert "val(0)-0.05*val(0)*val(0)*val(0)" in cmd_str
            assert "lowshelf=f=120:g=2" in cmd_str
            assert "lowpass=f=3500" in cmd_str

            # 2. Split-band reverb: highpass=f=150 on the wet path before afir convolution
            assert "highpass=f=150" in cmd_str
            assert "afir=" in cmd_str
            assert "amix=inputs=2:weights=0.82 0.18" in cmd_str

            # 3. Stem gains
            assert "volume=1dB" in cmd_str
            assert "volume=-1.5dB" in cmd_str

    finally:
        audio_render.subprocess.run = orig_run


def test_spec18_stereo_spatialization():
    """Verify stereo panning (+-20% for V0/V9, center for V1/V2) when channels == 2 (Spec 18 §2.4)."""
    captured = {}

    def fake_run(command, *args, **kwargs):
        captured["command"] = command

    import audio_render
    orig_run = audio_render.subprocess.run
    audio_render.subprocess.run = fake_run

    stereo_profile = dict(REFERENCE_RENDER_PROFILE)
    stereo_profile["channels"] = 2

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            wav_paths = [
                ("chords", tmp_path / "chords.wav"),
                ("bass", tmp_path / "bass.wav"),
                ("melody", tmp_path / "melody.wav"),
                ("percussion", tmp_path / "percussion.wav"),
            ]
            flac_path = tmp_path / "mix.flac"

            _mix_flac(
                "ffmpeg",
                wav_paths,
                flac_path,
                stereo_profile,
                temp_dir=tmp_path,
            )

            cmd_str = " ".join(captured["command"])

            # Chords (V0): 20% left spread
            assert "pan=stereo|c0=0.6*c0|c1=0.4*c0" in cmd_str
            # Percussion (V9): 20% right spread
            assert "pan=stereo|c0=0.4*c0|c1=0.6*c0" in cmd_str
            # Bass (V1) & Melody (V2): dead center
            assert "pan=stereo|c0=0.5*c0|c1=0.5*c0" in cmd_str

    finally:
        audio_render.subprocess.run = orig_run


def test_spec18_bass_tessitura_and_octave_jitter():
    """Verify bass pitches span E1 (MIDI 28) to G3 (MIDI 55) with ~20% octave jitter (Spec 18 §3.1)."""
    rng = random.Random(12345)

    # 1. Pitch bounds: never below 28, never above 55
    pitches = []
    octave_jumps = 0
    total_trials = 2000

    for _ in range(total_trials):
        bass_pc = rng.randint(0, 11)
        tonic_pc = rng.randint(0, 11)
        p = resolve_bass_pitch(bass_pc, tonic_pc, rng)
        assert 28 <= p <= 55, f"Pitch {p} outside [28, 55]"
        pitches.append(p)

    # 2. Verify lowest standard bass register (E1 = 28) is reachable
    assert 28 in pitches
    assert any(p <= 35 for p in pitches)  # Octave 1 notes present
    assert any(p >= 40 for p in pitches)  # Octave 2/3 notes present

    # 3. Test octave jitter rate on notes that can jump without exceeding 55
    fixed_rng = random.Random(42)
    jumps = 0
    trials = 1000
    for _ in range(trials):
        # E (4): base_midi = 28. Jump -> 40.
        p = resolve_bass_pitch(4, 0, fixed_rng)
        if p == 40:
            jumps += 1
    jump_rate = jumps / trials
    # Expected: ~20% (allow 16% to 24% for stochastic variance)
    assert 0.16 <= jump_rate <= 0.24, f"Jump rate {jump_rate} out of expected 20% range"


def test_spec18_bass_patch_prioritization():
    """Verify bass patch weights prioritize finger, pick, acoustic, and fretless basses (Spec 18 §3.3)."""
    rng = random.Random(9999)
    samples = [sample_bass_instrument(rng).name for _ in range(1000)]
    counts = {name: samples.count(name) for name in set(samples)}

    primary_patches = {"electric-bass-finger", "electric-bass-pick", "acoustic-bass", "fretless-bass"}
    primary_total = sum(counts.get(p, 0) for p in primary_patches)
    primary_ratio = primary_total / len(samples)

    # Primary patches should account for >= 85% of sampled basses
    assert primary_ratio >= 0.85, f"Primary bass ratio {primary_ratio} is below 85%"

    # Deprecated patches (square-wave, synth-bass-2, slap-bass-2) should be very rare (< 2%)
    deprecated_patches = {"square-wave", "synth-bass-2", "slap-bass-2"}
    deprecated_total = sum(counts.get(p, 0) for p in deprecated_patches)
    deprecated_ratio = deprecated_total / len(samples)
    assert deprecated_ratio < 0.02, f"Deprecated bass ratio {deprecated_ratio} is too high"


def test_spec18_percussion_velocity_humanization():
    """Verify percussion hits include Gaussian velocity jitter (Spec 18 §4.1)."""
    # 1. Audible percussion with humanization
    module = PercussionModule(seed=42, omission_probability=0, velocity_humanization=True)
    progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [
            {"root_interval": 0, "triad": "major", "bass_interval": 0, "duration_token": "w"},
            {"root_interval": 5, "triad": "major", "bass_interval": 0, "duration_token": "w"},
        ],
    }
    track = module.render(progression)
    tokens = track.split()[1:]

    # Parse velocities from non-rest tokens (e.g. "[BASS_DRUM]sa102+[CLOSED_HI_HAT]sa83")
    kick_vels = []
    snare_vels = []
    hihat_vels = []

    for token in tokens:
        if token == "Rs":
            continue
        parts = token.split("+")
        for part in parts:
            if "a" in part:
                sound_part, vel_str = part.split("a")
                vel = int(vel_str)
                assert 1 <= vel <= 127
                if "BASS_DRUM" in sound_part:
                    kick_vels.append(vel)
                elif "SNARE" in sound_part:
                    snare_vels.append(vel)
                elif "HI_HAT" in sound_part or "CYMBAL" in sound_part:
                    hihat_vels.append(vel)

    assert len(kick_vels) > 0
    assert len(hihat_vels) > 0

    # Kick/Snare mean around 100 (+-8)
    kick_mean = sum(kick_vels) / len(kick_vels)
    assert 92.0 <= kick_mean <= 108.0

    # Hi-hat mean around 80 (+-12)
    hihat_mean = sum(hihat_vels) / len(hihat_vels)
    assert 70.0 <= hihat_mean <= 90.0

    # Humanization implies velocities vary (not robotic identical values)
    assert len(set(kick_vels)) > 1
    assert len(set(hihat_vels)) > 1

    # 2. Disabled velocity humanization emits plain note tokens
    unhumanized_module = PercussionModule(seed=42, omission_probability=0, velocity_humanization=False)
    plain_track = unhumanized_module.render(progression)
    plain_tokens = [t for t in plain_track.split()[1:] if t != "Rs"]
    for t in plain_tokens:
        assert "a" not in t, f"Token {t} contains unexpected velocity marker"


def test_spec18_sustained_slash_inversions_and_slicing():
    """Verify sustained first and second inversions are preserved across whole and half notes (Spec 18 §3.2)."""
    events = [
        # Sustained first inversion C/E whole note
        ChordEvent(
            root_interval=0,
            triad="major",
            bass_interval=4,
            duration_token="w",
            harte="C:maj/3",
        ),
        # Sustained second inversion G/D half note
        ChordEvent(
            root_interval=7,
            triad="major",
            bass_interval=7,
            duration_token="h",
            harte="G:maj/5",
        ),
        # Plain root chord to be sub-measure sliced
        ChordEvent(
            root_interval=5,
            triad="major",
            bass_interval=0,
            duration_token="w",
            harte="F:maj",
        ),
    ]

    sliced = slice_chord_events(events, genre="pop_rock", tonic_pc=0, bpm=120, seed=10)

    # First event: sustained C/E preserved across full whole-note (4 beats)
    assert sliced[0]["duration_token"] == "w"
    assert sliced[0]["duration_beats"] == 4.0
    assert sliced[0]["bass_interval"] == 4
    assert sliced[0]["harte"] == "C:maj/3"

    # Second event: sustained G/D preserved across full half-note (2 beats)
    assert sliced[1]["duration_token"] == "h"
    assert sliced[1]["duration_beats"] == 2.0
    assert sliced[1]["bass_interval"] == 7
    assert sliced[1]["harte"] == "G:maj/5"

    # Third event: root-position F:maj should be sliced into walking/root-5th slices
    f_slices = [s for s in sliced[2:] if s["root_interval"] == 5]
    assert len(f_slices) >= 1


def test_spec18_5k_quota_scaling():
    """Verify 5,000 song corpus quota scales correctly (Spec 18 §5.1)."""
    # 5,000 songs * ~100 chords/song = 500,000 events total (65/35 split)
    pop_quota = GenerationQuota.for_genre("pop_rock", 325_000, triad_target_scale=3.0)
    jazz_quota = GenerationQuota.for_genre("jazz", 175_000, triad_target_scale=3.0)

    # Rare triad targets are scaled and substantial (> 1000 instances)
    assert pop_quota.triad_targets["sus2"] >= 500
    assert pop_quota.triad_targets["sus4"] >= 1000
    assert pop_quota.triad_targets["diminished"] >= 500
    assert pop_quota.triad_targets["augmented"] >= 500

    # Rare extension targets are present and adequately represented
    assert "b9" in jazz_quota.extension_targets
    assert "#11" in jazz_quota.extension_targets
    assert jazz_quota.extension_targets["b9"] >= 200
    assert jazz_quota.extension_targets["#11"] >= 200


def test_spec18_end_to_end_audio_mix_execution(tmp_path):
    """End-to-end execution of _mix_flac with real FFmpeg process and DSP filter chain."""
    import math

    # Generate small 0.25s 22050Hz mono WAV files for stems
    sample_rate = 22050
    duration_s = 0.25
    num_samples = int(sample_rate * duration_s)

    def write_sine_wav(path: Path, freq: float):
        samples = bytearray()
        for i in range(num_samples):
            val = int(math.sin(2.0 * math.pi * freq * i / sample_rate) * 16000)
            samples.extend(val.to_bytes(2, byteorder="little", signed=True))
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(samples)

    chords_wav = tmp_path / "chords.wav"
    bass_wav = tmp_path / "bass.wav"
    perc_wav = tmp_path / "percussion.wav"
    out_flac = tmp_path / "out_mix.flac"

    write_sine_wav(chords_wav, 440.0)
    write_sine_wav(bass_wav, 55.0)   # Low bass note (A1 = 55 Hz)
    write_sine_wav(perc_wav, 60.0)   # Kick transient

    # Mix with Spec 18 reverb, DSP filters, and production FX
    _mix_flac(
        "ffmpeg",
        [
            ("chords", chords_wav),
            ("bass", bass_wav),
            ("percussion", perc_wav),
        ],
        out_flac,
        REFERENCE_RENDER_PROFILE,
        reverb="studio",
        production_fx=True,
        temp_dir=tmp_path,
    )

    assert out_flac.is_file()
    assert out_flac.stat().st_size > 500


def test_spec18_target_corpus_generation_meets_quotas_with_sustained_inversions():
    """Verify target corpus generation satisfies quotas with sustained inversions (Spec 18 §3.2, §5.1)."""
    from target_corpus_gen import GenerationQuota, TargetChordGenerator, GenParams

    generator = TargetChordGenerator(
        genre="pop_rock",
        tonic_pc=0,
        bpm=120,
        dist_dir="./distributions",
        params=GenParams(),
        sustained_inversion_rate=0.10,
        seed=15001,
    )
    quota = GenerationQuota.for_genre("pop_rock", 1000, triad_target_scale=3.0)
    songs = generator.generate_batch(10, 1000, quota=quota)
    assert len(songs) == 10
    assert quota.meets_targets(), f"Deficits remain: {quota.deficits()}"
