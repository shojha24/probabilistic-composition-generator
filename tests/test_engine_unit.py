"""Spec 07 §13 items 1-5: unit tests for the shared engine."""
import dataclasses
import itertools
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voicing.types import (
    ChordEvent, Song, resolve_degrees, TRIAD_THIRD_FIFTH, EXT_SEMI,
)
from voicing.dct import compute_dct, predicate_top, predicate_isolated, predicate_octave
from voicing.engine import Engine
from voicing.vl import vl_distance
from voicing.candidates import (
    Candidate,
    _clash_aware_free_candidates,
    _clash_bipartition,
    apply_doubling_variants,
    dense_hand_layout,
    dct_expose_repair,
    free_placement_templates,
    preferred_template_for,
)
from voicing.voicers import pop_piano, pop_synth
from voicing.guitar.derive import derive_shape
from voicing.guitar.library import make_shape_library_source
from voicing.guitar.model import Shape, realize
from voicing.spacing import low_interval_limit_ok, semitone_cluster_ok


def test_degree_resolution_all_triads_and_tokens():
    """Item 1: degree resolution matches §2.3 for all 8 triads x extension combos."""
    triads = list(TRIAD_THIRD_FIFTH.keys())
    ext_choices = {
        "seventh": ["N", "7", "b7", "bb7"],
        "ninth": ["N", "9", "b9", "#9"],
        "eleventh": ["N", "11", "#11"],
        "thirteenth": ["N", "13", "b13"],
    }
    combos = list(itertools.product(ext_choices["seventh"], ext_choices["ninth"],
                                      ext_choices["eleventh"], ext_choices["thirteenth"]))
    for triad in triads:
        for seventh, ninth, eleventh, thirteenth in combos:
            ce = ChordEvent(root_interval=0, triad=triad, bass_interval=0,
                             seventh=seventh, ninth=ninth, eleventh=eleventh, thirteenth=thirteenth)
            degs = resolve_degrees(ce)
            roles = {d.role for d in degs}
            assert "root" in roles
            third, fifth = TRIAD_THIRD_FIFTH[triad]
            if third is not None:
                assert "3rd" in roles or any(d.merged_from for d in degs if d.role != "3rd")
            for slot, token in (("seventh", seventh), ("ninth", ninth),
                                 ("eleventh", eleventh), ("thirteenth", thirteenth)):
                if token == "N":
                    continue
                # after merges, the token's semitone must appear somewhere
                semi = EXT_SEMI[slot][token]
                assert any(d.semitone == semi for d in degs), (triad, slot, token, degs)


def test_collisions_resolve_without_dropping_required_pc():
    """Item 2: all five collisions in §2.5 resolve without dropping a required pitch class."""
    cases = [
        dict(triad="diminished", seventh="bb7", ninth="N", eleventh="N", thirteenth="13"),
        dict(triad="sus4", seventh="N", ninth="N", eleventh="11", thirteenth="N"),
        dict(triad="sus2", seventh="N", ninth="9", eleventh="N", thirteenth="N"),
        dict(triad="diminished", seventh="N", ninth="N", eleventh="#11", thirteenth="N"),
        dict(triad="augmented", seventh="N", ninth="N", eleventh="N", thirteenth="b13"),
    ]
    for case in cases:
        ce = ChordEvent(root_interval=0, bass_interval=0, **case)
        degs = resolve_degrees(ce)
        semitones = {d.semitone for d in degs}
        # root always present
        assert 0 in semitones
        # every active token's semitone is represented exactly once (merged)
        third, fifth = TRIAD_THIRD_FIFTH[case["triad"]]
        for slot in ("seventh", "ninth", "eleventh", "thirteenth"):
            token = case[slot]
            if token != "N":
                semi = EXT_SEMI[slot][token]
                assert semi in semitones


def test_dct_matches_worked_examples():
    """Item 3."""
    maj7 = ChordEvent(root_interval=0, triad="major", bass_interval=0, seventh="7")
    role, sec = compute_dct(maj7, resolve_degrees(maj7))
    assert role == "7th"

    dom7 = ChordEvent(root_interval=0, triad="major", bass_interval=0, seventh="b7")
    role, sec = compute_dct(dom7, resolve_degrees(dom7))
    assert role == "7th"

    hdim7 = ChordEvent(root_interval=0, triad="diminished", bass_interval=0, seventh="b7")
    role, sec = compute_dct(hdim7, resolve_degrees(hdim7))
    assert role == "7th"

    nine = ChordEvent(root_interval=0, triad="major", bass_interval=0, seventh="b7", ninth="9")
    role, sec = compute_dct(nine, resolve_degrees(nine))
    assert role == "9th"
    assert "7th" in sec

    maj = ChordEvent(root_interval=0, triad="major", bass_interval=0)
    role, sec = compute_dct(maj, resolve_degrees(maj))
    assert role is None


