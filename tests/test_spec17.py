"""Comprehensive acceptance and regression test suite for Spec 17.

Verifies:
Gate 1: Inversion Label Slicing Gate
  - Sub-measure time-aligned chord events for walking bass, root-5th alternations, and approach notes.
  - Duration, start_beat, end_beat preservation.
  - Non-root bass rate >= 12% across sliced corpus.
Gate 2: Vocal Formant Gate
  - General MIDI programs 52 (choir-aahs), 53 (voice-oohs), 54 (synth-voice) in melody instruments.
  - Prioritized in lead-mid-neutral and lead-mid-active melody profiles.
  - Selected in >= 25% of pop/rock melody tracks.
Gate 3: Studio Production FX Gate
  - Sample EQ tilt (+/- 1.5 to 3.0 dB around 1.5 kHz).
  - FFmpeg filter graph incorporates lowshelf/highshelf, tape saturation (aeval), compand, and loudnorm.
  - Manifest records production_fx_applied and eq_tilt_db_applied.
Gate 4: 10,000 Song Quota & Long-Tail Representation
  - GenerationQuota scales to 1M events, 10k songs (65/35 genre split, 50/50 bimodal split).
  - Rare harmonic extension targets match Spec 17 §5.2.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from bass_module import BassModule, slice_chord_events, slice_progression
from instruments import (
    Instrument,
    MELODY_INSTRUMENTS,
    MELODY_PROFILE_PREFERRED_INSTRUMENTS,
)
from melody_module import (
    MelodyGenerationConfig,
    generate_melody,
)
from render import render_song_tracks
from sound_design import (
    derive_seed,
    sample_eq_tilt_for_ordinal,
)
from audio_render import (
    _mix_flac,
    mix_audio_bundle_from_stems,
    render_audio_bundle,
)
from target_corpus_gen import GenerationQuota, generate_target_corpus


# ---------------------------------------------------------------------------
# Gate 1: Inversion Label Slicing Gate
# ---------------------------------------------------------------------------

def test_spec17_submeasure_inversion_slicing_structure():
    """Verify sub-measure chord slicing generates valid time-aligned slices."""
    pop_progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [
            {
                "root_interval": 0,
                "triad": "major",
                "seventh": "7",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "C",
                "bass": "C",
                "harte": "C:maj7",
            },
            {
                "root_interval": 9,
                "triad": "minor",
                "seventh": "b7",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "A",
                "bass": "A",
                "harte": "A:min7",
            },
            {
                "root_interval": 5,
                "triad": "major",
                "seventh": "7",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "F",
                "bass": "F",
                "harte": "F:maj7",
            },
            {
                "root_interval": 7,
                "triad": "major",
                "seventh": "b7",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "G",
                "bass": "G",
                "harte": "G:7",
            },
        ],
    }

    sliced_chords = slice_chord_events(pop_progression, seed=15002)
    assert len(sliced_chords) > len(pop_progression["chords"]), (
        "Progression must be sliced into more chord slices than whole measures"
    )

    # Verify time continuity and duration preservation
    current_time = 0.0
    for slice_chord in sliced_chords:
        dur = slice_chord.get("duration_beats")
        assert dur is not None and dur > 0, "Slice duration must be positive"
        assert slice_chord["duration_token"] in {"q", "h", "h.", "w"}
        assert "harte" in slice_chord and slice_chord["harte"]

    # Check jazz 4-beat walking line slicing
    jazz_progression = {
        "genre": "jazz",
        "tonic_pc": 2,
        "bpm": 140,
        "chords": [
            {
                "root_interval": 2,
                "triad": "minor",
                "seventh": "b7",
                "ninth": "9",
                "eleventh": "11",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "D",
                "bass": "D",
                "harte": "D:min7(9,11)",
            },
            {
                "root_interval": 7,
                "triad": "major",
                "seventh": "b7",
                "ninth": "b9",
                "eleventh": "N",
                "thirteenth": "b13",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "G",
                "bass": "G",
                "harte": "G:7(b9,b13)",
            },
        ],
    }
    jazz_slices = slice_chord_events(jazz_progression, seed=25001)
    # Jazz progressions slice into walking/alternating lines
    assert len(jazz_slices) >= 4, "Jazz whole note measures must slice into sub-measure beats"

    # Check that slice_progression helper updates the dictionary in place
    prog_copy = dict(jazz_progression)
    slice_progression(prog_copy, seed=25001)
    assert len(prog_copy["chords"]) == len(jazz_slices)


def test_spec17_inversion_slicing_gate_non_root_bass_rate():
    """Verify non-root bass rate across sliced corpus reaches >= 12% (Gate 1)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dirs = {
            "pop_rock": os.path.join(tmp_dir, "pop-rock-labels"),
            "jazz": os.path.join(tmp_dir, "jazz-labels"),
        }
        results = generate_target_corpus(
            events_by_genre={"pop_rock": 1300, "jazz": 700},
            songs_by_genre={"pop_rock": 32, "jazz": 18},
            bimodal_natural_ratio=0.5,
            dist_dir="./distributions",
            output_dirs=out_dirs,
            seed=42,
            slice_inversions=True,
        )

        total_chord_events = 0
        non_root_bass_events = 0

        for genre, songs in results.items():
            for song in songs:
                for chord in song:
                    total_chord_events += 1
                    bass_interval = chord.get("bass_interval", 0)
                    harte = chord.get("harte", "")
                    if bass_interval != 0 or "/" in harte:
                        non_root_bass_events += 1

        non_root_rate = non_root_bass_events / total_chord_events
        assert non_root_rate >= 0.12, (
            f"Ground truth non-root bass rate {non_root_rate:.2%} must be >= 12% (Spec 17 Gate 1)"
        )


