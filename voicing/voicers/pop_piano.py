"""
voicing/voicers/pop_piano.py -- spec 01, the reference/calibration voicer.
"""
from __future__ import annotations

from ..policy import VoicerPolicy
from ..candidates import (
    Candidate,
    hand_split_free_placement_templates,
    needs_rare_templates,
    template_profiles_for,
)
from ..engine import CandidateGenParams
from ..types import resolve_degrees

LH_WINDOW = (43, 60)
RH_WINDOW = (55, 88)
LH_MAX_VOICES = 3
RH_MAX_VOICES = 4
MAX_HAND_SPAN = 14


def candidate_source(p: CandidateGenParams) -> list:
    role_pcs = [(d.role, (p.chord.root_interval + d.semitone) % 12) for d in p.degrees]
    # `p.degrees` is the post-thinning degree set (spec 07 §4.1: a 5th can
    # be probabilistically omitted from the core voicing while remaining a
    # valid *doubling* target). Without a pc for it, doubling that role is
    # a silent no-op -- which can starve a low-cardinality chord (e.g. a
    # bare `maj(9)` with root+5th both absent from `degrees`) of any
    # candidate reaching `min_voices`. Resolve the *full*, untrimmed degree
    # set once so every doubling-eligible role always has a known pc.
    full_degrees = resolve_degrees(p.chord)
    doubling_pcs = {d.role: (p.chord.root_interval + d.semitone) % 12 for d in full_degrees}
    # Respect the engine's relaxation-ladder window widening (spec 07 §9
    # step 6): extend the outer hand bounds if the effective window is
    # wider than the nominal LH/RH windows.
    lh_window = (min(LH_WINDOW[0], p.window_lo), LH_WINDOW[1])
    rh_window = (RH_WINDOW[0], max(RH_WINDOW[1], p.window_hi))
    profiles = template_profiles_for(p.chord, p.policy)
    return hand_split_free_placement_templates(
        role_pcs, lh_window, rh_window, LH_MAX_VOICES, RH_MAX_VOICES, MAX_HAND_SPAN,
        p.anchor_center, p.prev_midi, p.doubling_roles, p.max_doublings, p.rng,
        anchor_shift=p.anchor_shift, doubling_pcs=doubling_pcs,
        cluster_min_gap=p.policy.extra.get("cluster_min_gap", 13),
        templates=profiles,
        preferred_template=(
            p.ctx.get("_template_target")
            if needs_rare_templates(p.chord) else None
        ),
    )


def post_filter(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> bool:
    if candidate.hands is None:
        return True
    for hand_pitches in candidate.hands.values():
        if hand_pitches and (max(hand_pitches) - min(hand_pitches)) > MAX_HAND_SPAN:
            return False

    # spec 01 §7 hard rule (maj7/dom7 root-masking, N19's dominant confusion
    # family): when the DCT is the chord's 7th (maj7's `7` or dom7's `b7`),
    # no root copy may sit 1-2 semitones above it -- that's exactly the
    # interval that buries a 7th under a doubled root. This is stricter
    # than spec 07 §5.2's generic exposure predicate (which only forbids
    # tones 1-2 semitones *below* the DCT), so it needs its own check here.
    chord = ctx.get("_current_chord")
    dct_role = ctx.get("_current_dct_role")
    dct_pc = ctx.get("_current_dct_pc")
    if chord is not None and dct_role == "7th" and dct_pc is not None and \
            chord.triad == "major" and chord.seventh in ("7", "b7"):
        dct_pitches = [p for p, r in zip(candidate.pitches, candidate.roles)
                       if r == "7th" and p % 12 == dct_pc]
        root_pitches = [p for p, r in zip(candidate.pitches, candidate.roles) if r == "root"]
        for dp in dct_pitches:
            for rp in root_pitches:
                if 1 <= (rp - dp) <= 2:
                    return False

    return True


def role_penalty(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> float:
    penalty = 0.0
    hands = candidate.hands or {}
    lh = hands.get("lh", [])
    rh = hands.get("rh", [])

    if lh and rh and max(lh) >= 55 and min(rh) <= 60:
        # hands overlap in the 55-60 band
        penalty += 1.0

    s = sorted(rh)
    for i in range(len(s) - 1):
        if s[i + 1] - s[i] > 12:
            penalty += 0.6

    for p, role in zip(candidate.pitches, candidate.roles):
        if role == "3rd" and p in lh and p < 52:
            penalty += 1.2

    if not ctx.get("bass_module_active") and (not candidate.pitches or min(candidate.pitches) >= 55):
        penalty += 2.0

    all_sorted = sorted(candidate.pitches)
    close_pairs = sum(1 for i in range(len(all_sorted) - 1) if all_sorted[i + 1] - all_sorted[i] <= 2)
    if close_pairs > 2:
        penalty += 0.8

    chord = ctx.get("_current_chord")
    if chord is not None and chord.thirteenth != "N":
        if "7th" not in candidate.roles:
            penalty += 0.7  # 13th with no audible 7th reads as a 6th chord

    return penalty


SECTION_PROFILE = {
    "verse": {"drift_free": 4, "max_voices": 5, "root_double_p": 0.60},
    "prechorus": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.70},
    "chorus": {"drift_free": 8, "max_voices": 7, "root_double_p": 0.85, "anchor_shift": 7},
    "bridge": {"drift_free": 6, "max_voices": 6, "root_double_p": 0.65, "anchor_shift": -3},
    "outro": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.70},
}

SIGNATURE_BANDS = {"low": (43, 54), "mid": (55, 67), "high": (68, 88)}

POLICY = VoicerPolicy(
    voicer_id="pop-piano", genre="pop_rock", instrument="piano",
    window_lo=43, window_hi=88, drift_free=5, drift_tol=11,
    min_voices=3, max_voices=7, p_omit_fifth=0.15, root_double_p=0.70,
    root_omission_p=0.02, max_doublings=3,
    tau=3.5, w_vl=1.0, w_drift=0.7, w_space=0.6, w_div=1.1, w_role=0.8,
    plr_bonus=1.5, unmatched_penalty=2.0, vl_target_mean=8.4, vl_target_sd=4.2,
    dct_mode_weights={"top": 0.50, "isolated": 0.20, "octave": 0.30},
    candidate_source=candidate_source, role_penalty=role_penalty, post_filter=post_filter,
    section_profile=SECTION_PROFILE,
    extra={
        "doubling_targets": ("root", "5th"),
        "cluster_cap": 4,
        "spacing_floor": 3,
        "spacing_floor_low": 6,
        "spacing_exception_tensions": 2,
        "spacing_exception_voices": 6,
        "spacing_exception_max_gaps": 1,
        "template_profiles": ("balanced", "open", "closed", "root_spread"),
        "rare_template_profiles": ("rare_feature", "tension_top"),
        "template_mismatch_penalty": 1.3,
        "rare_template_mismatch_penalty": 1.8,
    },
)
