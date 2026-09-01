"""
voicing/voicers/pop_synth.py -- spec 03, the pop/rock pad voicer.

No hand/fret constraint at all (parent §6): the widest register, the
heaviest drift weighting, and the only voicer whose `section_profile` is
mandatory rather than optional (spec 03 §3, parent §5's pop-synth row).
"""
from __future__ import annotations

from collections import Counter

from ..policy import VoicerPolicy
from ..candidates import (
    Candidate,
    free_placement_templates,
    needs_rare_templates,
    template_profiles_for,
)
from ..engine import CandidateGenParams
from ..types import resolve_degrees
from ..spacing import spacing_hard_ok

WINDOW_LO = 36
WINDOW_HI = 96

# spec 03 §3.1
SECTION_PROFILE = {
    "verse":     {"root_double_p": 0.25, "root_omission_p": 0.35,
                  "max_voices": 5, "spread": "open", "anchor_shift": 0},
    "prechorus": {"root_double_p": 0.45, "root_omission_p": 0.20,
                  "max_voices": 6, "spread": "open", "anchor_shift": 2},
    "chorus":    {"root_double_p": 0.80, "root_omission_p": 0.05,
                  "max_voices": 8, "spread": "wide", "anchor_shift": 5},
    "bridge":    {"root_double_p": 0.35, "root_omission_p": 0.30,
                  "max_voices": 6, "spread": "sparse", "anchor_shift": -4},
    "outro":     {"root_double_p": 0.55, "root_omission_p": 0.15,
                  "max_voices": 7, "spread": "wide", "anchor_shift": 0},
}

SIGNATURE_BANDS = {"low": (36, 49), "mid": (50, 64), "high": (65, 96)}

# spec 03 §3.1 table: target adjacent-interval profiles per `spread` class.
# These are stated as approximate target profiles ("target adjacent-interval
# profile"), not exact acceptance/rejection boundaries, so both the
# generator-side filter (`_matches_spread`, §4 item 3) and the softer
# role_penalty deviation count (§7) treat them as a loose template rather
# than a hard shape -- consistent with §4 item 3's framing of `spread` as a
# candidate *generator* constraint whose job is mainly to keep the
# candidate pool from exploding, not a strict grammar.
_SPREAD_TEMPLATES = {
    "sparse": {"gap_lo": 7, "gap_hi": 14, "small_gap_thresh": 5, "max_small_gaps": 1},
    "open":   {"gap_lo": 5, "gap_hi": 12, "small_gap_thresh": 4, "max_small_gaps": 1},
    "wide":   {"min_doubled_octaves": 2, "min_ambitus": 24},
}


def _spread_for_section(section: str) -> str:
    return SECTION_PROFILE.get(section, {}).get("spread", "open")


# spec 03 §8's additional gate target: emitted `spread` classes must be
# distributed no more skewed than this across the dataset.
_SPREAD_TARGET = {"wide": 0.60, "open": 0.25, "sparse": 0.15}
# `_matches_spread` is intentionally a loose realized-shape classifier, so a
# small reserve is needed for requested "wide" shapes that land in the
# fallback "open" bucket. Keep the published target above for reporting, but
# steer the corrective sampler against this conservative control target so
# the realized mix can still meet the 60/25/15 gate.
_SPREAD_CONTROL_TARGET = {"wide": 0.62, "open": 0.25, "sparse": 0.13}