def test_exposure_predicates_fixtures():
    """Item 4."""
    # (a) topmost
    assert predicate_top(74, [50, 57, 64, 68, 74])
    assert not predicate_top(64, [50, 57, 64, 68, 74])

    # (b) isolated: 3+ below, 2+ above
    assert predicate_isolated(64, [50, 57, 64, 71])  # 7 below, 7 above
    assert not predicate_isolated(64, [62, 64, 71])  # only 2 below -> fails
    assert not predicate_isolated(64, [50, 57, 64, 65])  # only 1 above -> fails
    assert predicate_isolated(64, [50, 57, 64, 66])  # exactly 2 above -> ok

    # (c) octave-exposed: doubled at +-12, lower copy isolated
    assert predicate_octave(64, [50, 57, 64, 76])  # 76 = 64+12, lower copy (64) isolated
    assert not predicate_octave(64, [50, 63, 64, 76])  # lower copy masked by 63


def test_vl_distance_symmetric_and_zero_and_unmatched():
    """Item 5."""
    a = [60, 64, 67]
    b = [60, 64, 67]
    assert vl_distance(a, b) == 0.0

    a = [60, 64, 67]
    b = [62, 64, 67]
    assert vl_distance(a, b) == vl_distance(b, a)
    assert vl_distance(a, b) == 2.0

    # different voice counts: unmatched voice charged unmatched_penalty
    a = [60, 64, 67]
    b = [60, 64, 67, 74]
    d = vl_distance(a, b, unmatched_penalty=2.0)
    assert d == 2.0

    # hand-computed matching: a=[60,64], b=[61,66] -> best matching is
    # 60->61 (1) + 64->66 (2) = 3, not 60->66(6)+64->61(3)=9
    assert vl_distance([60, 64], [61, 66]) == 3.0


def test_template_target_survives_candidate_pool_merge():
    """A non-balanced target must remain selectable after profile merging."""
    candidates = free_placement_templates(
        [("root", 0), ("3rd", 4), ("5th", 7)],
        43, 88, 60, None, ["root", "5th"], 2, random.Random(3),
        templates=("balanced", "root_spread"),
        preferred_template="root_spread",
        max_candidates=40,
    )
    assert any(c.meta.get("voicing_template") == "root_spread"
               for c in candidates)


def test_rare_chords_rotate_over_rare_template_subset():
    rare = ChordEvent(root_interval=0, triad="diminished", bass_interval=0)
    common = ChordEvent(root_interval=0, triad="major", bass_interval=0)
    rare_target = preferred_template_for(
        rare, pop_piano.POLICY, {"seed": 3, "_current_t": 0}
    )
    common_target = preferred_template_for(
        common, pop_piano.POLICY, {"seed": 3, "_current_t": 0}
    )
    assert rare_target in pop_piano.POLICY.extra["rare_template_profiles"]
    assert common_target in pop_piano.POLICY.extra["template_profiles"]


def test_derived_guitar_shape_prefers_low_register_safe_fingering():
    chord_type = ("sus4", "b7", "9", "11", "13")
    result = derive_shape(
        chord_type, 5, "sus4-extended-a-shape",
        prefer_low_interval_safe=True,
    )
    assert result is not None
    shape, _dropped = result
    realized = realize(shape, chord_root_pc=10, max_fret=12)
    assert realized is not None
    assert low_interval_limit_ok(realized["pitches"])


def test_forced_dct_copy_survives_single_variant_budget():
    variants = apply_doubling_variants(
        [("root", 60), ("7th", 71)],
        ["root"],
        {"root": 0, "7th": 11},
        48, 84, 1, random.Random(1),
        anchor_center=60, max_variants=1, forced_dct_role="7th",
    )
    assert len(variants) == 1
    assert sum(role == "7th" for role, _pitch in variants[0]) == 2
    assert abs(variants[0][-1][1] - 71) == 12


def test_dct_repair_forced_copy_respects_existing_doublings():
    candidate = Candidate([60, 71, 72], ["root", "7th", "root"])
    repairs = list(dct_expose_repair(
        candidate, "7th", [], "octave", 48, 84, 60,
        max_voices=4, max_doublings=1,
    ))
    assert not any(repair.meta["forced_dct_copy"] for repair in repairs)


def test_dense_layout_fallback_can_use_noncontiguous_hand_partition():
    layouts = dense_hand_layout(
        [("root", 0), ("3rd", 4), ("5th", 7), ("7th", 11)],
        (48, 60), (55, 71), 3, 2, 14, 60, 4,
        max_candidates=512,
    )
    assert layouts
    assert any(
        {role for pitch, role in zip(candidate.pitches, candidate.roles)
         if pitch in candidate.hands["lh"]} == {"root", "5th"}
        for candidate in layouts
    )