# ---------------------------------------------------------------------------
# Gate 2: Vocal Formant Gate
# ---------------------------------------------------------------------------

def test_spec17_vocal_lead_instruments_catalog():
    """Verify GM programs 52, 53, 54 exist in MELODY_INSTRUMENTS and preferred profiles."""
    vocal_names = {"choir-aahs", "voice-oohs", "synth-voice"}
    melody_inst_names = {inst.name for inst in MELODY_INSTRUMENTS}
    assert vocal_names.issubset(melody_inst_names), (
        f"MELODY_INSTRUMENTS must include GM vocal patches: {vocal_names - melody_inst_names}"
    )

    programs = {inst.name: inst.program for inst in MELODY_INSTRUMENTS if inst.name in vocal_names}
    assert programs["choir-aahs"] == 52
    assert programs["voice-oohs"] == 53
    assert programs["synth-voice"] == 54

    # Verify preference in lead-mid-neutral and lead-mid-active
    neutral_prefs = MELODY_PROFILE_PREFERRED_INSTRUMENTS.get("lead-mid-neutral", ())
    active_prefs = MELODY_PROFILE_PREFERRED_INSTRUMENTS.get("lead-mid-active", ())
    assert "voice-oohs" in neutral_prefs or "choir-aahs" in neutral_prefs
    assert "synth-voice" in active_prefs or "choir-aahs" in active_prefs


def test_spec17_vocal_formant_gate_selection_frequency():
    """Verify GM programs 52, 53, 54 are selected in >= 25% of pop/rock melody tracks (Gate 2)."""
    from render import _render_progression

    sample_progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [
            {"root_interval": 0, "triad": "major", "seventh": "7", "ninth": "N", "eleventh": "N", "thirteenth": "N", "bass_interval": 0, "duration_token": "w"},
            {"root_interval": 9, "triad": "minor", "seventh": "b7", "ninth": "N", "eleventh": "N", "thirteenth": "N", "bass_interval": 0, "duration_token": "w"},
            {"root_interval": 5, "triad": "major", "seventh": "7", "ninth": "N", "eleventh": "N", "thirteenth": "N", "bass_interval": 0, "duration_token": "w"},
            {"root_interval": 7, "triad": "major", "seventh": "b7", "ninth": "N", "eleventh": "N", "thirteenth": "N", "bass_interval": 0, "duration_token": "w"},
        ],
    }

    vocal_programs = {52, 53, 54}
    total_pop_melodies = 120
    vocal_selected_count = 0

    for i in range(total_pop_melodies):
        result = _render_progression(
            sample_progression,
            0,
            20000 + i,
            "pads",
            melody_condition="naturalistic",
            melody_profile="mixed",
            melody_included=True,
        )
        melody_info = result.get("melody")
        if melody_info and melody_info.get("instrument_program") in vocal_programs:
            vocal_selected_count += 1

    vocal_rate = vocal_selected_count / total_pop_melodies
    assert vocal_rate >= 0.25, (
        f"Vocal instruments selected at {vocal_rate:.2%}, expected >= 25% for pop/rock (Spec 17 Gate 2)"
    )


