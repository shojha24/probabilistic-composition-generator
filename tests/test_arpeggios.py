import hashlib
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import chord_module
from chord_module import (
    ArpeggioAttack,
    _arpeggio_cycle,
    schedule_arpeggios,
    serialize_scheduled_arpeggios,
    serialize_arpeggios,
    sixteenth_slot_count,
)
from eda.validate_rendered_corpus import (
    _parse_track_details,
    _track_timeline_sixteenths,
    validate_corpus,
)
from instruments import ARPEGGIO_PROFILES, ArpeggioProfile
from render import _render_source, render_song
from voicing.types import ChordEvent, VoicedChord


def _event(duration="w", no_chord=False):
    if no_chord:
        return {
            "is_no_chord": True,
            "root_interval": None,
            "triad": None,
            "bass_interval": None,
            "seventh": "N",
            "ninth": "N",
            "eleventh": "N",
            "thirteenth": "N",
            "root": None,
            "bass": None,
            "harte": "N",
            "duration_token": duration,
        }
    return {
        "is_no_chord": False,
        "root_interval": 0,
        "triad": "major",
        "bass_interval": 0,
        "seventh": "N",
        "ninth": "N",
        "eleventh": "N",
        "thirteenth": "N",
        "root": "C",
        "bass": "C",
        "harte": "C:maj",
        "duration_token": duration,
    }


def _voiced(index, midis):
    return VoicedChord(
        index, "test", list(midis), [], None, None, None, 0.0, 60.0,
        {
            "level": 0,
            "sounding_strings": [6, 5, 1][:len(midis)],
        },
    )


def test_arpeggio_cycles_cover_arbitrary_voice_counts():
    for family in ("up", "down", "up_down", "outside_in"):
        for voice_count in range(2, 7):
            cycle = _arpeggio_cycle(family, voice_count)
            assert set(cycle) == set(range(voice_count))
            assert all(
                left != right
                for left, right in zip(cycle, cycle[1:] + cycle[:1])
            )

    events = [ChordEvent(0, "major", 0, duration_token="w")]
    for voice_count in range(1, 7):
        midis = list(range(48, 48 + voice_count))
        tokens, diagnostics = serialize_arpeggios(
            [_voiced(0, midis)], events, random.Random(voice_count)
        )
        assert len(tokens) == 16
        assert set(token[:-1] for token in tokens) <= {
            chord_module.midi_to_jfugue(midi) for midi in midis
        }
        if voice_count > 1:
            indexes = [
                midis.index(
                    next(
                        midi for midi in midis
                        if chord_module.midi_to_jfugue(midi) == token[:-1]
                    )
                )
                for token in tokens
            ]
            assert all(
                left != right for left, right in zip(indexes, indexes[1:])
            )
            cycle = _arpeggio_cycle(
                diagnostics[0]["pattern_family"], voice_count
            )
            assert set(indexes[:len(cycle)]) == set(range(voice_count))
        assert diagnostics[0]["source_voicing_midis"] == midis


def test_arpeggio_duration_and_no_chord_boundaries():
    durations = ("ww", "w.", "w", "h.", "h", "q.", "q")
    events = [ChordEvent(0, "major", 0, duration_token=duration)
              for duration in durations]
    voiced = [_voiced(index, [48, 52, 55]) for index in range(len(events))]
    tokens, diagnostics = serialize_arpeggios(
        voiced, events, random.Random(1)
    )
    assert [item["slot_count"] for item in diagnostics] == [
        sixteenth_slot_count(event) for event in events
    ]
    assert len(tokens) == sum(item["slot_count"] for item in diagnostics)
    assert all(token.endswith("s") for token in tokens)

    silence = ChordEvent(
        None, None, None, is_no_chord=True, duration_token="h."
    )
    calls = []

    class StubRandom:
        def choice(self, values):
            calls.append(("choice", tuple(values)))
            return values[0]

        def randrange(self, stop):
            calls.append(("randrange", stop))
            return 0

    tokens, diagnostics = serialize_arpeggios(
        [_voiced(0, [48, 52]), None, _voiced(2, [50, 53])],
        [
            ChordEvent(0, "major", 0, duration_token="q"),
            silence,
            ChordEvent(0, "major", 0, duration_token="q"),
        ],
        StubRandom(),
    )
    assert tokens[4:5] == ["Rh."]
    assert diagnostics[1]["slot_count"] == 0
    assert diagnostics[0]["pattern_family"] == diagnostics[2]["pattern_family"]
    assert [call[0] for call in calls] == [
        "choice", "randrange", "choice", "randrange"
    ]