def _spread_for_chord(section: str, ctx: dict, t) -> str:
    """§8: pick this chord's active `spread` class. Mostly follows the
    section's nominal request (preserving each section's intended musical
    character -- chorus should still generally read as "wide", bridge as
    "sparse"), but self-corrects against a running tally of *realized*
    spread classes (populated by `_on_commit`, not by this function) once
    a class has drifted more than 5 points over its 60/25/15 target share,
    switching to whichever class is currently most under-represented.

    Deliberately reads a tally of *realized*, not merely requested,
    classes: `_matches_spread`'s own criteria mean not every chord that
    *requests* "wide" actually *realizes* as wide (a narrow-window section
    may simply have no candidate that spans the needed ambitus), so
    correcting off the request side alone would systematically overshoot
    -- pushing many more chords toward "wide" than ever actually land
    there, while starving whichever class silently absorbs the shortfall
    as `_matches_spread`'s fallback bucket.

    Without this, `spread` is purely a *section*-level policy knob (see
    SECTION_PROFILE) with no feedback from how often each section actually
    occurs -- a corpus/run with proportionally more chorus/outro ("wide")
    chords than bridge ("sparse") ones would then silently violate the
    dataset-wide 60/25/15 gate regardless of any individual chord's
    voicing, since nothing upstream of this ever looks at the *aggregate*
    mix. Cached per chord index (`t`) in `ctx` so the several
    candidate_gen/role_penalty calls made for the *same* chord across
    ladder-level retries don't each re-roll independently -- only advancing
    to a genuinely new chord may change the active class."""
    requested = _spread_for_section(section)
    cache = ctx.setdefault("_spread_cache", {})
    if t is not None and t in cache:
        return cache[t]
    tally = ctx.setdefault("_spread_tally", {"wide": 0, "open": 0, "sparse": 0})
    total = sum(tally.values())
    chosen = requested
    if total >= 20:
        # Deficit measured *relative to each class's own target*, not as a
        # raw percentage-point gap -- a raw gap always favors "wide" (60%
        # target) over "sparse" (15% target) whenever both are
        # under-represented, since the same proportional shortfall
        # produces a much bigger absolute number for the class with the
        # larger target. That would make every correction pile into
        # "wide" and starve "sparse" further, exactly backwards from what
        # is needed.
        #
        # Always defers to whichever class is currently furthest under
        # its own target (no additional hysteresis margin on top of the
        # requested class): because `_matches_spread` is a loose,
        # imperfect classifier, a chosen class doesn't always convert to
        # that same realized class, so a soft/lazy correction converges
        # too slowly to actually satisfy the 60/25/15 gate across a
        # finite dataset -- only overriding once requested's own target is
        # already exceeded left "open" free to run persistently high
        # every time an under-realizing "wide"/"sparse" request silently
        # fell back to it.
        rel_deficit = {
            k: (_SPREAD_CONTROL_TARGET[k] - tally[k] / total) / _SPREAD_CONTROL_TARGET[k]
            for k in tally
        }
        best = max(rel_deficit, key=rel_deficit.get)
        if rel_deficit[best] > rel_deficit[requested] + 1e-9:
            chosen = best
    if t is not None:
        cache[t] = chosen
    return chosen


def _realized_spread_class(pitches: list[int]) -> str:
    """Classify a *realized* voicing's spread, in the same wide > sparse >
    open priority order used by the corpus-level distribution gate's own
    test (spec 03 §9 item 5), so the self-correcting tally in
    `_spread_for_chord` is measuring the same thing the gate checks."""
    if _matches_spread(pitches, "wide"):
        return "wide"
    if _matches_spread(pitches, "sparse"):
        return "sparse"
    return "open"


def _on_commit(candidate, ctx: dict) -> None:
    """§8 spread-distribution self-correction: record this chord's
    *realized* spread class into the running tally that `_spread_for_chord`
    reads on subsequent chords. Registered via `policy.extra["on_commit"]`
    and invoked once per chord, after final candidate selection, by
    engine.py."""
    if not candidate.pitches:
        return
    tally = ctx.setdefault("_spread_tally", {"wide": 0, "open": 0, "sparse": 0})
    tally[_realized_spread_class(candidate.pitches)] += 1


def _gaps(pitches: list[int]) -> list[int]:
    s = sorted(pitches)
    return [s[i + 1] - s[i] for i in range(len(s) - 1)]


def _octave_doubled_count(pitches: list[int]) -> int:
    s = sorted(pitches)
    return sum(1 for i in range(len(s)) for j in range(i + 1, len(s)) if s[j] - s[i] == 12)


def _matches_spread(pitches: list[int], spread: str) -> bool:
    """Loose acceptance test for whether a candidate's realized shape is
    plausibly a member of the active `spread` class (spec 03 §3.1's
    table). Used to prefilter `free_placement`'s pool before role_penalty
    ever sees it, per §4 item 3.

    `wide` requires BOTH doubled-octave count and ambitus to clear their
    thresholds (not either alone): an OR let almost any multi-voice pad
    chord in *any* section qualify as "wide" purely because stacking 5+
    roles across a wide window commonly spans >= 2 octaves regardless of
    the active section's actual spread target, which silently defeated
    spec 03 §8's realized-spread-distribution gate (a candidate's
    genuinely-`wide` character should mean deliberately doubled *and*
    spread, not merely "happens to be spread out because it has many
    voices")."""
    if len(pitches) < 2:
        return True
    tmpl = _SPREAD_TEMPLATES.get(spread, _SPREAD_TEMPLATES["open"])
    if spread == "wide":
        ambitus = max(pitches) - min(pitches)
        return (_octave_doubled_count(pitches) >= tmpl["min_doubled_octaves"] and
                ambitus >= tmpl["min_ambitus"])
    gaps = _gaps(pitches)
    small = sum(1 for g in gaps if g < tmpl["small_gap_thresh"])
    out_of_band = sum(1 for g in gaps if not (tmpl["gap_lo"] <= g <= tmpl["gap_hi"]))
    return small <= tmpl["max_small_gaps"] and out_of_band <= 1


