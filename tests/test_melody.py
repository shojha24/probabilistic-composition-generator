from dataclasses import replace
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instruments import CHORD_INSTRUMENTS, MELODY_INSTRUMENTS
from melody_module import (
    MelodyGenerationConfig,
    _build_rhythm_plan,
    enumerate_melody_candidates,
    generate_melody,
    get_melody_profile,
    infer_local_key_context,
    melody_inclusion_plan,
    melody_quota_count,
    resolve_scale_pcs,
    select_melody_instrument,
)
from render import render_song_tracks
from voicing.types import ChordEvent, Song, resolve_degrees


def _chord(
    *,
    root_interval=0,
    triad="major",
    duration_token="q",
    seventh="N",
    ninth="N",
    eleventh="N",
    thirteenth="N",
):
    return ChordEvent(
        root_interval=root_interval,
        triad=triad,
        bass_interval=0,
        seventh=seventh,
        ninth=ninth,
        eleventh=eleventh,
        thirteenth=thirteenth,
        duration_token=duration_token,
    )


def _no_chord(duration_token="q"):
    return ChordEvent(
        root_interval=None,
        triad=None,
        bass_interval=None,
        root=None,
        bass=None,
        harte="N",
        is_no_chord=True,
        duration_token=duration_token,
    )


def _song(*chords, tonic_pc=0, mode=None, scale_pcs=None):
    return Song(
        "pop_rock",
        tonic_pc,
        120,
        len(chords),
        tuple(chords),
        mode=mode,
        scale_pcs=scale_pcs,
    )


def test_collision_merged_degrees_are_preserved_in_melody_candidates():
    sus2_ninth = _chord(triad="sus2", ninth="9")
    diminished_sharp_eleven = _chord(triad="diminished", eleventh="#11")

    sus_degrees = resolve_degrees(sus2_ninth)
    ninth = next(degree for degree in sus_degrees if degree.role == "9th")
    assert ninth.merged_from == ("3rd",)
    candidates = enumerate_melody_candidates(
        _song(sus2_ninth),
        0,
        profile="lead-mid-neutral",
    )
    candidate = next(
        item for item in candidates
        if item.degree_role == "9th" and item.pitch_source == "source_degree"
    )
    assert candidate.merged_from == ("3rd",)

    diminished_degrees = resolve_degrees(diminished_sharp_eleven)
    eleventh = next(
        degree for degree in diminished_degrees if degree.role == "11th"
    )
    assert eleventh.merged_from == ("5th",)


def test_tonic_relative_scale_precedence_and_absolute_pitch_classes():
    assert resolve_scale_pcs(5, "major", (0, 1)) == (5, 6)
    song = _song(
        _chord(),
        tonic_pc=5,
        mode="major",
        scale_pcs=(0, 1),
    )
    context = infer_local_key_context(
        song,
        0,
        16,
        MelodyGenerationConfig(),
    )
    assert context.source == "explicit"
    assert context.scale_pcs == (5, 6)
    candidates = enumerate_melody_candidates(
        song,
        0,
        profile="lead-mid-neutral",
        config=MelodyGenerationConfig(candidate_limit_per_onset=64),
    )
    assert any(
        candidate.pitch_source == "source_degree"
        and candidate.midi % 12 == 5
        for candidate in candidates
        if candidate.midi is not None
    )
    assert any(
        candidate.pitch_source == "global_scale"
        and candidate.midi % 12 == 6
        for candidate in candidates
        if candidate.midi is not None
    )


def test_chromatic_candidates_are_targeted_and_respect_profile_leaps():
    song = _song(_chord(), _chord(root_interval=7))
    profile = get_melody_profile("lead-mid-neutral")
    candidates = enumerate_melody_candidates(
        song,
        0,
        previous_midi=74,
        profile=profile,
        config=MelodyGenerationConfig(candidate_limit_per_onset=64),
    )
    assert {
        candidate.role
        for candidate in candidates
        if candidate.pitch_source == "chromatic_neighbor"
    } & {"approach", "passing", "neighbor", "enclosure"}
    assert all(
        candidate.midi is None
        or abs(candidate.midi - 74) <= profile.max_leap_semitones
        for candidate in candidates
    )
    assert all(
        candidate.resolution_target is not None
        for candidate in candidates
        if candidate.pitch_source == "chromatic_neighbor"
    )


def test_realized_voicing_candidates_keep_optional_provenance():
    song = _song(_chord(seventh="7"))
    candidates = enumerate_melody_candidates(
        song,
        0,
        profile="lead-mid-neutral",
        realized_voicing={
            "midi": [60, 64, 67, 70],
            "roles": ["root", "3rd", "5th", "7th"],
        },
        config=MelodyGenerationConfig(candidate_limit_per_onset=64),
    )
    realized = [
        candidate for candidate in candidates
        if candidate.pitch_source == "realized_voicing"
    ]
    assert realized
    assert any(
        candidate.degree_role == "7th" and candidate.role == "extension"
        for candidate in realized
    )