class _StubEngine:
    def __init__(self, policy, ctx, root_omission_gate=False):
        self.policy = policy

    def run(self, song):
        return [
            None if chord.is_no_chord else _voiced(
                index, [48, 52, 67]
            )
            for index, chord in enumerate(song.chords)
        ]


class _DenseStubEngine(_StubEngine):
    def run(self, song):
        return [
            None if chord.is_no_chord else _voiced(
                index, [48, 52, 60, 64, 67, 72, 79]
            )
            for index, chord in enumerate(song.chords)
        ]


def test_pad_and_arpeggio_voicing_is_mode_invariant():
    progression = {
        "genre": "jazz",
        "tonic_pc": 2,
        "bpm": 120,
        "chords": [{
            "is_no_chord": False,
            "root_interval": 5,
            "triad": "minor",
            "bass_interval": 4,
            "seventh": "b7",
            "ninth": "9",
            "eleventh": "N",
            "thirteenth": "N",
            "root": "A",
            "bass": "C",
            "harte": "A:min7(9)/C",
            "duration_token": "q.",
        }],
    }
    order = ["jazz-piano", "jazz-guitar", "jazz-synth"]
    pads = chord_module.ChordModule(mode="pads", seed=7)
    arpeggios = chord_module.ChordModule(mode="arpeggios", seed=7)
    pads.render(progression, bass_module_active=True, voicer_order=order)
    arpeggios.render(
        progression, bass_module_active=True, voicer_order=order
    )

    assert arpeggios.last_voicer == pads.last_voicer
    assert arpeggios.last_voiced_midis == pads.last_voiced_midis
    assert arpeggios.last_voiced_roles == pads.last_voiced_roles
    assert arpeggios.last_voicing_diagnostics == pads.last_voicing_diagnostics
    assert len(arpeggios.last_arpeggio_diagnostics) == 1


def test_render_source_keeps_bass_and_percussion_isolated(monkeypatch):
    monkeypatch.setattr(chord_module, "Engine", _StubEngine)
    raw = json.dumps({
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [_event("q.")],
    }).encode()
    order = ["pop-piano", "pop-guitar", "pop-synth"]
    pads = _render_source((0, raw, 7, "pads", order))
    arpeggios = _render_source((0, raw, 7, "arpeggios", order))
    assert pads["bass_track"] == arpeggios["bass_track"]
    assert pads["percussion_track"] == arpeggios["percussion_track"]
    assert pads["percussion_included"] == arpeggios["percussion_included"]
    assert pads["percussion_feel"] == arpeggios["percussion_feel"]
    assert arpeggios["pad_collapse"] is False


def test_render_song_keeps_bass_and_percussion_isolated(monkeypatch):
    monkeypatch.setattr(chord_module, "Engine", _StubEngine)
    progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [_event("q.")],
    }
    pads = render_song(progression, seed=7, mode="pads").split("  ")
    arpeggios = render_song(
        progression, seed=7, mode="arpeggios"
    ).split("  ")
    assert pads[1] == arpeggios[1]
    assert pads[2] == arpeggios[2]


def test_arpeggio_catalog_covers_every_chord_family():
    for mode in ("pads", "arpeggios"):
        chord_module.validate_chord_instrument_catalog(mode)


