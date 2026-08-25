"""Jazz piano voicer, spec 04.

This module keeps the shared engine's hand realization and cost pipeline, but
changes tone selection/register policy to the jazz shell convention:
rootless-by-default comping with a 3rd+7th LH skeleton and extensions in RH.
"""
from __future__ import annotations

from ..candidates import Candidate, hand_split_free_placement
from ..engine import CandidateGenParams
from ..policy import VoicerPolicy
from ..types import resolve_degrees

LH_WINDOW = (48, 67)
RH_WINDOW = (60, 84)
LH_MAX_VOICES = 3
RH_MAX_VOICES = 4
MAX_HAND_SPAN = 12


def _shell_roles(p: CandidateGenParams) -> tuple[str, ...]:
    roles = {d.role for d in p.degrees}
    third_role = "3rd"
    for degree in p.degrees:
        if "3rd" in degree.merged_from:
            third_role = degree.role
            break
    if "7th" in roles:
        return (third_role, "7th")
    if third_role in roles and "5th" in roles:
        return (third_role, "5th")
    if third_role in roles:
        return (third_role, "root")
    return ("root",)


def candidate_source(p: CandidateGenParams) -> list[Candidate]:
    degrees = list(p.degrees)
    # Rootless selection is only meaningful when the 3rd/7th shell can
    # carry quality. Keep a root for dyads and triads without a seventh.
    if ("7th" not in {d.role for d in degrees}
            and not any(d.role == "root" for d in degrees)):
        degrees.insert(0, next(d for d in resolve_degrees(p.chord)
                               if d.role == "root"))
    role_pcs = [(d.role, (p.chord.root_interval + d.semitone) % 12)
                for d in degrees]
    full_degrees = resolve_degrees(p.chord)
    doubling_pcs = {
        d.role: (p.chord.root_interval + d.semitone) % 12
        for d in full_degrees
    }
    lh_window = (min(LH_WINDOW[0], p.window_lo), LH_WINDOW[1])
    rh_window = (RH_WINDOW[0], max(RH_WINDOW[1], p.window_hi))
    raw = hand_split_free_placement(
        role_pcs, lh_window, rh_window, LH_MAX_VOICES, RH_MAX_VOICES,
        MAX_HAND_SPAN, p.anchor_center, p.prev_midi, p.doubling_roles,
        p.max_doublings, p.rng, anchor_shift=p.anchor_shift,
        doubling_pcs=doubling_pcs,
        cluster_min_gap=p.policy.extra.get("cluster_min_gap", 13),
    )
    shell = set(_shell_roles(p))
    out = []
    for candidate in raw:
        hands = candidate.hands or {}
        lh_roles = {
            role for pitch, role in zip(candidate.pitches, candidate.roles)
            if pitch in hands.get("lh", [])
        }
        if not shell.issubset(lh_roles):
            continue
        lh = hands.get("lh", [])
        rh = hands.get("rh", [])
        if lh and rh and min(rh) - max(lh) > 16:
            continue
        out.append(candidate)
    return out


def _rootless(candidate: Candidate) -> bool:
    return "root" not in candidate.roles


def post_filter(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> bool:
    hands = candidate.hands or {}
    lh_roles = {
        role for pitch, role in zip(candidate.pitches, candidate.roles)
        if pitch in hands.get("lh", [])
    }
    chord = ctx.get("_current_chord")
    if chord is None:
        return False

    degrees = resolve_degrees(chord)
    third_role = next((d.role for d in degrees if "3rd" in d.merged_from), "3rd")
    if chord.seventh != "N":
        if not {third_role, "7th"}.issubset(lh_roles):
            return False
    elif "3rd" not in {d.role for d in degrees} and not any(
            "3rd" in d.merged_from for d in degrees):
        if not {"root", "5th"}.issubset(lh_roles):
            return False
    elif third_role not in lh_roles or not ({"5th", "root"} & lh_roles):
        return False

    lh = hands.get("lh", [])
    rh = hands.get("rh", [])
    if lh and rh and min(rh) - max(lh) > 16:
        return False
    if any(max(hand) - min(hand) > MAX_HAND_SPAN for hand in hands.values() if hand):
        return False

    if not _rootless(candidate):
        return True

    # Rootless jazz voicings must retain the complete quality-bearing shell.
    if chord.seventh != "N" and not {third_role, "7th"}.issubset(candidate.roles):
        return False

    dct_role = ctx.get("_current_dct_role")
    if not dct_role:
        return True
    dct_pitches = [p for p, role in zip(candidate.pitches, candidate.roles)
                   if role == dct_role]
    if not dct_pitches:
        return False
    # Rootless 7th-DCT voicings may expose the differentiator only from the
    # top, or as the upper member of the LH shell with a clear RH separation.
    if dct_role == "7th":
        top = max(candidate.pitches)
        if not any(p == top for p in dct_pitches):
            if not lh or max(lh) not in dct_pitches or (rh and min(rh) - max(lh) < 3):
                return False
    return True


def role_penalty(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> float:
    penalty = 0.0
    hands = candidate.hands or {}
    lh = hands.get("lh", [])
    rh = hands.get("rh", [])
    if lh and rh and min(rh) - max(lh) > 16:
        penalty += 1.0
    if lh and any(p < 55 and role == "3rd"
                  for p, role in zip(candidate.pitches, candidate.roles)
                  if p in lh):
        penalty += 1.5
    if rh and any(rh[i + 1] - rh[i] > 12 for i in range(len(rh) - 1)):
        penalty += 0.6
    if candidate.roles.count("5th") and candidate.roles.count("13th"):
        penalty += 0.6
    if len(candidate.roles) > 6:
        penalty += 2.0
    return penalty


SECTION_PROFILE = {
    "verse": {"drift_free": 3, "max_voices": 5, "root_omission_p": 0.70},
    "prechorus": {"drift_free": 4, "max_voices": 5, "root_omission_p": 0.70},
    "chorus": {"drift_free": 5, "max_voices": 6, "root_omission_p": 0.65},
    "bridge": {"drift_free": 4, "max_voices": 5, "root_omission_p": 0.75},
    "outro": {"drift_free": 4, "max_voices": 6, "root_omission_p": 0.70},
}

SIGNATURE_BANDS = {"low": (48, 58), "mid": (59, 70), "high": (71, 84)}

POLICY = VoicerPolicy(
    voicer_id="jazz-piano", genre="jazz", instrument="piano",
    window_lo=48, window_hi=84, drift_free=4, drift_tol=8,
    min_voices=3, max_voices=6, p_omit_fifth=0.60, root_double_p=0.05,
    root_omission_p=0.70, max_doublings=1,
    tau=2.2, w_vl=1.6, w_drift=0.8, w_space=0.7, w_div=0.9, w_role=1.0,
    plr_bonus=3.0, unmatched_penalty=2.5, vl_target_mean=6.0, vl_target_sd=3.5,
    dct_mode_weights={"top": 0.55, "isolated": 0.40, "octave": 0.05},
    candidate_source=candidate_source, role_penalty=role_penalty,
    post_filter=post_filter, section_profile=SECTION_PROFILE,
    extra={
        "doubling_targets": ("root", "5th"),
        "fixed_voice_count": False,
        "cluster_min_gap": 3,
    },
)