def test_rhythm_holds_cover_playable_boundaries_but_stop_at_no_chord():
    base = get_melody_profile("lead-mid-active")
    profile = replace(
        base,
        density_weights={"moderate": 1.0},
        rhythm_weights={"half": 1.0},
        rest_probability=0.0,
        hold_probability=1.0,
    )
    playable_slots = _build_rhythm_plan(
        _song(_chord(), _chord(root_interval=7)),
        profile,
        13,
    )
    assert playable_slots[0].source_event_indices == (0, 1)
    assert playable_slots[0].duration == 8

    blocked_slots = _build_rhythm_plan(
        _song(_chord(), _no_chord(), _chord(root_interval=7)),
        profile,
        13,
    )
    assert all(1 not in slot.source_event_indices for slot in blocked_slots if not slot.is_forced_rest)
    assert any(
        slot.is_forced_rest and slot.source_event_indices == (1,)
        for slot in blocked_slots
    )


def test_generation_resets_at_no_chord_and_is_reproducible_for_both_decoders():
    song = _song(
        _chord(duration_token="h"),
        _no_chord(duration_token="q"),
        _chord(root_interval=7, duration_token="h"),
    )
    for decoder in ("chord_centered_random_walk", "sequence_beam"):
        config = MelodyGenerationConfig(decoder=decoder)
        first = generate_melody(song, seed=42, config=config)
        second = generate_melody(song, seed=42, config=config)
        assert first.events == second.events
        assert first.metadata == second.metadata
        assert all(
            event.midi is None
            for event in first.events
            if 1 in event.source_event_indices
        )
        later_pitched = [
            event for event in first.events
            if event.midi is not None
            and min(event.source_event_indices) >= 2
        ]
        if later_pitched:
            assert later_pitched[0].arrival_interval is None


def test_quota_is_exact_nearest_whole_song_and_stable():
    assert melody_quota_count(0, 70) == 0
    assert melody_quota_count(3, 0) == 0
    assert melody_quota_count(3, 100) == 3
    assert melody_quota_count(10, 70) == 7
    first = melody_inclusion_plan(10, 70, 99, source_ids=list(range(10)))
    repeated = melody_inclusion_plan(10, 70, 99, source_ids=list(range(10)))
    reordered = melody_inclusion_plan(
        10,
        70,
        99,
        source_ids=list(reversed(range(10))),
    )
    assert first == repeated
    assert sum(first) == 7
    assert sum(reordered) == 7
    assert first != reordered


def test_local_key_falls_back_when_window_is_ambiguous():
    context = infer_local_key_context(
        _song(_no_chord()),
        0,
        4,
        MelodyGenerationConfig(),
    )
    assert context.source == "global_fallback"
    assert context.fallback_reason is not None


def test_melody_instrument_collapse_boundaries_are_isolated():
    chord_instrument = next(iter(CHORD_INSTRUMENTS.values()))[0]
    melody_catalog_programs = {
        instrument.program for instrument in MELODY_INSTRUMENTS
    }
    not_collapsed = select_melody_instrument(
        "lead-mid-neutral",
        17,
        chord_instrument=chord_instrument,
        collapse_probability=0.0,
    )
    collapsed = select_melody_instrument(
        "lead-mid-neutral",
        17,
        chord_instrument=chord_instrument,
        collapse_probability=1.0,
    )
    assert not_collapsed["collapsed_to_chord"] is False
    assert not_collapsed["instrument_source"] == "melody_catalog"
    assert not_collapsed["instrument_program"] in melody_catalog_programs
    assert collapsed["collapsed_to_chord"] is True
    assert collapsed["instrument_source"] == "chord_collapse"
    assert collapsed["instrument_program"] == chord_instrument.program
    assert (
        not_collapsed["melody_catalog_program"]
        == collapsed["melody_catalog_program"]
    )


def test_melody_enablement_does_not_change_accompaniment_tracks():
    progression = {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": 120,
        "chords": [
            {
                "root_interval": 0,
                "triad": "major",
                "bass_interval": 0,
                "seventh": "7",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "duration_token": "q",
            },
            {
                "root_interval": 7,
                "triad": "major",
                "bass_interval": 0,
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "duration_token": "q",
            },
        ],
    }
    disabled = render_song_tracks(progression, seed=31, mode="pads")
    enabled = render_song_tracks(
        progression,
        seed=31,
        mode="pads",
        melody_condition="naturalistic",
        melody_profile="lead-mid-neutral",
        melody_collapse_probability=0.0,
        melody_decoder="sequence_beam",
    )
    assert enabled.melody_track is not None
    assert "V2" in enabled.mixed_line
    assert "V2" not in disabled.mixed_line
    assert enabled.chord_track == disabled.chord_track
    assert enabled.bass_track == disabled.bass_track
    assert enabled.percussion_track == disabled.percussion_track