def test_arpeggio_manifest_validates_expanded_track(tmp_path, monkeypatch):
    monkeypatch.setattr(chord_module, "Engine", _StubEngine)
    raw = json.dumps({
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "num_chords": 1,
        "chords": [_event("q.")],
    }, separators=(",", ":")).encode()
    label_path = tmp_path / "song_0.json"
    label_path.write_bytes(raw)
    result = _render_source((
        0, raw, 7, "arpeggios", ["pop-piano", "pop-guitar", "pop-synth"]
    ))
    score_path = tmp_path / "scores.txt"
    score_path.write_text(
        "START_SONG_0\n"
        f"{result['chord_track']}  {result['bass_track']}  "
        f"{result['percussion_track']}\n"
        "END_SONG\n"
    )
    manifest_path = tmp_path / "scores.txt.manifest.json"
    manifest_path.write_text(json.dumps({
        "records": [{
            "ordinal": 0,
            "source_dir": str(tmp_path),
            "source_file": label_path.name,
            "source_id": 0,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "genre": "pop_rock",
            "tonic_pc": 0,
            "bpm": 120,
            "num_chords": 1,
            "voicer_family": "piano",
            "render_mode": "arpeggios",
            "arpeggio": result["arpeggio"],
        }]
    }))

    report = validate_corpus(
        tmp_path, score_path, manifest_path, expected_genre="pop_rock"
    )
    assert report["pairing_errors"] == 0
    assert report["hard_failure_count"] == 0

    bad_score = score_path.read_text().replace("C3/", "D3/", 1)
    score_path.write_text(bad_score)
    report = validate_corpus(
        tmp_path, score_path, manifest_path, expected_genre="pop_rock"
    )
    assert any(
        issue["category"] == "arpeggio_pitch_not_in_source_voicing"
        for issue in report["issues"]
    )

    score_path.write_text(
        score_path.read_text().replace(result["bass_track"], "V1 I32")
    )
    report = validate_corpus(
        tmp_path, score_path, manifest_path, expected_genre="pop_rock"
    )
    assert report["hard_failure_count"] > 0
    assert any(
        issue["category"] == "arpeggio_bass_timeline_mismatch"
        for issue in report["issues"]
    )


def test_arpeggio_pad_fallback_manifest_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(chord_module, "Engine", _DenseStubEngine)
    raw = json.dumps({
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "num_chords": 1,
        "chords": [_event("q")],
    }, separators=(",", ":")).encode()
    label_path = tmp_path / "song_0.json"
    label_path.write_bytes(raw)
    result = _render_source((
        0, raw, 7, "arpeggios", ["pop-piano", "pop-guitar", "pop-synth"]
    ))

    assert result["arpeggio"]["event_pad_fallbacks"] == [True]
    assert (
        "@0 C3q+E3q+C4q+E4q+G4q+C5q+G5q"
        in result["chord_track"]
    )

    score_path = tmp_path / "scores.txt"
    score_path.write_text(
        "START_SONG_0\n"
        f"{result['chord_track']}  {result['bass_track']}  "
        f"{result['percussion_track']}\n"
        "END_SONG\n"
    )
    manifest_path = tmp_path / "scores.txt.manifest.json"
    manifest_path.write_text(json.dumps({
        "records": [{
            "ordinal": 0,
            "source_dir": str(tmp_path),
            "source_file": label_path.name,
            "source_id": 0,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "genre": "pop_rock",
            "tonic_pc": 0,
            "bpm": 120,
            "num_chords": 1,
            "voicer_family": "piano",
            "render_mode": "arpeggios",
            "arpeggio": result["arpeggio"],
        }]
    }))

    report = validate_corpus(
        tmp_path, score_path, manifest_path, expected_genre="pop_rock"
    )
    assert report["pairing_errors"] == 0
    assert report["hard_failure_count"] == 0


class _DeterministicRandom:
    def choices(self, values, weights=None, k=1):
        return [values[0]]

    def choice(self, values):
        return values[0]

    def randrange(self, stop):
        return 0

    def random(self):
        return 1.0


class _PairRandom(_DeterministicRandom):
    def random(self):
        return 0.0


