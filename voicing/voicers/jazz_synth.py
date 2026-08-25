"""Jazz pad-synth voicer (spec 06).

Tone selection follows the jazz shell/root-omission convention while
realization remains unrestricted free placement. Extended shapes use the
shared candidate generator; no canonical jazz-synth fingering is assumed.
"""
from __future__ import annotations

from collections import Counter

from ..candidates import Candidate, free_placement
from ..engine import CandidateGenParams
from ..policy import VoicerPolicy
from ..types import resolve_degrees

WINDOW_LO = 40
WINDOW_HI = 91


def candidate_source(p: CandidateGenParams) -> list[Candidate]:
    role_pcs = [(d.role, (p.chord.root_interval + d.semitone) % 12)
                for d in p.degrees]
    full = resolve_degrees(p.chord)
    doubling_pcs = {
        d.role: (p.chord.root_interval + d.semitone) % 12 for d in full
    }
    return free_placement(
        role_pcs, min(WINDOW_LO, p.window_lo), max(WINDOW_HI, p.window_hi),
        p.anchor_center, p.prev_midi, p.doubling_roles, p.max_doublings,
        p.rng, anchor_shift=p.anchor_shift, doubling_pcs=doubling_pcs,
        cluster_min_gap=p.policy.extra.get("cluster_min_gap", 3),
    )


def _shell_roles(chord) -> set[str]:
    degrees = resolve_degrees(chord)
    third = next((d.role for d in degrees if "3rd" in d.merged_from), "3rd")
    if chord.seventh != "N":
        return {third, "7th"}
    if third in {d.role for d in degrees}:
        return {third}
    return {"root", "5th"}


def _shell_interval(candidate: Candidate, chord) -> int | None:
    shell = _shell_roles(chord)
    values = [p for p, role in zip(candidate.pitches, candidate.roles) if role in shell]
    if len(values) < 2:
        return None
    return max(values) - min(values)


def post_filter(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> bool:
    if not candidate.pitches:
        return False
    chord = ctx.get("_current_chord")
    anchor = ctx.get("_current_anchor_center")
    if chord is None or anchor is None:
        return False
    bottom = min(candidate.pitches)
    if not (anchor - 14 <= bottom <= anchor):
        return False

    roles = set(candidate.roles)
    shell = _shell_roles(chord)
    if chord.seventh != "N" and not shell <= roles:
        return False
    if "root" not in roles and chord.seventh == "N" and not shell <= roles:
        return False

    return True


def role_penalty(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> float:
    chord = ctx.get("_current_chord")
    penalty = 0.0
    if chord is not None:
        shell_gap = _shell_interval(candidate, chord)
        if shell_gap is not None and not 7 <= shell_gap <= 19:
            penalty += 1.0
    ambitus = max(candidate.pitches) - min(candidate.pitches)
    if ambitus < 22:
        penalty += 1.0
    if ambitus > 45:
        penalty += 0.8
    if not any(abs(p - ctx.get("_current_anchor_center", 60)) <= 8
               for p in candidate.pitches):
        penalty += 0.9
    counts = Counter(candidate.roles)
    if sum(n - 1 for n in counts.values() if n > 1) > 3:
        penalty += 1.5
    if "5th" in counts and "9th" in counts and "13th" in counts:
        penalty += 0.7
    return penalty


SECTION_PROFILE = {
    "verse": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.15},
    "prechorus": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.15},
    "chorus": {"drift_free": 6, "max_voices": 7, "root_double_p": 0.20},
    "bridge": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.15},
    "outro": {"drift_free": 5, "max_voices": 7, "root_double_p": 0.15},
}

POLICY = VoicerPolicy(
    voicer_id="jazz-synth", genre="jazz", instrument="synth",
    window_lo=WINDOW_LO, window_hi=WINDOW_HI, drift_free=5, drift_tol=11,
    min_voices=3, max_voices=7, p_omit_fifth=0.45, root_double_p=0.15,
    root_omission_p=0.55, max_doublings=3,
    tau=3.0, w_vl=1.1, w_drift=0.95, w_space=0.8, w_div=1.1, w_role=0.8,
    plr_bonus=2.0, unmatched_penalty=1.5, vl_target_mean=7.0, vl_target_sd=4.0,
    dct_mode_weights={"top": 0.40, "isolated": 0.30, "octave": 0.30},
    candidate_source=candidate_source, role_penalty=role_penalty,
    post_filter=post_filter, section_profile=SECTION_PROFILE,
    extra={"doubling_targets": ("root", "5th", "7th"), "cluster_min_gap": 3},
)