def candidate_source(p: CandidateGenParams) -> list:
    role_pcs = [(d.role, (p.root_pc + d.semitone) % 12) for d in p.degrees]
    # See pop_piano.candidate_source's identical comment: doubling-eligible
    # roles thinned out of the core degree set (a probabilistically
    # omitted 5th) still need a resolvable pc or doubling them is a
    # silent no-op.
    full_degrees = resolve_degrees(p.chord)
    doubling_pcs = {d.role: (p.root_pc + d.semitone) % 12 for d in full_degrees}
    window_lo = min(WINDOW_LO, p.window_lo)
    window_hi = max(WINDOW_HI, p.window_hi)
    profiles = template_profiles_for(p.chord, p.policy)
    raw = free_placement_templates(
        role_pcs, window_lo, window_hi, p.anchor_center, p.prev_midi,
        p.doubling_roles, p.max_doublings, p.rng,
        anchor_shift=p.anchor_shift, doubling_pcs=doubling_pcs,
        cluster_min_gap=p.policy.extra.get("cluster_min_gap", 13),
        forced_dct_role=getattr(p, "forced_dct_role", None),
        templates=profiles,
        preferred_template=(
            p.ctx.get("_template_target")
            if needs_rare_templates(p.chord) else None
        ),
    )
    spread = _spread_for_chord(p.section, p.ctx, p.ctx.get("_current_t"))
    min_gap = p.policy.extra.get("cluster_min_gap", 13)
    matches = [c for c in raw if _matches_spread(c.pitches, spread)]
    # Always keep candidates that already satisfy the hard cluster/LIL
    # gates (spec 07 §7.3) even when they don't match `spread` -- for a
    # chord with a semitone-adjacent role pair (e.g. maj7's root/7th, or
    # any chord with a b9-type clash), the *only* candidates that can ever
    # survive engine.py's hard cluster filter are usually wide-interval
    # ones that a tight `spread` template (especially "sparse"/"open",
    # which want small adjacent gaps) would otherwise exclude outright.
    # Silently dropping them here wouldn't just make the voicing
    # "off-template" -- it would make the chord unvoiceable, since
    # `free_placement`'s own clash-aware fallback only fires when its pool
    # has *no* clash-clean candidate at all, and by definition it already
    # found one here.
    matched_ids = {id(c) for c in matches}
    safe = [c for c in raw
            if id(c) not in matched_ids and spacing_hard_ok(c.pitches, min_gap)]
    # Always keep candidates that already satisfy the hard cluster/LIL
    # gates (spec 07 §7.3) even when they don't match `spread` -- for a
    # chord with a semitone-adjacent role pair (e.g. maj7's root/7th, or
    # any chord with a b9-type clash), the *only* candidates that can ever
    # survive engine.py's hard cluster filter are usually wide-interval
    # ones that a tight `spread` template (especially "sparse"/"open",
    # which want small adjacent gaps) would otherwise exclude outright.
    # Silently dropping them here wouldn't just make the voicing
    # "off-template" -- it would make the chord unvoiceable, since
    # `free_placement`'s own clash-aware fallback only fires when its pool
    # has *no* clash-clean candidate at all, and by definition it already
    # found one here.
    return (matches + safe) if (matches or safe) else raw