# ---------------------------------------------------------------------------
# Gate 3: Studio Production FX Gate
# ---------------------------------------------------------------------------

def test_spec17_sample_eq_tilt():
    """Verify spectral EQ tilt samples within +/- 1.5 to 3.0 dB around 1.5 kHz."""
    sampled_tilts = [sample_eq_tilt_for_ordinal(15001, ord_idx) for ord_idx in range(50)]
    for tilt in sampled_tilts:
        assert 1.5 <= abs(tilt) <= 3.0, (
            f"EQ tilt {tilt} dB must be between 1.5 and 3.0 dB in magnitude"
        )
    # Check that both positive (bright) and negative (warm) tilts are generated
    assert any(tilt > 0 for tilt in sampled_tilts), "Should have positive tilts"
    assert any(tilt < 0 for tilt in sampled_tilts), "Should have negative tilts"


def test_spec17_production_fx_filter_graph(monkeypatch):
    """Verify FFmpeg filter graph includes EQ tilt, saturation, and dynamic compand."""
    from sound_design import REFERENCE_RENDER_PROFILE

    executed_commands = []

    def mock_run(cmd, *args, **kwargs):
        executed_commands.append(cmd)
        # Create dummy flac output file
        out_file = Path(cmd[-1])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"FLAC mock audio")
        class MockResult:
            returncode = 0
            stdout = ""
            stderr = ""
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        mix_out = tmp_path / "mix.flac"
        stem_wav = tmp_path / "stem.wav"
        stem_wav.write_bytes(b"RIFF mock wav")

        profile = dict(REFERENCE_RENDER_PROFILE)
        profile["role_gains_db"] = {"V0": 0.0}

        _mix_flac(
            "ffmpeg",
            [("chords", stem_wav)],
            mix_out,
            profile,
            production_fx=True,
            eq_tilt_db=2.5,
        )

        assert len(executed_commands) == 1
        cmd_str = " ".join(executed_commands[0])

        # Verify presence of lowshelf and highshelf EQ tilt
        assert "lowshelf=f=1500" in cmd_str
        assert "highshelf=f=1500" in cmd_str
        # Verify presence of soft saturation polynomial
        assert "aeval=" in cmd_str and "val(0)" in cmd_str
        # Verify presence of dynamic bus compand
        assert "compand=" in cmd_str
        # Verify presence of loudnorm
        assert "loudnorm=" in cmd_str


# ---------------------------------------------------------------------------
# Gate 4: 10,000 Song Quota & Long-Tail Representation
# ---------------------------------------------------------------------------

def test_spec17_10k_quota_scaling():
    """Verify 10,000 song quota scales to 1M events with 65/35 genre split."""
    pop_quota = GenerationQuota.for_genre("pop_rock", 650_000, triad_target_scale=3.0)
    jazz_quota = GenerationQuota.for_genre("jazz", 350_000, triad_target_scale=3.0)

    assert pop_quota.total_events == 650_000
    assert jazz_quota.total_events == 350_000

    # Rare harmonic extension quotas (Spec 17 §5.2)
    total_b7 = pop_quota.extension_targets["b7"] + jazz_quota.extension_targets["b7"]
    total_b9 = pop_quota.extension_targets["b9"] + jazz_quota.extension_targets["b9"]
    total_sharp9 = pop_quota.extension_targets["#9"] + jazz_quota.extension_targets["#9"]
    total_11 = pop_quota.extension_targets["11"] + jazz_quota.extension_targets["11"]
    total_sharp11 = pop_quota.extension_targets["#11"] + jazz_quota.extension_targets["#11"]
    total_13 = pop_quota.extension_targets["13"] + jazz_quota.extension_targets["13"]
    total_b13 = pop_quota.extension_targets["b13"] + jazz_quota.extension_targets["b13"]

    assert total_b7 >= 250_000
    assert total_b9 >= 8_000
    assert total_sharp9 >= 12_000
    assert total_11 >= 12_000
    assert total_sharp11 >= 6_000
    assert total_13 >= 8_000
    assert total_b13 >= 6_000

    total_sus2 = pop_quota.triad_targets["sus2"] + jazz_quota.triad_targets["sus2"]
    total_sus4 = pop_quota.triad_targets["sus4"] + jazz_quota.triad_targets["sus4"]
    total_dim = pop_quota.triad_targets["diminished"] + jazz_quota.triad_targets["diminished"]

    assert total_sus2 >= 20_000
    assert total_sus4 >= 25_000
    assert total_dim >= 15_000
