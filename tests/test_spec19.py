"""Unit and integration tests for Spec 19:
Empirical Benchmark Mitigations and Distributional Alignment.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import audio_render
from audio_render import _mix_flac
from bass_module import BassModule, slice_chord_events
from melody_module import (
    enumerate_melody_candidates,
    filter_melody_pitch_candidates,
    generate_melody,
)
from sound_design import POWER_CHORD_DISTORTION_FILTER, REFERENCE_RENDER_PROFILE
from tools.project_condition_corpus import build_parser


# ============================================================================
# Gate 1: Distributional Alignment of Bass Movement (§4.1)
# ============================================================================

def test_spec19_pop_rock_bass_slicing_eliminates_artificial_slash_five():
    """Verify pop_rock whole-note chords keep root-position label and 4-beat duration

    when executing the root-5th groove, eliminating artificial /5 labels.
    """
    progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [
            {
                "root_interval": 0,
                "triad": "major",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "C",
                "bass": "C",
                "harte": "C:maj",
            },
            {
                "root_interval": 9,
                "triad": "minor",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "A",
                "bass": "A",
                "harte": "A:min",
            },
        ],
    }

    # Test over multiple seeds
    slash_five_count = 0
    total_slices = 0
    for seed in range(2000, 2050):
        sliced = slice_chord_events(progression, seed=seed)
        total_slices += len(sliced)
        for chord in sliced:
            # Under Spec 19, pop_rock whole notes must NEVER slice with /5
            assert not chord["harte"].endswith("/5"), f"Found forbidden /5 slice: {chord['harte']}"
            if chord.get("bass_interval") == 7:
                slash_five_count += 1

    assert slash_five_count == 0, "Artificial /5 chord slices must be 0 for pop_rock whole notes"


def test_spec19_bass_module_renders_root_fifth_groove_without_slash_label():
    """Verify BassModule renders the root-5th audio groove while label remains root-position."""
    progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [
            {
                "root_interval": 0,
                "triad": "major",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "C",
                "bass": "C",
                "harte": "C:maj",
            },
            {
                "root_interval": 5,
                "triad": "major",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "F",
                "bass": "F",
                "harte": "F:maj",
            },
        ],
    }

    # Test seeds to find one that triggers the 25% root-5th groove roll (e.g. seeds 5, 6)
    groove_rendered = False
    for seed in range(10):
        bass_mod = BassModule(seed=seed)
        tokens_str = bass_mod.render(progression)
        # Root C + Fifth G half note groove in audio tokens
        if ("C2h G2h" in tokens_str) or ("C3h G2h" in tokens_str) or ("C3h G3h" in tokens_str):
            groove_rendered = True
            break

    assert groove_rendered, "Root-5th groove must be rendered in audio by BassModule"


# ============================================================================
# Gate 2: Third-Masking in Melody Generation (§4.2)
# ============================================================================

def test_spec19_filter_melody_pitch_candidates_unit():
    """Unit test filter_melody_pitch_candidates for 5, sus4, sus2, 1 chords."""
    # Root C (pc = 0)
    # Pitches: 60 (C), 61 (Db), 62 (D), 63 (Eb), 64 (E), 65 (F), 67 (G), 69 (A), 70 (Bb), 71 (B)
    candidates = [60, 61, 62, 63, 64, 65, 67, 69, 70, 71]

    # Triad '5': strictly pentatonic {0, 5, 7, 10} -> {60 (C), 65 (F), 67 (G), 70 (Bb)}
    filtered_5 = filter_melody_pitch_candidates(candidates, triad="5", root_pc=0)
    expected_5 = [60, 65, 67, 70]
    assert filtered_5 == expected_5, f"Expected {expected_5}, got {filtered_5}"

    # Triad 'sus4': mask 3 and 4 -> {60, 61, 62, 65, 67, 69, 70, 71}
    filtered_sus4 = filter_melody_pitch_candidates(candidates, triad="sus4", root_pc=0)
    assert 63 not in filtered_sus4  # minor 3rd masked
    assert 64 not in filtered_sus4  # major 3rd masked
    assert 65 in filtered_sus4      # 4th preserved

    # Triad 'sus2': mask 3 and 4 -> {60, 61, 62, 65, 67, 69, 70, 71}
    filtered_sus2 = filter_melody_pitch_candidates(candidates, triad="sus2", root_pc=0)
    assert 63 not in filtered_sus2
    assert 64 not in filtered_sus2
    assert 62 in filtered_sus2      # 2nd preserved

    # Triad '1': mask 3 and 4
    filtered_1 = filter_melody_pitch_candidates(candidates, triad="1", root_pc=0)
    assert 63 not in filtered_1
    assert 64 not in filtered_1

    # Standard major/minor triads should NOT be altered by filter_melody_pitch_candidates
    filtered_maj = filter_melody_pitch_candidates(candidates, triad="major", root_pc=0)
    assert filtered_maj == candidates

    filtered_min = filter_melody_pitch_candidates(candidates, triad="minor", root_pc=0)
    assert filtered_min == candidates


def test_spec19_enumerate_melody_candidates_masks_thirds():
    """Verify enumerate_melody_candidates masks thirds and b3 on power/sus chords."""
    progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [
            {
                "root_interval": 0,
                "triad": "5",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "C",
                "bass": "C",
                "harte": "C:5",
            },
            {
                "root_interval": 2,
                "triad": "sus4",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "D",
                "bass": "D",
                "harte": "D:sus4",
            },
        ],
    }

    candidates_5 = enumerate_melody_candidates(progression, event_index=0)
    assert len(candidates_5) > 0
    for cand in candidates_5:
        if cand.midi is not None:
            interval = (cand.midi - 0) % 12
            assert interval not in {3, 4}, f"Candidate {cand.midi} leaked 3rd into '5' chord"
            assert interval in {0, 5, 7, 10}, f"Candidate {cand.midi} violates pentatonic framework for '5'"

    candidates_sus4 = enumerate_melody_candidates(progression, event_index=1)
    assert len(candidates_sus4) > 0
    for cand in candidates_sus4:
        if cand.midi is not None:
            interval = (cand.midi - 2) % 12
            assert interval not in {3, 4}, f"Candidate {cand.midi} leaked 3rd into 'sus4' chord"


def test_spec19_generate_melody_zero_third_leakage():
    """Integration test: generate_melody across multiple seeds produces 0 third-leakage events on '5' and 'sus' chords."""
    progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [
            {
                "root_interval": 0,
                "triad": "5",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "C",
                "bass": "C",
                "harte": "C:5",
            },
            {
                "root_interval": 5,
                "triad": "sus4",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "F",
                "bass": "F",
                "harte": "F:sus4",
            },
            {
                "root_interval": 7,
                "triad": "5",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "G",
                "bass": "G",
                "harte": "G:5",
            },
            {
                "root_interval": 9,
                "triad": "sus2",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "A",
                "bass": "A",
                "harte": "A:sus2",
            },
        ],
    }

    # Test across 20 seeds
    for seed in range(100, 120):
        melody = generate_melody(progression, seed=seed)
        events = melody.events
        assert len(events) > 0, "Melody must generate events"

        for ev in events:
            if ev.midi is None:
                continue
            chord_idx = ev.source_event_indices[0]
            chord = progression["chords"][chord_idx]
            triad = chord["triad"]
            root_pc = chord["root_interval"]

            interval = (ev.midi - root_pc) % 12
            if triad in ("5", "sus4", "sus2", "1"):
                assert interval not in {3, 4}, (
                    f"Seed {seed}: Melody pitch {ev.midi} (interval {interval}) leaked into {triad} chord"
                )
            if triad == "5":
                assert interval in {0, 5, 7, 10}, (
                    f"Seed {seed}: Melody pitch {ev.midi} (interval {interval}) not in pentatonic set for '5' chord"
                )


# ============================================================================
# Gate 3: Power Chord Saturation & Overdrive Modeling (§4.3)
# ============================================================================

def test_spec19_power_chord_distortion_filter_constant():
    """Verify POWER_CHORD_DISTORTION_FILTER exact filter string per Spec 19 §4.3."""
    expected = (
        "lowshelf=f=100:g=-3.0,"
        "aeval='(3/2)*val(0)*(1-(val(0)*val(0))/3)':c=same,"
        "highshelf=f=4500:g=-6.0,"
        "lowshelf=f=250:g=2.5"
    )
    assert POWER_CHORD_DISTORTION_FILTER == expected


def test_spec19_audio_render_power_chord_filtergraph():
    """Verify _mix_flac inserts power chord saturation filter on V0 when requested."""
    captured = {}

    def fake_run(command, *args, **kwargs):
        captured["command"] = command

    with patch("audio_render.subprocess.run", side_effect=fake_run):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            wav_paths = [
                ("chords", tmp_path / "chords.wav"),
                ("bass", tmp_path / "bass.wav"),
                ("melody", tmp_path / "melody.wav"),
                ("percussion", tmp_path / "drums.wav"),
            ]
            out_flac = tmp_path / "mix.flac"

            # Case 1: power_chord_distortion=True
            _mix_flac(
                "ffmpeg",
                wav_paths,
                out_flac,
                REFERENCE_RENDER_PROFILE,
                temp_dir=tmp_path,
                power_chord_distortion=True,
            )
            cmd1 = " ".join(captured["command"])
            assert POWER_CHORD_DISTORTION_FILTER in cmd1

            # Case 2: power_chord_distortion=False
            _mix_flac(
                "ffmpeg",
                wav_paths,
                out_flac,
                REFERENCE_RENDER_PROFILE,
                temp_dir=tmp_path,
                power_chord_distortion=False,
            )
            cmd2 = " ".join(captured["command"])
            assert POWER_CHORD_DISTORTION_FILTER not in cmd2


def test_spec19_render_progression_metadata_power_chord_tagging():
    """Verify render.py tags has_power_chords and power_chord_distortion in progression metadata."""
    from render import _render_progression

    # Progression with power chords
    prog_power = {
        "genre": "pop_rock",
        "bpm": 120,
        "tonic_pc": 0,
        "chords": [
            {
                "root_interval": 0,
                "triad": "5",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "C",
                "bass": "C",
                "harte": "C:5",
            }
        ],
    }

    res_power = _render_progression(prog_power, ordinal=1, seed=42, mode="pads")
    assert res_power["has_power_chords"] is True
    assert res_power["power_chord_distortion"] is True

    # Progression without power chords
    prog_clean = {
        "genre": "pop_rock",
        "bpm": 120,
        "tonic_pc": 0,
        "chords": [
            {
                "root_interval": 0,
                "triad": "major",
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "bass_interval": 0,
                "duration_token": "w",
                "root": "C",
                "bass": "C",
                "harte": "C:maj",
            }
        ],
    }

    res_clean = _render_progression(prog_clean, ordinal=1, seed=42, mode="pads")
    assert res_clean["has_power_chords"] is False
    assert res_clean["power_chord_distortion"] is False


def test_spec19_cli_power_chord_distortion_flag():
    """Verify tools/project_condition_corpus.py has --power-chord-distortion argument."""
    parser = build_parser()
    args_default = parser.parse_args(["--canonical", "foo.txt", "--out-dir", "/tmp"])
    assert hasattr(args_default, "power_chord_distortion")
    assert args_default.power_chord_distortion is True

    args_off = parser.parse_args(["--canonical", "foo.txt", "--out-dir", "/tmp", "--no-power-chord-distortion"])
    assert args_off.power_chord_distortion is False
