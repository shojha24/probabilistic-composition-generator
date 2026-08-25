"""Jazz guitar voicer (spec 05).

Extended drop voicings use the shared programmatic ``drop2_derived`` shapes.
The reviewed literature does not provide a canonical extended-shape inventory;
the auditable derivation in ``guitar.derive`` is therefore the intentional
construction choice.
"""
from __future__ import annotations

from ..candidates import Candidate
from ..engine import CandidateGenParams
from ..guitar.library import ExtensionDropTracker, make_shape_library_source
from ..guitar.model import shape_distance
from ..guitar.pop_shapes import ALL_SHAPES, SHAPES_BY_KEY
from ..policy import VoicerPolicy

MAX_FRET = 15
MAX_FRET_SPAN = 4
drop_tracker = ExtensionDropTracker()
_source = make_shape_library_source(ALL_SHAPES, SHAPES_BY_KEY, MAX_FRET, MAX_FRET_SPAN)


def candidate_source(p: CandidateGenParams) -> list[Candidate]:
    return _source(p)


def role_penalty(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> float:
    penalty = 0.0
    if prev is not None and prev.shape_id and candidate.shape_id:
        penalty += 0.5 * shape_distance(
            prev.meta.get("root_fret", 0), prev.shape_id,
            frozenset(prev.meta.get("muted", ())),
            candidate.meta.get("root_fret", 0), candidate.shape_id,
            frozenset(candidate.meta.get("muted", ())),
        )
        if prev.meta.get("root_fret") == candidate.meta.get("root_fret"):
            penalty -= 1.0
    if candidate.meta.get("fret_span", 0) > MAX_FRET_SPAN:
        penalty += 0.8
    dropped = candidate.meta.get("extensions_dropped") or ()
    if dropped:
        penalty += 60.0 * len(dropped)
    if "root" in candidate.roles and ctx.get("bass_module_active"):
        penalty += 0.4
    if len(candidate.roles) >= 6:
        penalty += 1.5
    return penalty


def post_filter(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> bool:
    chord = ctx.get("_current_chord")
    dct_role = ctx.get("_current_dct_role")
    if chord is None:
        return False

    roles = set(candidate.roles)
    rootless = "root" not in roles
    if chord.seventh != "N" and rootless and not {"3rd", "7th"} <= roles:
        return False
    if chord.seventh == "N" and "3rd" in roles and rootless and "5th" not in roles:
        return False

    # Shared DCT filtering enforces the sampled top/isolated exposure branch.
    # The derived library does not carry canonical dropped-voice metadata, so
    # do not add a second string-order heuristic here.
    return True


SECTION_PROFILE = {
    "verse": {"drift_free": 4, "max_voices": 5, "root_double_p": 0.03},
    "prechorus": {"drift_free": 4, "max_voices": 5, "root_double_p": 0.03},
    "chorus": {"drift_free": 5, "max_voices": 5, "root_double_p": 0.04},
    "bridge": {"drift_free": 4, "max_voices": 5, "root_double_p": 0.03},
    "outro": {"drift_free": 4, "max_voices": 5, "root_double_p": 0.03},
}

POLICY = VoicerPolicy(
    voicer_id="jazz-guitar", genre="jazz", instrument="guitar",
    window_lo=45, window_hi=79, drift_free=4, drift_tol=9,
    min_voices=3, max_voices=5, p_omit_fifth=0.70, root_double_p=0.03,
    root_omission_p=0.75, max_doublings=1,
    tau=3.5, w_vl=1.2, w_drift=0.9, w_space=0.3, w_div=1.0, w_role=1.3,
    plr_bonus=2.0, unmatched_penalty=2.0, vl_target_mean=7.5, vl_target_sd=4.0,
    dct_mode_weights={"top": 0.60, "isolated": 0.38, "octave": 0.02},
    candidate_source=candidate_source, role_penalty=role_penalty,
    post_filter=post_filter, section_profile=SECTION_PROFILE,
    extra={"max_fret": MAX_FRET, "max_fret_span": MAX_FRET_SPAN,
           "drop_tracker": drop_tracker, "cluster_min_gap": 10},
)
