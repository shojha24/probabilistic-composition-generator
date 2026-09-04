import hashlib
import json
import logging
import os
import random
import sys
from collections import Counter
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chord_module
from eda.validate_rendered_corpus import parse_score_blocks, validate_corpus
from render import _render_mode_plan, _source_files, render_directory
from voicing.dct import compute_dct
from voicing.engine import Engine, VoicingImpossible
from voicing.types import ChordEvent, Song, resolve_degrees
from voicing.voicers import (
    jazz_guitar,
    jazz_piano,
    jazz_synth,
    pop_guitar,
    pop_piano,
    pop_synth,
)


def _label(genre="pop_rock", tonic_pc=0, chord=None):
    chord = chord or {
        "root_interval": 0,
        "triad": "major",
        "bass_interval": 0,
        "seventh": "N",
        "ninth": "N",
        "eleventh": "N",
        "thirteenth": "N",
    }
    return {
        "genre": genre,
        "tonic_pc": tonic_pc,
        "bpm": 120,
        "num_chords": 1,
        "chords": [chord],
    }


def test_numeric_source_order_and_filename_validation(tmp_path):
    for name in ("song_10.json", "song_2.json", "song_1.json"):
        (tmp_path / name).write_text(json.dumps(_label()))
    ordered = _source_files(str(tmp_path))
    assert [item[0] for item in ordered] == [1, 2, 10]

    (tmp_path / "song_bad.json").write_text(json.dumps(_label()))
    with pytest.raises(ValueError, match="Malformed song filename"):
        _source_files(str(tmp_path))


def test_mixed_render_mode_plan_is_seeded_and_exact():
    modes, effective_mode, targets = _render_mode_plan(
        "mixed", 10, 7, arpeggio_percent=30, pad_percent=70
    )
    assert effective_mode == "mixed"
    assert modes.count("arpeggios") == 3
    assert modes.count("pads") == 7
    assert targets == {"arpeggios": 30.0, "pads": 70.0}
    repeated, _, _ = _render_mode_plan(
        "mixed", 10, 7, arpeggio_percent=30, pad_percent=70
    )
    assert repeated == modes

    inferred, _, inferred_targets = _render_mode_plan(
        "pads", 10, 7, arpeggio_percent=30
    )
    assert inferred.count("arpeggios") == 3
    assert inferred_targets == {"arpeggios": 30.0, "pads": 70.0}

    with pytest.raises(ValueError, match="sum to 100"):
        _render_mode_plan(
            "mixed", 10, 7, arpeggio_percent=30, pad_percent=60
        )
    with pytest.raises(ValueError, match="requires"):
        _render_mode_plan("mixed", 10, 7)


def test_mixed_render_directory_records_each_mode(tmp_path):
    for index in range(4):
        progression = _label()
        progression["chords"][0]["duration_token"] = "q"
        (tmp_path / f"song_{index}.json").write_text(
            json.dumps(progression)
        )

    output = tmp_path / "mixed-scores.txt"
    render_directory(
        tmp_path,
        str(output),
        seed=7,
        mode="mixed",
        arpeggio_percent=50,
        pad_percent=50,
    )
    manifest = json.loads(
        output.with_name(output.name + ".manifest.json").read_text()
    )
    report = validate_corpus(
        tmp_path,
        output,
        output.with_name(output.name + ".manifest.json"),
        expected_genre="pop_rock",
    )

    assert manifest["render_mode"] == "mixed"
    assert manifest["render_mode_counts"] == {
        "arpeggios": 2,
        "pads": 2,
    }
    assert manifest["render_mode_percentages"] == {
        "arpeggios": 50.0,
        "pads": 50.0,
    }
    assert manifest["render_mode_targets"] == {
        "arpeggios": 50.0,
        "pads": 50.0,
    }
    assert Counter(
        record["render_mode"] for record in manifest["records"]
    ) == Counter({"arpeggios": 2, "pads": 2})
    assert "--mode mixed" in manifest["command"]
    assert len(parse_score_blocks(output)) == 4
    assert report["pairing_errors"] == 0
    assert report["hard_failure_count"] == 0


def test_render_directory_records_percussion_inclusion_target(tmp_path):
    for index in range(2):
        (tmp_path / f"song_{index}.json").write_text(
            json.dumps(_label())
        )

    output = tmp_path / "scores.txt"
    render_directory(
        tmp_path,
        str(output),
        seed=7,
        mode="pads",
        percussion_percent=0,
    )
    manifest = json.loads(
        output.with_name(output.name + ".manifest.json").read_text()
    )

    assert manifest["percussion_inclusion_percent"] == 0.0
    assert manifest["percussion_inclusion_probability"] == 0.0
    assert manifest["percussion_included_count"] == 0
    assert manifest["percussion_realized_percent"] == 0.0
    assert "--percussion-percent 0" in manifest["command"]
    assert all(
        record["percussion_included"] is False
        and record["percussion_inclusion_percent"] == 0.0
        for record in manifest["records"]
    )