def test_dense_layout_forced_dct_respects_doubling_budget():
    layouts = dense_hand_layout(
        [("root", 0), ("3rd", 4), ("5th", 7), ("7th", 11)],
        (48, 60), (55, 71), 3, 2, 14, 60, 5,
        max_candidates=512, forced_dct_role="7th", max_doublings=0,
    )
    assert layouts
    assert all(not candidate.meta["forced_dct_copy"] for candidate in layouts)


def test_clash_fallback_keeps_non_clashing_roles_out_of_low_register_pack():
    role_pcs = [
        ("root", 4), ("3rd", 7), ("7th", 2),
        ("9th", 6), ("11th", 9), ("13th", 1),
    ]
    role_pc_lookup = dict(role_pcs)
    role_pc_lookup["5th"] = 11
    colors = _clash_bipartition(list(role_pc_lookup.items()), 13)
    candidates = _clash_aware_free_candidates(
        role_pcs, role_pc_lookup, colors, 36, 96, 62, random.Random(1),
        min_gap=13,
    )

    expected = [50, 55, 57, 64, 78, 85]
    assert any(candidate.pitches == expected for candidate in candidates)
    assert low_interval_limit_ok(expected)
    assert semitone_cluster_ok(expected, 13)


def test_post_filter_repair_can_use_dct_clean_candidate():
    chord = ChordEvent(
        root_interval=9, triad="minor", bass_interval=0,
        seventh="b7", ninth="9", eleventh="11", thirteenth="13",
    )
    source_candidate = Candidate(
        [38, 57, 59, 64, 80, 85],
        ["3rd", "7th", "root", "11th", "13th", "9th"],
    )
    policy = dataclasses.replace(
        pop_synth.POLICY,
        candidate_source=lambda _params: [source_candidate],
        dct_mode_weights={"top": 0.0, "isolated": 1.0, "octave": 0.0},
    )
    song = Song("pop_rock", 2, 120, 1, (chord,))

    voiced = Engine(
        policy,
        {"bass_module_active": True, "section": "bridge", "seed": 1},
        signature_bands=pop_synth.SIGNATURE_BANDS,
    ).run(song)[0]

    assert voiced.diagnostics["dct_repair"] is True
    assert voiced.midi == [50, 57, 59, 64, 80, 85]


def test_guitar_octave_source_phase_keeps_offset_metadata():
    shape = Shape(
        "E-dom7", 6, (0, 2, 0, 1, 0, 0),
        ("root", "5th", "7th", "3rd", "5th", "root"),
        (("major", "b7", "N", "N", "N"),), barre=True,
    )
    source = make_shape_library_source(
        [shape], {("major", "b7"): (shape,)}, max_fret=15, max_fret_span=4,
    )
    chord = ChordEvent(
        root_interval=0, triad="major", bass_interval=0, seventh="b7",
    )
    params = dict(
        chord=chord, root_pc=4, degrees=resolve_degrees(chord),
        dct_role="7th", ctx={"bass_module_active": True},
    )
    normal = source(type("Params", (), {**params, "source_phase": "normal"})())
    offset = source(type("Params", (), {
        **params, "source_phase": "guitar_octave",
    })())
    assert normal and all(candidate.meta["octave_offset"] == 0 for candidate in normal)
    assert offset and all(
        candidate.shape_id == "E-dom7@oct+12"
        and candidate.meta["base_shape_id"] == "E-dom7"
        and candidate.meta["octave_offset"] == 12
        for candidate in offset
    )


def test_alternate_guitar_root_assignment_is_tagged_and_realizable():
    result = derive_shape(
        ("major", "b7", "9", "N", "N"), 6, "alternate-dom9",
        retained_root_string=5,
    )
    assert result is not None
    shape, _dropped = result
    root_index = ("6", "5", "4", "3", "2", "1").index(str(5))
    assert shape.retained_root_string == 5
    assert shape.degrees[root_index] == "root"
    assert "alternate_root" in shape.tags
    realized = realize(shape, chord_root_pc=7, max_fret=15)
    assert realized is not None
    assert realized["root_fret"] >= shape.frets[root_index]


def test_rootless_derivation_does_not_retain_an_alternate_root():
    result = derive_shape(
        ("major", "b7", "9", "N", "N"), 6, "rootless-dom9",
        initial_dropped=("root",), retained_root_string=5,
    )
    assert result is not None
    shape, _dropped = result
    assert shape.retained_root_string is None
    assert "alternate_root" not in shape.tags


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