def _profile(
    family="keyboard",
    normal="sixteenth",
    fast="eighth",
    threshold=120,
    gate=1.0,
    base_velocity=80,
    patterns=None,
    motifs=None,
    intervals=(100,),
    eighth_probability=0.0,
    pair_probability=0.0,
):
    return ArpeggioProfile(
        family,
        threshold,
        normal,
        fast,
        gate,
        base_velocity,
        patterns or {"up": 1.0},
        motifs or {"straight": 1.0},
        intervals,
        eighth_probability,
        pair_probability,
    )


def _scheduled_voiced(index, midis, strings=None, dct_pitch=None):
    diagnostics = {}
    if strings is not None:
        diagnostics["sounding_strings"] = list(strings)
    return VoicedChord(
        index,
        "test",
        list(midis),
        [],
        dct_pitch,
        None,
        None,
        0.0,
        sum(midis) / len(midis),
        diagnostics,
    )


def _schedule(
    voiced,
    events,
    profile,
    bpm=120,
    rng=None,
):
    rng = rng or _DeterministicRandom()
    return schedule_arpeggios(
        voiced,
        events,
        bpm,
        profile,
        rng,
        pattern_rng=rng,
        motif_rng=rng,
        change_rng=rng,
    )


def test_arpeggio_profiles_select_normal_and_fast_grids_at_threshold():
    events = [ChordEvent(0, "major", 0, duration_token="q.")]
    for profile in ARPEGGIO_PROFILES.values():
        strings = [6, 5, 1] if profile.family == "guitar" else None
        voiced = [_scheduled_voiced(0, [48, 52, 67], strings, 48)]
        _, normal_diagnostics = _schedule(
            voiced, events, profile, profile.fast_bpm_threshold
        )
        _, fast_diagnostics = _schedule(
            voiced, events, profile, profile.fast_bpm_threshold + 1
        )
        assert normal_diagnostics[0]["subdivision"] == profile.normal_subdivision
        assert fast_diagnostics[0]["subdivision"] == profile.fast_subdivision


def test_arpeggio_scheduler_keeps_phase_across_chords_and_resets_at_n():
    events = [
        ChordEvent(0, "major", 0, duration_token="q"),
        ChordEvent(0, "major", 0, duration_token="q"),
        ChordEvent(None, None, None, is_no_chord=True, duration_token="q"),
        ChordEvent(0, "major", 0, duration_token="q"),
    ]
    voiced = [
        _scheduled_voiced(0, [48, 52, 67]),
        _scheduled_voiced(1, [50, 55, 69]),
        None,
        _scheduled_voiced(3, [51, 56, 68]),
    ]
    _, diagnostics = _schedule(voiced, events, _profile())
    assert [item["start_phase"] for item in diagnostics] == [0, 0, None, 0]
    assert diagnostics[2]["onset_count"] == 0
    assert diagnostics[3]["event_start_sixteenths"] == 12


def test_guitar_arpeggios_follow_declared_string_order():
    events = [ChordEvent(0, "major", 0, duration_token="q")]
    voiced = [_scheduled_voiced(0, [50, 60, 70], [1, 6, 3], 50)]
    ascending, _ = _schedule(
        voiced,
        events,
        _profile("guitar", patterns={"up": 1.0}),
    )
    descending, _ = _schedule(
        voiced,
        events,
        _profile("guitar", patterns={"down": 1.0}),
    )
    assert [attack.source_index for attack in ascending[:3]] == [1, 2, 0]
    assert [attack.source_index for attack in descending[:3]] == [0, 2, 1]


def test_guitar_arpeggios_reject_invalid_string_provenance():
    events = [ChordEvent(0, "major", 0, duration_token="q")]
    for strings in ([1, 6], [1, 1, 3], [1, 7, 3]):
        with pytest.raises(ValueError, match="sounding_strings"):
            _schedule(
                [_scheduled_voiced(0, [50, 60, 70], strings)],
                events,
                _profile("guitar"),
            )