def test_manifest_hash_and_score_pairing(tmp_path):
    label_path = tmp_path / "song_10.json"
    raw = json.dumps(_label(), separators=(",", ":")).encode()
    label_path.write_bytes(raw)
    score_path = tmp_path / "scores.txt"
    score_path.write_text(
        "START_SONG_0\n"
        "T120 V0 I0 C4w+E4w+G4w  V1 I32 C3w  V9 Rs\n"
        "END_SONG\n"
    )
    manifest_path = tmp_path / "scores.txt.manifest.json"
    manifest_path.write_text(json.dumps({
        "records": [{
            "ordinal": 0,
            "source_file": "song_10.json",
            "source_id": 10,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "genre": "pop_rock",
            "tonic_pc": 0,
            "bpm": 120,
            "num_chords": 1,
            "voicer_family": "piano",
        }]
    }))

    assert parse_score_blocks(score_path) == {
        0: "T120 V0 I0 C4w+E4w+G4w  V1 I32 C3w  V9 Rs"
    }
    report = validate_corpus(tmp_path, score_path, manifest_path, "pop_rock")
    assert report["pairing_errors"] == 0
    assert report["hard_failure_count"] == 0

    manifest_path.write_text(json.dumps({
        "records": [{
            "ordinal": 0,
            "source_file": "song_10.json",
            "source_id": 10,
            "source_sha256": "bad",
            "genre": "pop_rock",
            "tonic_pc": 0,
            "bpm": 120,
            "num_chords": 1,
            "voicer_family": "piano",
        }]
    }))
    report = validate_corpus(tmp_path, score_path, manifest_path, "pop_rock")
    assert any(issue["category"] == "source_hash_mismatch"
               for issue in report["issues"])


def test_non_c_tonic_uses_absolute_root_for_every_voicer_family():
    chord = ChordEvent(0, "major", 0, "7")
    policies = (
        ("pop_rock", pop_piano.POLICY),
        ("pop_rock", pop_guitar.POLICY),
        ("pop_rock", pop_synth.POLICY),
        ("jazz", jazz_piano.POLICY),
        ("jazz", jazz_guitar.POLICY),
        ("jazz", jazz_synth.POLICY),
    )
    expected_pcs = {0, 1, 5, 8}
    for genre, policy in policies:
        engine = Engine(
            policy,
            {"bass_module_active": True, "seed": 5},
            root_omission_gate=genre == "jazz",
        )
        voiced = engine.run(Song(genre, 1, 120, 1, (chord,)))[0]
        assert engine.ctx["_current_root_pc"] == 1
        assert {pitch % 12 for pitch in voiced.midi} <= expected_pcs


def test_inversion_bass_uses_absolute_root():
    from bass_module import BassModule

    progression = _label(
        tonic_pc=1,
        chord={
            "root_interval": 0,
            "triad": "major",
            "bass_interval": 4,
            "seventh": "N",
            "ninth": "N",
            "eleventh": "N",
            "thirteenth": "N",
        },
    )
    track = BassModule(seed=1).render(progression)
    assert "F2w" in track


def test_guitar_candidate_rejects_inactive_extension():
    chord = ChordEvent(0, "major", 0, "7")
    degrees = resolve_degrees(chord)
    dct_role, _ = compute_dct(chord, degrees)
    params = SimpleNamespace(
        chord=chord,
        root_pc=0,
        degrees=degrees,
        dct_role=dct_role,
        ctx={"bass_module_active": True},
        max_voices=6,
        policy=pop_guitar.POLICY,
        window_lo=pop_guitar.POLICY.window_lo,
        window_hi=pop_guitar.POLICY.window_hi,
        anchor_center=60,
        prev_midi=None,
        rng=random.Random(2),
        max_doublings=1,
        doubling_roles=["root", "5th"],
        section=None,
        anchor_shift=0,
    )
    for candidate in pop_guitar.candidate_source(params):
        assert "9th" not in candidate.roles
        assert all(pitch % 12 in {0, 4, 7, 11} for pitch in candidate.pitches)


def test_chord_module_logs_each_failed_voicer(monkeypatch, caplog):
    order = (
        "pop-guitar", "pop-synth", "pop-piano",
        "jazz-guitar", "jazz-synth", "jazz-piano",
    )

    class AlwaysFailEngine:
        def __init__(self, policy, ctx, root_omission_gate=False):
            self.policy = policy

        def run(self, song):
            raise VoicingImpossible(song.chords[0], 0)

    monkeypatch.setattr(chord_module, "Engine", AlwaysFailEngine)
    caplog.set_level(logging.WARNING, logger="chord_module")

    with pytest.raises(RuntimeError) as raised:
        chord_module.ChordModule(seed=7).render(
            _label(),
            bass_module_active=True,
            voicer_order=order,
        )

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "chord_module"
    ]
    assert all(
        f"{voicer}:" in message
        for voicer, message in zip(order, messages)
    )
    assert len(messages) == len(order)
    assert all(f"- {voicer}:" in str(raised.value) for voicer in order)