def post_filter(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> bool:
    if not candidate.pitches:
        return False
    anchor_center = ctx.get("_current_anchor_center")
    if anchor_center is None:
        return True  # defensive; engine always sets this before calling post_filter
    bottom = min(candidate.pitches)

    # spec 03 §2 "octave-band anchoring": the pad's lowest sounding voice
    # must stay within [anchor_center - 16, anchor_center - 2] -- the
    # centroid-only drift test doesn't catch a bottom falling out while
    # the top simultaneously spreads upward.
    if not (anchor_center - 16 <= bottom <= anchor_center - 2):
        return False

    # spec 03 §6 "sub-bass collision": below anchor_center - 16, only
    # root/5th pitch classes may sound when the bass module is active.
    # Structurally this is almost always implied by the bottom-anchoring
    # floor just above (nothing can be below anchor_center - 16 once the
    # bottom voice itself is bound at-or-above that floor) -- kept as an
    # explicit, independent check anyway as defense in depth, since it's
    # cheap and guards against the two rules ever drifting out of sync.
    if ctx.get("bass_module_active"):
        floor = anchor_center - 16
        for pitch, role in zip(candidate.pitches, candidate.roles):
            if pitch < floor and role not in ("root", "5th"):
                return False

    return True


def role_penalty(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> float:
    pitches = candidate.pitches
    if not pitches:
        return 0.0
    penalty = 0.0
    section = ctx.get("section")
    spread = _spread_for_chord(section, ctx, ctx.get("_current_t"))
    tmpl = _SPREAD_TEMPLATES.get(spread, _SPREAD_TEMPLATES["open"])

    if spread == "wide":
        ambitus = max(pitches) - min(pitches)
        doubled = _octave_doubled_count(pitches)
        # Strengthened from 0.3 to 9.0 (spec 03 §8): the previous weight
        # left `role_penalty`'s spread-template term too small relative
        # to VL/drift/diversity cost to reliably steer softmax selection
        # toward whatever `spread` `_spread_for_chord`'s self-correction
        # actually asked for, so the corpus-wide 60/25/15 gate could not
        # converge even when the requested class was correctly chosen.
        if doubled < tmpl["min_doubled_octaves"]:
            penalty += 9.0 * (tmpl["min_doubled_octaves"] - doubled)
        if ambitus < tmpl["min_ambitus"]:
            penalty += 9.0
    else:
        for g in _gaps(pitches):
            if not (tmpl["gap_lo"] <= g <= tmpl["gap_hi"]):
                penalty += 9.0

    ambitus = max(pitches) - min(pitches)
    if ambitus < 19:
        penalty += 1.0
    if ambitus > 48:
        penalty += 0.8

    role_counts = Counter(candidate.roles)
    total_doublings = sum(n - 1 for n in role_counts.values() if n > 1)
    if total_doublings > 4:
        penalty += 1.5

    dct_role = ctx.get("_current_dct_role")
    for role, n in role_counts.items():
        if n > 1 and role not in ("root", "5th", dct_role):
            penalty += 1.2

    anchor_center = ctx.get("_current_anchor_center", 60)
    if not any(abs(p - anchor_center) <= 7 for p in pitches):
        penalty += 0.9

    return penalty


POLICY = VoicerPolicy(
    voicer_id="pop-synth", genre="pop_rock", instrument="synth",
    window_lo=WINDOW_LO, window_hi=WINDOW_HI, drift_free=6, drift_tol=12,
    min_voices=3, max_voices=8, p_omit_fifth=0.10, root_double_p=0.50,
    root_omission_p=0.20, max_doublings=4,
    # spec 03 §5: "tau fitted; expect ~5.0-7.0" -- no calibration corpus is
    # given to fit this against, so this picks the range's midpoint as a
    # concrete starting value pending real calibration data. Flagging this
    # choice per the user's instruction to surface judgment calls rather
    # than silently pick a number.
    tau=6.0, w_vl=0.5, w_drift=1.0, w_space=0.9, w_div=1.3, w_role=0.7,
    plr_bonus=0.4, unmatched_penalty=1.2, vl_target_mean=11.0, vl_target_sd=6.0,
    dct_mode_weights={"top": 0.35, "isolated": 0.35, "octave": 0.30},
    candidate_source=candidate_source, role_penalty=role_penalty, post_filter=post_filter,
    section_profile=SECTION_PROFILE,
    extra={"doubling_targets": ("root", "5th"), "cluster_cap": 4,
           "spacing_floor": 3, "spacing_floor_low": 6,
           "spacing_exception_tensions": 2, "spacing_exception_voices": 6,
           "spacing_exception_max_gaps": 1,
           "voice_excess_penalty": 0.6, "voice_excess_growth": 1.8,
           "dct_repair_ok": True, "max_repair_inputs": 64,
           "max_secondary_assignments": 32, "max_repair_variants": 256,
           "max_retained_hard_clean": 256, "on_commit": _on_commit,
           "template_profiles": ("balanced", "open", "wide", "tension_top"),
           "rare_template_profiles": ("rare_feature", "root_spread"),
           "template_mismatch_penalty": 0.7,
           "rare_template_mismatch_penalty": 1.0},
)