def test_arpeggio_motifs_protect_endpoints_and_dct():
    events = [ChordEvent(0, "major", 0, duration_token="w")]
    voiced = [_scheduled_voiced(0, [48, 52, 55, 60], [6, 5, 4, 1], 52)]
    _, diagnostics = _schedule(
        voiced,
        events,
        _profile(
            motifs={"light_alternate": 1.0},
            patterns={"up": 1.0},
        ),
    )
    assert diagnostics[0]["motif_family"] == "light_alternate"
    assert set(attack.source_index for attack in _schedule(
        voiced,
        events,
        _profile(
            motifs={"light_alternate": 1.0},
            patterns={"up": 1.0},
        ),
    )[0]) == {0, 1, 2, 3}
    small_diagnostics = _schedule(
        [_scheduled_voiced(0, [48, 55], [6, 1], 48)],
        [ChordEvent(0, "major", 0, duration_token="q")],
        _profile(
            family="guitar",
            motifs={"bass_return": 1.0},
            patterns={"up": 1.0},
        ),
    )[1]
    assert small_diagnostics[0]["motif_family"] == "straight"


def test_arpeggio_pattern_changes_only_at_source_boundaries():
    events = [
        ChordEvent(0, "major", 0, duration_token="q")
        for _ in range(9)
    ]
    voiced = [
        _scheduled_voiced(index, [48, 52, 67])
        for index in range(len(events))
    ]
    _, diagnostics = _schedule(
        voiced,
        events,
        _profile(
            patterns={"up": 1.0, "down": 1.0},
            intervals=(2,),
        ),
    )
    assert [item["pattern_family"] for item in diagnostics[:8]] == ["up"] * 8
    assert diagnostics[8]["pattern_family"] == "down"
    assert diagnostics[8]["event_start_sixteenths"] == 32


def test_arpeggio_accents_are_profile_driven_and_reach_event_end():
    events = [ChordEvent(0, "major", 0, duration_token="w")]
    voiced = [_scheduled_voiced(0, [48, 52, 55])]
    ringing, _ = _schedule(
        voiced,
        events,
        _profile(gate=1.5, base_velocity=80),
    )
    dry, _ = _schedule(
        voiced,
        events,
        _profile(gate=0.8, base_velocity=80),
    )
    assert [attack.velocity for attack in ringing] == [90, 76, 80]
    assert [attack.duration_sixteenths for attack in ringing] == [16, 15, 14]
    assert [attack.duration_sixteenths for attack in dry] == [16, 15, 14]


def test_profile_arpeggio_plays_each_voicing_note_once_to_event_end():
    event = ChordEvent(0, "major", 0, duration_token="q")
    voiced = [_scheduled_voiced(0, [48, 52, 55])]
    attacks, diagnostics = _schedule(
        voiced,
        [event],
        _profile(),
    )

    assert len(attacks) == len(voiced[0].midi)
    assert [attack.source_index for attack in attacks] == [0, 1, 2]
    assert len({attack.source_index for attack in attacks}) == 3
    assert all(
        attack.onset_sixteenths + attack.duration_sixteenths == 4
        for attack in attacks
    )
    assert diagnostics[0]["attack_count"] == 3
    assert diagnostics[0]["pair_count"] == 0


def test_arpeggio_forces_pairs_to_preserve_eighth_note_tail():
    event = ChordEvent(0, "major", 0, duration_token="q")
    voiced = [_scheduled_voiced(0, [48, 52, 55, 60])]
    attacks, diagnostics = _schedule(
        voiced,
        [event],
        _profile(pair_probability=0.0),
    )

    assert diagnostics[0]["subdivision"] == "sixteenth"
    assert diagnostics[0]["pair_count"] == 1
    assert [attack.onset_sixteenths for attack in attacks] == [0, 1, 2, 2]
    assert all(attack.duration_sixteenths >= 2 for attack in attacks)


def test_short_arpeggio_event_falls_back_to_pad():
    event = ChordEvent(0, "major", 0, duration_token="q")
    voiced = [_scheduled_voiced(0, list(range(48, 55)))]
    attacks, diagnostics = _schedule(
        voiced,
        [event],
        _profile(pair_probability=0.0),
    )

    assert attacks == []
    assert diagnostics[0]["render_mode"] == "pad"
    assert diagnostics[0]["pad_fallback"] is True
    assert diagnostics[0]["fallback_reason"] == (
        "insufficient_eighth_note_sustain"
    )
    tokens = serialize_scheduled_arpeggios(attacks, [event], diagnostics)
    assert tokens[:2] == [
        "@0",
        "C3q+C#3q+D3q+D#3q+E3q+F3q+F#3q",
    ]


def test_eighth_grid_requires_enough_time_for_one_pass():
    enough_time = [ChordEvent(0, "major", 0, duration_token="q")]
    enough_voicing = [_scheduled_voiced(0, [48, 52, 55, 60])]
    profile = _profile(eighth_probability=1.0)
    attacks, diagnostics = _schedule(
        enough_voicing,
        enough_time,
        profile,
        bpm=100,
        rng=_PairRandom(),
    )
    assert diagnostics[0]["subdivision"] == "eighth"
    assert diagnostics[0]["onset_count"] == 2
    assert [attack.onset_sixteenths for attack in attacks] == [0, 0, 2, 2]

    too_short = [ChordEvent(0, "major", 0, duration_token="q")]
    too_many_voices = [_scheduled_voiced(0, [48, 52, 55, 60, 64])]
    _, short_diagnostics = _schedule(
        too_many_voices,
        too_short,
        profile,
        bpm=100,
        rng=_PairRandom(),
    )
    assert short_diagnostics[0]["subdivision"] == "sixteenth"


def test_pair_probability_groups_adjacent_pattern_notes_at_one_onset():
    event = ChordEvent(0, "major", 0, duration_token="q")
    voiced = [_scheduled_voiced(0, [48, 52, 55])]
    attacks, diagnostics = _schedule(
        voiced,
        [event],
        _profile(pair_probability=1.0),
        rng=_PairRandom(),
    )
    assert len(attacks) == 3
    assert diagnostics[0]["onset_count"] == 2
    assert diagnostics[0]["attack_count"] == 3
    assert diagnostics[0]["pair_count"] == 1
    assert [attack.onset_sixteenths for attack in attacks] == [0, 0, 1]
    tokens = serialize_scheduled_arpeggios(
        attacks,
        [event],
        diagnostics,
    )
    assert "+" in tokens[1]


def test_timed_arpeggio_serialization_uses_absolute_numeric_notes():
    events = [ChordEvent(0, "major", 0, duration_token="q")]
    voiced = [_scheduled_voiced(0, [48, 52, 55])]
    attacks, diagnostics = _schedule(
        voiced,
        events,
        _profile(gate=1.5, base_velocity=80),
    )
    tokens = serialize_scheduled_arpeggios(attacks, events, diagnostics)
    assert tokens[:2] == ["@0", "C3/0.25A90"]
    assert tokens[2] == "@0.0625"
    note_tokens = [token for token in tokens if "/" in token and "A" in token]
    assert all("/" in token and "A" in token for token in note_tokens)
    assert tokens[-2:] == ["@0.25", "#ARPEVENT0"]
    assert isinstance(attacks[0], ArpeggioAttack)


def test_timed_boundaries_extend_short_final_gates_to_source_end():
    events = [ChordEvent(0, "major", 0, duration_token="q")]
    voiced = [_scheduled_voiced(0, [48, 52, 55])]
    attacks, diagnostics = _schedule(
        voiced,
        events,
        _profile(gate=0.8, base_velocity=80),
    )
    assert attacks[-1].onset_sixteenths + attacks[-1].duration_sixteenths == 4
    parsed = _parse_track_details(
        serialize_scheduled_arpeggios(attacks, events, diagnostics)
    )
    assert _track_timeline_sixteenths(parsed, absolute=True) == 4
