"""
voicing/candidates.py -- Stage 2 candidate enumeration, spec 07 §4.2.

Two candidate sources exist per the parent spec: `free_placement` (piano,
synth) and `shape_library` (guitar, see voicing/guitar/shapes.py). This
module implements `free_placement` as a bounded beam search over per-role
octave placements (a practical realization of "branch-and-bound on partial
cost" from §4.2) plus a doubling-variant expansion step, and a generic
hand-split post-processor used by the piano voicers.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache
from math import comb
from typing import Optional

from .types import Degree
from .spacing import low_interval_limit_ok


@dataclass
class Candidate:
    pitches: list       # sorted ascending, deduped MIDI ints
    roles: list         # parallel to pitches (not necessarily unique)
    shape_id: Optional[str] = None
    hands: Optional[dict] = None
    meta: dict = field(default_factory=dict)

    def dedup_sorted(self) -> "Candidate":
        pairs = sorted(zip(self.pitches, self.roles), key=lambda pr: pr[0])
        seen = set()
        out_p, out_r = [], []
        for p, r in pairs:
            if p in seen:
                continue
            seen.add(p)
            out_p.append(p)
            out_r.append(r)
        return Candidate(out_p, out_r, self.shape_id, self.hands, dict(self.meta))


def dct_expose_repair(candidate: "Candidate", dct_role: str, secondary_roles: list,
                       sampled_branch: Optional[str], window_lo: int, window_hi: int,
                       anchor_center: int, max_variants: int = 256,
                       max_secondary_assignments: int = 32,
                       max_voices: Optional[int] = None,
                       max_doublings: Optional[int] = None):
    """Last-resort, lazily-triggered repair for spec 07 §5.2/§5.3 DCT
    exposure: `beam_octave_assignment` minimizes anchor-distance per role
    with zero awareness of *which* role is the DCT, so for role
    combinations whose natural close-voicing stacking order happens to
    sandwich the DCT tone between two neighbours a step away on each side
    (e.g. an 11th sitting between a 9th and a 5th -- none of them a
    semitone-clash pair, so `_clash_bipartition` never separates them),
    exposure can fail to be satisfied by *any* naturally-generated
    candidate at *any* ladder level, even though widening the window or
    voice count doesn't help (the problem is topology, not headroom).

    Rather than regenerating the whole candidate pool (expensive and,
    per the RNG-order lesson learned earlier this session, liable to
    silently perturb unrelated downstream chords), this repairs a single
    already-otherwise-valid candidate by re-placing only the DCT role's
    octave (and each secondary differentiator's octave) in isolation,
    leaving every other role's placement untouched. Only called when
    engine.py has confirmed no naturally-generated candidate at the
    current level already satisfies exposure -- see its call site.

    Generator: yields successive candidate variants in
    nearest-to-anchor-first preference order (rather than returning only
    the single best-by-isolation option), because the single best
    isolation-satisfying placement for the DCT/secondary role(s) can
    still create a *new* LIL/cluster violation against an untouched
    role -- the caller must re-validate every hard gate on each yielded
    variant and only needs to try the next one if that happens.

    Secondary differentiators are enumerated as bounded combinations rather
    than greedily taking the first placement for each role. The octave
    branch adds exactly one DCT copy at an available +/-12 position; it never
    treats the DCT as an ordinary doubling target.
    """
    base_pitches = list(candidate.pitches)
    roles = list(candidate.roles)

    def _isolated_against(p: int, others: list) -> bool:
        below = max((x for x in others if x < p), default=None)
        above = min((x for x in others if x > p), default=None)
        ok_below = below is None or (p - below) >= 3
        ok_above = above is None or (above - p) >= 2
        return ok_below and ok_above

    def _options_for(idx: int, pitches: list) -> list:
        pc = pitches[idx] % 12
        others = [p for j, p in enumerate(pitches) if j != idx]
        opts = [p for p in range(window_lo, window_hi + 1) if p % 12 == pc]
        opts.sort(key=lambda p: (abs(p - anchor_center), p))
        return [
            p for p in opts
            if p not in others and _isolated_against(p, others)
        ]

    dct_idx = next((i for i, r in enumerate(roles) if r == dct_role), None)
    if dct_idx is None:
        return  # DCT dropped -- never allowed, caller must reject anyway

    dct_placements = []
    if sampled_branch == "top":
        # Trivially satisfies predicate_top (and usually predicate_isolated
        # too, given the resulting gap) by placing the DCT's pc strictly
        # above every other currently-placed pitch.
        pc = base_pitches[dct_idx] % 12
        current_max = max(p for j, p in enumerate(base_pitches) if j != dct_idx)
        dct_options = [p for p in range(window_lo, window_hi + 1)
                       if p % 12 == pc and p > current_max]
        dct_options.sort(key=lambda p: (abs(p - anchor_center), p))
        dct_placements = [(p, None) for p in dct_options]
    elif sampled_branch == "octave":
        # Repositioning the existing DCT is not an octave exposure. Add one
        # explicit copy exactly an octave away and let the shared validator
        # decide whether the lower copy is exposed and all other hard gates
        # still hold.
        dct_pitch = base_pitches[dct_idx]
        forced_options = [
            p for p in range(window_lo, window_hi + 1)
            if p % 12 == dct_pitch % 12
            and abs(p - dct_pitch) == 12
            and p not in base_pitches
        ]
        forced_options.sort(key=lambda p: (abs(p - anchor_center), p))
        base_doublings = len(base_pitches) - len(set(roles))
        if (max_voices is not None and len(base_pitches) >= max_voices) or \
                (max_doublings is not None and (
                    max_doublings <= 0 or base_doublings >= max_doublings
                )):
            forced_options = []
        dct_placements = [(dct_pitch, p) for p in forced_options]
    else:
        # The isolated branch enumerates every legal instance in the current
        # window rather than only the first nearest-to-anchor option.
        dct_options = _options_for(dct_idx, base_pitches)
        dct_options.sort(key=lambda p: (abs(p - anchor_center), p))
        dct_placements = [(p, None) for p in dct_options]
    if not dct_placements:
        return

    sec_idxs = [next((i for i, r in enumerate(roles) if r == sr), None)
                for sr in secondary_roles]
    if any(i is None for i in sec_idxs):
        return

    generated = 0
    secondary_limit = max(1, int(max_secondary_assignments))
    for dp, forced_copy in dct_placements:
        trial = list(base_pitches)
        trial[dct_idx] = dp
        trial_roles = list(roles)
        if forced_copy is not None:
            trial.append(forced_copy)
            trial_roles.append(dct_role)
        if len(trial) != len(set(trial)):
            continue

        assignments = []

        def enumerate_secondary(position: int, current: list[int]) -> None:
            if len(assignments) >= secondary_limit:
                return
            if position == len(sec_idxs):
                assignments.append(list(current))
                return
            sidx = sec_idxs[position]
            for option in _options_for(sidx, current):
                next_pitches = list(current)
                next_pitches[sidx] = option
                if len(next_pitches) != len(set(next_pitches)):
                    continue
                enumerate_secondary(position + 1, next_pitches)
                if len(assignments) >= secondary_limit:
                    return

        enumerate_secondary(0, trial)
        if not sec_idxs:
            assignments = [trial]
        for assignment in assignments:
            if generated >= max_variants:
                return
            if len(assignment) != len(set(assignment)):
                continue
            meta = dict(candidate.meta)
            meta.update({
                "generation_phase": "dct_repair",
                "dct_repair": True,
                "forced_dct_copy": forced_copy is not None,
            })
            repaired = Candidate(
                assignment, trial_roles, candidate.shape_id, candidate.hands, meta
            ).dedup_sorted()
            # A repair may not hide a required role behind a pitch collision.
            if len(repaired.pitches) != len(assignment):
                continue
            yield repaired
            generated += 1




def post_filter_repairs(candidate: "Candidate", window_lo: int, window_hi: int,
                         anchor_center: int, min_gap: int,
                         post_filter_fn, prev, ctx: dict, policy,
                         max_variants: int = 256):
    """Generic last-resort repair for a voicer-specific hard `post_filter`
    (e.g. spec 03's bottom-voice octave-band anchoring): among several
    otherwise-equal-cost register placements for the *lowest* currently
    sounding role, the anchor-distance-only beam search has no way to
    prefer the one that also happens to satisfy an arbitrary voicer
    predicate it knows nothing about -- so it can consistently settle on
    a tied-cost combination (e.g. clash-separation pushing the root down
    to the window floor) that fails `post_filter` on every single
    candidate, even though an equal-cost alternative registration would
    have passed. Rather than teach the generic beam search about every
    voicer's post_filter (which would break the module boundary), this
    tries repositioning just the lowest-sounding role across its other
    in-window octave options -- re-checking clash-safety against every
    other currently-placed pitch each time -- and asks `post_filter_fn`
    directly whether the result is acceptable. Bounded and lazy: only
    called by engine.py when every otherwise-valid candidate at a level
    failed post_filter specifically."""
    pitches = list(candidate.pitches)
    roles = list(candidate.roles)
    if not pitches:
        return
    bottom_idx = min(range(len(pitches)), key=lambda i: pitches[i])
    pc = pitches[bottom_idx] % 12
    others = [p for j, p in enumerate(pitches) if j != bottom_idx]
    options = [p for p in range(window_lo, window_hi + 1) if p % 12 == pc]
    options.sort(key=lambda p: (abs(p - anchor_center), p))
    generated = 0
    for p in options:
        if p == pitches[bottom_idx]:
            continue
        if p in others:
            continue
        # avoid reintroducing a semitone-cluster clash against any other
        # already-placed pitch at the new position.
        if any(((p - q) % 12 in (1, 11)) and abs(p - q) < min_gap for q in others):
            continue
        trial_pitches = list(pitches)
        trial_pitches[bottom_idx] = p
        meta = dict(candidate.meta)
        meta.update({
            "generation_phase": "dct_repair",
            "dct_repair": True,
            "repair_kind": "post_filter",
            "forced_dct_copy": False,
        })
        trial = Candidate(
            trial_pitches, roles, candidate.shape_id, candidate.hands, meta
        ).dedup_sorted()
        if len(trial.pitches) != len(trial_pitches):
            continue
        if post_filter_fn(trial, prev, ctx, policy):
            yield trial
            generated += 1
            if generated >= max_variants:
                return


def post_filter_repair(candidate: "Candidate", window_lo: int, window_hi: int,
                        anchor_center: int, min_gap: int,
                        post_filter_fn, prev, ctx: dict, policy,
                        max_variants: int = 256) -> Optional["Candidate"]:
    """Return the first bounded post-filter repair.

    ``post_filter_repairs`` is exposed separately so the engine can inspect
    the complete bounded repair pool while preserving the original helper's
    single-result API for existing callers.
    """
    return next(post_filter_repairs(
        candidate, window_lo, window_hi, anchor_center, min_gap,
        post_filter_fn, prev, ctx, policy, max_variants=max_variants,
    ), None)


@lru_cache(maxsize=4096)
def role_octave_options(pc: int, window_lo: int, window_hi: int) -> tuple:
    """Cached: this is pure/static in its arguments and gets called with the
    same (pc, window) pairs an enormous number of times during doubling
    exploration -- profiling showed this rebuilding the same `range()`
    filter ~3.8M times in a 3-song run before caching."""
    return tuple(p for p in range(window_lo, window_hi + 1) if p % 12 == pc % 12)


# Fractions of the active register window used as soft targets by the
# octave-assignment beam. These are register mappings, not post-selection
# pitch moves: every resulting candidate still goes through the engine's
# spacing, DCT, drift, and instrument-specific gates.
TEMPLATE_ROLE_TARGETS = {
    # Preserve the original mapping as the stable default for callers that
    # do not opt into the expanded template set.
    "balanced": {
        "root": 0.18, "5th": 0.30, "3rd": 0.52, "7th": 0.56,
        "9th": 0.78, "11th": 0.84, "13th": 0.90,
    },
    # A compact core with upper extensions allowed to sit closer together.
    "closed": {
        "root": 0.20, "5th": 0.31, "3rd": 0.43, "7th": 0.50,
        "9th": 0.60, "11th": 0.67, "13th": 0.74,
    },
    # A root/fifth foundation with the guide tones and colors progressively
    # higher, suitable for open pads and two-hand piano textures.
    "open": {
        "root": 0.12, "5th": 0.28, "3rd": 0.46, "7th": 0.61,
        "9th": 0.75, "11th": 0.84, "13th": 0.94,
    },
    # Keep the shell readable while separating it from the color tones.
    "shell_spread": {
        "root": 0.10, "5th": 0.25, "3rd": 0.48, "7th": 0.68,
        "9th": 0.78, "11th": 0.88, "13th": 0.96,
    },
    # Make the highest active extension the clearest register landmark.
    "tension_top": {
        "root": 0.10, "5th": 0.24, "3rd": 0.42, "7th": 0.55,
        "9th": 0.79, "11th": 0.90, "13th": 0.98,
    },
    # A deliberately broad pad mapping; it remains bounded by the active
    # window and is useful for rare, fully spelled extension stacks.
    "wide": {
        "root": 0.08, "5th": 0.24, "3rd": 0.46, "7th": 0.64,
        "9th": 0.82, "11th": 0.92, "13th": 0.98,
    },
    # Rare-feature layout: preserve every role while giving unusual triads
    # and dense extension stacks a clearly separated upper identity.
    "rare_feature": {
        "root": 0.09, "5th": 0.34, "3rd": 0.64, "7th": 0.58,
        "9th": 0.75, "11th": 0.87, "13th": 0.95,
    },
    # Power/root-only layout: keep the low foundation and move a fifth or
    # additional root copy away from it when the chord has few roles.
    "root_spread": {
        "root": 0.10, "5th": 0.78, "3rd": 0.46, "7th": 0.61,
        "9th": 0.80, "11th": 0.90, "13th": 0.98,
    },
    # Piano-specific jazz mappings keep the quality-bearing shell inside
    # the lower hand while reserving the upper register for color tones.
    "jazz_shell": {
        "root": 0.16, "5th": 0.28, "3rd": 0.38, "7th": 0.50,
        "9th": 0.74, "11th": 0.84, "13th": 0.94,
    },
    "jazz_tension_top": {
        "root": 0.14, "5th": 0.25, "3rd": 0.36, "7th": 0.49,
        "9th": 0.78, "11th": 0.89, "13th": 0.98,
    },
    "jazz_rare": {
        "root": 0.12, "5th": 0.29, "3rd": 0.41, "7th": 0.59,
        "9th": 0.78, "11th": 0.89, "13th": 0.97,
    },
    "jazz_open": {
        "root": 0.12, "5th": 0.31, "3rd": 0.41, "7th": 0.55,
        "9th": 0.76, "11th": 0.87, "13th": 0.96,
    },
}

RARE_TRIADS = frozenset(("diminished", "augmented", "sus2", "5", "1"))


def needs_rare_templates(chord) -> bool:
    return chord.triad in RARE_TRIADS or sum(
        getattr(chord, slot) != "N"
        for slot in ("seventh", "ninth", "eleventh", "thirteenth")
    ) >= 2


def template_profiles_for(chord, policy) -> tuple[str, ...]:
    """Return the configured placement profiles for one chord.

    Rare triads and multi-extension labels receive additional candidates,
    rather than replacing the normal pool. This keeps common voicings
    available for voice leading while giving underrepresented features more
    physically valid ways to appear.
    """
    profiles = list(policy.extra.get("template_profiles", ("balanced",)))
    if needs_rare_templates(chord):
        profiles.extend(policy.extra.get("rare_template_profiles", ()))
    profiles = tuple(dict.fromkeys(
        profile for profile in profiles if profile in TEMPLATE_ROLE_TARGETS
    ))
    return profiles or ("balanced",)


def preferred_template_for(chord, policy, ctx: dict) -> str:
    """Choose a deterministic rotating target for the current chord.

    Candidate pools still contain every configured profile, so a target can
    yield to voice leading or a hard physical constraint. Ordinary chords
    rotate across all profiles; rare triads and dense extension labels rotate
    over their rare-profile subset so those features receive deliberate
    coverage instead of only being passively available. The song seed avoids
    identical template sequences in every song.
    """
    profiles = template_profiles_for(chord, policy)
    rare_profiles = tuple(
        profile for profile in policy.extra.get("rare_template_profiles", ())
        if profile in profiles
    )
    # Rare labels should not merely have extra alternatives; they should
    # actively exercise those alternatives. Keep the ordinary profiles in
    # the candidate pool for local voice-leading fallbacks, but rotate the
    # target over rare profiles whenever the chord qualifies.
    target_profiles = (
        rare_profiles if needs_rare_templates(chord) and rare_profiles
        else profiles
    )
    t = ctx.get("_current_t")
    key = t if t is not None else len(ctx.setdefault("_template_targets", {}))
    cache = ctx.setdefault("_template_targets", {})
    if key not in cache:
        seed = int(ctx.get("seed", 0))
        cache[key] = target_profiles[(seed + int(key)) % len(target_profiles)]
    target = cache[key]
    ctx["_template_target"] = target
    return target


def template_selection_penalty(candidate: Candidate, chord, ctx: dict,
                               policy) -> float:
    """Softly favor the profile targeted for this chord.

    The penalty is deliberately soft: an unavailable or physically awkward
    target must never make a legal chord unvoiceable.
    """
    target = ctx.get("_template_target")
    mismatch_penalty = float(policy.extra.get("template_mismatch_penalty", 0.0))
    if not target or not mismatch_penalty:
        return 0.0
    if candidate.meta.get("voicing_template") == target:
        return 0.0
    if needs_rare_templates(chord):
        mismatch_penalty += float(
            policy.extra.get("rare_template_mismatch_penalty", 0.0)
        )
    return mismatch_penalty


def beam_octave_assignment(role_pcs: list[tuple], window_lo: int, window_hi: int,
                            anchor_center: int, prev_midi: Optional[list],
                            beam_width: int = 120,
                            rng: Optional[random.Random] = None,
                            anchor_shift: int = 0,
                            template: str = "balanced",
                            role_windows: Optional[dict] = None) -> list[list[tuple]]:
    """Assign one absolute octave to each (role, pc) pair, via beam search
    minimizing running distance-to-anchor (and, softly, distance to a
    similar prior pitch, to seed reasonable VL-distance candidates before
    the real cost function runs). Returns a list of complete assignments,
    each a list of (role, pitch) tuples, deduplicated by resulting pitch
    multiset.

    When `anchor_shift` is nonzero (an active section profile is
    genuinely moving the anchor, e.g. a chorus lift), placements on the
    "wrong" side of the anchor are penalized more heavily than ones on the
    shift's side. Plain symmetric distance-to-anchor alone still frequently
    resolves ties toward the *unshifted* register (e.g. the previous
    section's placements, or simply the register with more available
    octave slots), which quietly cancels out the shift. Sections with no
    shift keep pure symmetric behaviour."""
    role_targets = TEMPLATE_ROLE_TARGETS.get(
        template, TEMPLATE_ROLE_TARGETS["balanced"])
    # The original balanced mapping remains a light tie-breaker. Explicit
    # profiles need a stronger beam preference so their alternatives survive
    # the bounded search instead of collapsing into the same few placements;
    # hard spacing and instrument gates still decide whether they are usable.
    template_weight = 0.12 if template == "balanced" else 0.38
    role_pc_map = dict(role_pcs)
    beams = [([], 0.0)]  # (assignment_so_far, partial_cost)
    prev_sorted = sorted(prev_midi) if prev_midi else []

    for role, pc in role_pcs:
        options = role_octave_options(pc, window_lo, window_hi)
        if role_windows and role in role_windows:
            role_lo, role_hi = role_windows[role]
            options = tuple(p for p in options if role_lo <= p <= role_hi)
        if not options:
            # no legal placement inside the window for this pc; caller must
            # relax the window (spec 07 §9 ladder step 6)
            return []
        new_beams = []
        for assignment, cost in beams:
            placed = [p for _, p in assignment]
            for p in options:
                # §7.3 LIL is a hard, never-relaxed constraint -- checking
                # it only after a full candidate is built means the beam
                # (which prunes purely by anchor-distance cost) can and
                # does discard every LIL-clean full assignment before ever
                # completing one, when register-tight/anchor-cheap partial
                # states outcompete wider-but-LIL-clean ones at every beam
                # step (dense low-register chords with several roles are
                # especially prone to this). Reject the doomed partial
                # extension immediately instead of paying for it all the
                # way through doubling-variant expansion only to filter it
                # out afterward with nothing to show for it.
                valid_low_interval = True
                for previous in placed:
                    lower = min(previous, p)
                    interval = abs(previous - p)
                    if (
                        (lower < 48 and interval < 7)
                        or (48 <= lower < 55 and interval < 4)
                    ):
                        valid_low_interval = False
                        break
                if not valid_low_interval:
                    continue
                extra = abs(p - anchor_center) * 0.5
                # Keep structural roles in idiomatic register bands while
                # retaining the anchor as the primary global constraint.
                span = window_hi - window_lo
                target_fraction = role_targets.get(role, 0.50)
                if template.startswith("jazz_"):
                    clashes = [
                        other_role
                        for other_role, other_pc in role_pc_map.items()
                        if other_role != role
                        and (pc - other_pc) % 12 in (1, 11)
                    ]
                    # A sounded root and seventh should not form a
                    # one-semitone stack in the lower hand. For jazz
                    # templates, put the root above the shell when that
                    # particular clash exists; the rootless case remains
                    # governed by the ordinary shell mapping.
                    if role == "root" and "7th" in clashes:
                        target_fraction = max(target_fraction, 0.82)
                    elif role == "7th" and "root" in clashes:
                        target_fraction = min(target_fraction, 0.48)
                    elif role in {"root", "3rd", "5th", "7th"} and any(
                            other in {"9th", "11th", "13th"}
                            for other in clashes):
                        target_fraction = min(target_fraction, 0.42)
                    elif role in {"9th", "11th", "13th"} and any(
                            other in {"root", "3rd", "5th", "7th"}
                            for other in clashes):
                        target_fraction = max(target_fraction, 0.80)
                role_target = window_lo + target_fraction * span
                extra += abs(p - role_target) * template_weight
                if anchor_shift > 0 and p < anchor_center:
                    extra += (anchor_center - p) * 0.9
                elif anchor_shift < 0 and p > anchor_center:
                    extra += (p - anchor_center) * 0.9
                if prev_sorted:
                    nearest_prev = min(abs(p - q) for q in prev_sorted)
                    extra += nearest_prev * 0.25
                new_beams.append((assignment + [(role, p)], cost + extra))
        new_beams.sort(key=lambda ac: ac[1])
        beams = new_beams[:beam_width]

    seen = set()
    out = []
    for assignment, _ in beams:
        key = tuple(sorted(p for _, p in assignment))
        if key in seen:
            continue
        seen.add(key)
        out.append(assignment)
    return out


def _weighted_octave_choice(options: list[int], anchor_center: int, rng: random.Random,
                             direction_bias: int = 0) -> int:
    """Bias octave choice toward the anchor, rather than uniform-random
    across the whole window. When `direction_bias` is nonzero (i.e. the
    active section has a genuine `anchor_shift`), also prefer doublings
    placed on the shift's side of the anchor: "nearest to anchor" alone
    still frequently picks an octave on the *opposite* side from a raised
    (or lowered) anchor, and an extra doubled note there reads musically
    as padding, not lift/drop, silently neutralizing the section's
    `anchor_shift`. Sections with no shift (bias=0) get plain symmetric
    weighting -- this asymmetry is opt-in per section, not universal,
    so it doesn't also inflate registers in sections that aren't asking
    for a lift."""
    if len(options) == 1:
        return options[0]
    pool = options
    if direction_bias > 0:
        side = [p for p in options if p >= anchor_center]
        pool = side if side and rng.random() < 0.85 else options
    elif direction_bias < 0:
        side = [p for p in options if p <= anchor_center]
        pool = side if side and rng.random() < 0.85 else options
    weights = [1.0 / (1.0 + abs(p - anchor_center)) for p in pool]
    total = sum(weights)
    r = rng.random() * total
    acc = 0.0
    for p, w in zip(pool, weights):
        acc += w
        if r <= acc:
            return p
    return pool[-1]


def apply_doubling_variants(assignment: list[tuple], doubling_roles: list[str],
                             role_pc_lookup: dict, window_lo: int, window_hi: int,
                             max_doublings: int, rng: random.Random,
                             anchor_center: int = 0, anchor_shift: int = 0,
                             max_variants: int = 40,
                             forced_dct_role: Optional[str] = None) -> list[list[tuple]]:
    """Given a base (undoubled) assignment, produce variants with 0..
    max_doublings extra octave copies of the requested doubling-target
    roles. Always includes the undoubled assignment itself. Explores up to
    `max_doublings` chained additions (not just a fixed 0/1/2 special
    case), which matters for low-cardinality chords (e.g. `triad == "1"`,
    a single required root) where reaching `min_voices` requires several
    doublings of the *same* role. Octave choice for each doubling is
    anchor-weighted (see `_weighted_octave_choice`), not uniform, and
    direction-biased toward `anchor_shift`'s sign when a section profile
    is actively shifting the anchor."""
    variants = [list(assignment)]
    valid_roles = [
        r for r in doubling_roles
        if r in role_pc_lookup and r != forced_dct_role
    ]
    if max_doublings <= 0:
        return variants
    ordinary_budget = max_doublings - (1 if forced_dct_role else 0)
    if ordinary_budget < 0:
        ordinary_budget = 0
    if ordinary_budget == 0 or not valid_roles:
        ordinary_variants = [list(assignment)]
    else:
        ordinary_variants = None

    # Precompute each role's full (cached) octave-option tuple once; the
    # per-attempt `used`-set filtering below is cheap.
    role_options = {
        role: role_octave_options(role_pc_lookup[role], window_lo, window_hi)
        for role in valid_roles
    }

    if ordinary_variants is None:
        seen_keys = {frozenset(p for _, p in assignment)}
        available_pitches = set().union(*role_options.values()) - {
            p for _, p in assignment
        }
        max_reachable = 1 + sum(
            comb(len(available_pitches), n)
            for n in range(1, min(ordinary_budget, len(available_pitches)) + 1)
        )
        target_variants = min(max_variants, max_reachable)
        attempts = 0
        max_attempts = max_variants * 3
        variants = ordinary_variants = [list(assignment)]
        while len(variants) < target_variants and attempts < max_attempts:
            attempts += 1
            n_doublings = rng.randint(1, ordinary_budget)
            current = list(assignment)
            used = {p for _, p in current}
            for _ in range(n_doublings):
                eligible_roles = [
                    candidate_role for candidate_role in valid_roles
                    if candidate_role != "3rd"
                    and not (
                        candidate_role in {"7th", "9th", "11th", "13th"}
                        and sum(1 for existing_role, _ in current
                                if existing_role == candidate_role) >= 1
                    )
                ]
                if not eligible_roles:
                    break
                role = rng.choice(eligible_roles)
                options = [p for p in role_options[role] if p not in used]
                if not options:
                    continue
                p = _weighted_octave_choice(
                    options, anchor_center, rng, direction_bias=anchor_shift
                )
                current.append((role, p))
                used.add(p)
            key = frozenset(used)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            variants.append(current)
    else:
        variants = ordinary_variants
        seen_keys = {
            tuple(sorted(p for _, p in variant))
            for variant in variants
        }

    # Deterministic fallback: greedily add as many distinct octave copies
    # (nearest-to-anchor first, tie-broken toward the shift's direction)
    # as `max_doublings` allows. This guarantees at least one
    # maximally-doubled variant exists even when the random search above
    # is unlucky -- important for low-cardinality chords (e.g.
    # `triad == "1"`, a bare root with no 3rd/5th) where only a handful of
    # octave slots exist at all and reaching `min_voices` is otherwise at
    # the mercy of the RNG.
    def _fallback_key(item):
        dist, role, p = item
        wrong_side = (anchor_shift > 0 and p < anchor_center) or \
                     (anchor_shift < 0 and p > anchor_center)
        return (dist + (2 if wrong_side else 0), role, p)

    all_options = []
    for role in valid_roles:
        for p in role_options[role]:
            all_options.append((abs(p - anchor_center), role, p))
    all_options.sort(key=_fallback_key)
    current = list(assignment)
    used = {p for _, p in current}
    added = 0
    for _, role, p in all_options:
        if added >= ordinary_budget:
            break
        if p in used:
            continue
        if role == "3rd":
            continue
        if role in {"7th", "9th", "11th", "13th"} and any(
                existing_role == role for existing_role, _ in current):
            continue
        current.append((role, p))
        used.add(p)
        added += 1
    key = tuple(sorted(used))
    if key not in seen_keys:
        variants.append(current)

    if forced_dct_role and forced_dct_role in role_pc_lookup:
        # Add exactly one octave copy of the requested DCT. The ordinary
        # doubling budget above reserved one slot for this copy, and the
        # copy is kept near the front so a bounded pool cannot hide it behind
        # unrelated density variants.
        forced_options = role_octave_options(
            role_pc_lookup[forced_dct_role], window_lo, window_hi
        )
        forced_variants = []
        for current in variants:
            dct_pitches = [
                p for role, p in current if role == forced_dct_role
            ]
            if not dct_pitches:
                continue
            used = {p for _, p in current}
            options = [
                p for p in forced_options
                if p not in used and abs(p - dct_pitches[0]) == 12
            ]
            options.sort(key=lambda p: (abs(p - anchor_center), p))
            if options:
                forced_variants.append(
                    current + [(forced_dct_role, options[0])]
                )
        # In the forced branch, keep a valid copy ahead of ordinary density
        # variants so a bounded caller cannot truncate the required DCT copy.
        ordered = list(forced_variants)
        ordered.extend(variants)
        variants = []
        seen = set()
        for variant in ordered:
            key = tuple(sorted(p for _, p in variant))
            if key in seen:
                continue
            seen.add(key)
            variants.append(variant)

    return variants[:max_variants]


_MAX_BASE_ASSIGNMENTS_FOR_DOUBLING = 120


def free_placement(role_pcs: list[tuple], window_lo: int, window_hi: int,
                    anchor_center: int, prev_midi: Optional[list],
                    doubling_roles: list[str], max_doublings: int,
                    rng: random.Random, beam_width: int = 120,
                    max_candidates: int = 400, anchor_shift: int = 0,
                    doubling_pcs: Optional[dict] = None,
                    cluster_min_gap: int = 13,
                    template: str = "balanced",
                    forced_dct_role: Optional[str] = None) -> list[Candidate]:
    """Generic free-placement candidate generator: beam-search octave
    assignment + doubling variants. `role_pcs` is the list of (role, pc)
    pairs for the *required* (non-doubled) degrees only.

    `doubling_pcs` (optional) supplies pitch classes for roles that are
    eligible doubling targets (per `doubling_roles`) but were *thinned out*
    of the core degree set (e.g. a probabilistically-omitted 5th, spec 07
    §4.1). Without this, a thinned role has no pc anywhere in `role_pcs`
    and doubling for it silently becomes a no-op -- which can starve
    low-cardinality chords of a legal >=min_voices candidate entirely when
    that thinned role was the only way to reach the voice-count floor."""
    assignments = beam_octave_assignment(role_pcs, window_lo, window_hi,
                                          anchor_center, prev_midi, beam_width, rng,
                                          anchor_shift=anchor_shift, template=template)
    # See note on `_MAX_BASE_ASSIGNMENTS_FOR_DOUBLING` in
    # `hand_split_free_placement`: expanding doubling variants for every
    # surviving beam is wasteful; the beam is already ordered best-first.
    assignments = assignments[:_MAX_BASE_ASSIGNMENTS_FOR_DOUBLING]
    role_pc_lookup = dict(doubling_pcs or {})
    role_pc_lookup.update({role: pc for role, pc in role_pcs})
    # See `hand_split_free_placement`'s identical mechanism: the beam
    # search minimizes distance-to-anchor independently per role, with no
    # awareness of which *other* roles it clashes with -- for a chord with
    # a semitone-adjacent role pair (e.g. `min7(9,11)`'s 3rd/9th), that
    # reliably packs both roles into the same tight register instead of
    # ever spreading them >= `cluster_min_gap` apart, since anchor-nearness
    # dominates every beam step. This isn't hand-split-specific: it hits
    # plain `free_placement` (pad voicers) exactly the same way.
    clash_color = _clash_bipartition(list(role_pc_lookup.items()), cluster_min_gap)
    clash_edges = _clash_edges(list(role_pc_lookup.items())) if clash_color is not None else []
    out = []
    any_clash_clean = False
    for assignment in assignments:
        remaining = max_candidates - len(out)
        if remaining <= 0:
            break
        for variant in apply_doubling_variants(assignment, doubling_roles, role_pc_lookup,
                                                window_lo, window_hi, max_doublings, rng,
                                                anchor_center=anchor_center,
                                                anchor_shift=anchor_shift,
                                                max_variants=min(40, remaining),
                                                forced_dct_role=forced_dct_role):
            pitches = [p for _, p in variant]
            roles = [r for r, _ in variant]
            forced_copy = (
                forced_dct_role is not None
                and sum(r == forced_dct_role for r in roles)
                > sum(r == forced_dct_role for r, _ in assignment)
            )
            cand = Candidate(
                pitches, roles, meta={
                    "voicing_template": template,
                    "candidate_source": "free_placement",
                    "generation_phase": "normal",
                    "dct_repair": False,
                    "forced_dct_copy": forced_copy,
                    "octave_offset": 0,
                }
            ).dedup_sorted()
            out.append(cand)
            if clash_edges and _candidate_clash_ok(cand, clash_edges, cluster_min_gap):
                any_clash_clean = True
        if len(out) >= max_candidates:
            break

    # Same lazy trigger as `hand_split_free_placement`: use the direct
    # construction when nothing the beam search found is clash-clean.
    #
    # Dense candidate pools also reserve a small direct slice when the beam
    # has a clash-clean result. A beam result can still be unusable after a
    # voicer-specific post-filter (for example, a bottom-register band), and
    # discarding every topology-aware alternative in that case recreates the
    # same coverage gap while the bounded pool is truncated.
    if clash_color is not None:
        direct = _clash_aware_free_candidates(
            role_pcs, role_pc_lookup, clash_color, window_lo, window_hi,
            anchor_center, rng, min_gap=cluster_min_gap, template=template,
            forced_dct_role=(
                forced_dct_role if max_doublings > 0 else None
            ),
        )
        if not any_clash_clean:
            # The direct pool exists specifically because the beam exhausted
            # the available candidate budget without producing a clash-clean
            # realization. Keep it ahead of that beam so the bounded return
            # does not truncate the only topology-aware alternatives.
            combined = direct + out
        elif len(role_pcs) >= 6 and direct:
            reserve = min(len(direct), max(1, max_candidates // 8))
            combined = out[:max_candidates - reserve] + direct[:reserve]
        else:
            combined = out
        return combined[:max_candidates]
    return out[:max_candidates]


def _clash_aware_free_candidates(role_pcs: list[tuple], role_pc_lookup: dict,
                                  clash_color: dict, window_lo: int, window_hi: int,
                                  anchor_center: int, rng: random.Random,
                                  min_gap: int = 13, max_variants: int = 24,
                                  template: str = "balanced",
                                  forced_dct_role: Optional[str] = None) -> list[Candidate]:
    """Generate bounded clash-safe register splits for hand-less voicers.

    Only roles that participate in a semitone-clash edge need to be forced
    onto opposite sides of the anchor. Keeping non-clashing roles free lets
    them occupy the intervening register without making the direct fallback
    unnecessarily rigid (and without creating low-register interval
    violations by packing every core role below the anchor).
    """
    if forced_dct_role is not None and forced_dct_role not in role_pc_lookup:
        return []
    required_roles = [r for r, _ in role_pcs]
    clash_edges = _clash_edges(list(role_pc_lookup.items()))
    clash_roles = {role for edge in clash_edges for role in edge}
    out = []
    for lo_color in (0, 1):
        lo_roles = [
            r for r in required_roles
            if r in clash_roles and clash_color.get(r) == lo_color
        ]
        hi_roles = [
            r for r in required_roles
            if r in clash_roles and clash_color.get(r) == (1 - lo_color)
        ]
        free_roles = [r for r in required_roles if r not in clash_roles]

        lo_hi_bound = anchor_center - 1
        hi_lo_bound = anchor_center + min_gap
        if lo_hi_bound < window_lo or hi_lo_bound > window_hi:
            continue  # this register split doesn't fit inside the window at all

        lo_opts = {r: role_octave_options(role_pc_lookup[r], window_lo, lo_hi_bound)
                   for r in lo_roles}
        hi_opts = {r: role_octave_options(role_pc_lookup[r], hi_lo_bound, window_hi)
                   for r in hi_roles}
        free_opts = {r: role_octave_options(role_pc_lookup[r], window_lo, window_hi)
                     for r in free_roles}
        if (any(not opts for opts in lo_opts.values()) or
                any(not opts for opts in hi_opts.values()) or
                any(not opts for opts in free_opts.values())):
            continue  # some role has no legal pc inside its designated register

        for variant_index in range(max_variants):
            def stable_choice(options, role_index):
                ordered = sorted(
                    options, key=lambda p: (abs(p - anchor_center), p)
                )
                # Keep the first fallback variant as the all-nearest
                # register split. Applying the role offset to variant zero
                # can select a distant option for every other role, which
                # defeats the fallback's purpose when a post-filter also
                # constrains the bottom register.
                choice_index = (
                    variant_index
                    if variant_index == 0
                    else variant_index + role_index
                )
                return ordered[choice_index % len(ordered)]

            placed = [
                (r, stable_choice(lo_opts[r], role_index))
                for role_index, r in enumerate(lo_roles)
            ]
            placed += [
                (r, stable_choice(hi_opts[r], role_index + len(lo_roles)))
                for role_index, r in enumerate(hi_roles)
            ]
            placed += [
                (r, stable_choice(
                    free_opts[r], role_index + len(lo_roles) + len(hi_roles)
                ))
                for role_index, r in enumerate(free_roles)
            ]
            forced_copy = False
            if forced_dct_role is not None:
                dct_pitches = [
                    p for r, p in placed if r == forced_dct_role
                ]
                if not dct_pitches:
                    continue
                forced_options = [
                    p for p in role_octave_options(
                         role_pc_lookup[forced_dct_role], window_lo, window_hi
                    )
                    if p not in {pitch for _, pitch in placed}
                    and abs(p - dct_pitches[0]) == 12
                ]
                if not forced_options:
                    continue
                placed.append((forced_dct_role, min(
                    forced_options, key=lambda p: (abs(p - anchor_center), p)
                )))
                forced_copy = True
            pitches = [p for _, p in placed]
            roles = [r for r, _ in placed]
            out.append(Candidate(
                pitches, roles, meta={
                    "voicing_template": template,
                    "candidate_source": "free_placement",
                    "generation_phase": "normal",
                    "dct_repair": False,
                    "forced_dct_copy": forced_copy,
                    "octave_offset": 0,
                }
            ).dedup_sorted())
    return out


def _merge_template_candidates(candidates: list[Candidate],
                               max_candidates: int) -> list[Candidate]:
    """Remove physical duplicates after several register profiles are merged.

    A profile is only a generation preference; it must not make the same
    realized pitch/hand assignment count several times in the softmax pool.
    """
    out = []
    seen = set()
    for candidate in candidates:
        hands = tuple(
            (name, tuple(values))
            for name, values in sorted((candidate.hands or {}).items())
        )
        key = (tuple(candidate.pitches), tuple(candidate.roles),
               candidate.shape_id, hands)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
        if len(out) >= max_candidates:
            break
    return out


def _isolated_profile_args(args: tuple, profile: str,
                           target_profile: Optional[str]) -> tuple:
    """Give an expanded target profile a private RNG stream.

    Reserving extra candidates for a target is intentionally additive, but
    consuming those extra random draws from the engine stream would change
    every later chord in a song. The normal profiles keep the caller's RNG;
    only the extra target generation is isolated.
    """
    if profile != target_profile:
        return args
    rng_index = next(
        (index for index, value in enumerate(args)
         if isinstance(value, random.Random)),
        None,
    )
    if rng_index is None:
        return args
    local_rng = random.Random()
    local_rng.setstate(args[rng_index].getstate())
    profile_args = list(args)
    profile_args[rng_index] = local_rng
    return tuple(profile_args)


def free_placement_templates(*args, templates=("balanced",),
                              preferred_template: Optional[str] = None,
                              **kwargs) -> list[Candidate]:
    """Generate one bounded candidate pool for each explicit profile.

    Keeping profile generation separate from selection gives the engine
    several intentional register families while retaining its existing
    voice-leading, diversity, and hard-gate scoring.
    """
    profiles = tuple(dict.fromkeys(templates or ("balanced",)))
    requested = int(kwargs.get("max_candidates", 400))
    # Preserve the original pool for difficult chords, then append a
    # bounded slice from each alternate profile. Capping the merged result
    # at `requested` would let the first (balanced) pool consume the entire
    # output and make the new templates inert.
    per_profile = max(24, (requested + (2 * len(profiles)) - 1)
                      // (2 * len(profiles)))
    target_profile = (
        preferred_template
        if preferred_template in profiles and preferred_template != "balanced"
        else None
    )
    # Keep a bounded target slice ahead of the balanced pool. The old
    # balanced-first ordering made alternate profiles inert for low-cardinality
    # chords: balanced often generated every physically reachable pitch set
    # before an alternate profile was merged, so deduplication discarded the
    # alternate metadata and its target preference. The balanced pool remains
    # full-sized as a fallback; only the target profile receives an additional
    # reserved slice, capped so dense synth pools do not grow without bound.
    target_budget = (
        max(per_profile, min(requested, max(96, per_profile * 3)))
        if target_profile is not None else 0
    )
    generated = []
    profile_budgets = {}
    for profile in profiles:
        if profile == "balanced":
            profile_budgets[profile] = requested
        elif profile == target_profile:
            profile_budgets[profile] = target_budget
        else:
            profile_budgets[profile] = per_profile
    generated_by_profile = {}
    for profile in profiles:
        profile_kwargs = dict(kwargs)
        profile_kwargs["template"] = profile
        # Keep the original balanced pool intact. Additional profiles are
        # additive alternatives, not a reason to starve the baseline pool
        # of candidates that previously made difficult chords realizable.
        profile_kwargs["max_candidates"] = profile_budgets[profile]
        profile_args = _isolated_profile_args(args, profile, target_profile)
        generated_by_profile[profile] = free_placement(*profile_args, **profile_kwargs)
    merge_profiles = (
        (target_profile,) + tuple(profile for profile in profiles
                                  if profile != target_profile)
        if target_profile is not None else profiles
    )
    for profile in merge_profiles:
        generated.extend(generated_by_profile[profile])
    expanded_limit = sum(profile_budgets.values())
    return _merge_template_candidates(generated, expanded_limit)


def _clash_bipartition(role_pcs: list[tuple], min_gap: int) -> Optional[dict]:
    """Static (per-chord, not per-candidate) 2-coloring of roles by the
    semitone-cluster clash graph (spec 07 §7.3: any two roles whose pitch
    classes are a semitone apart must end up >= `min_gap` semitones apart
    in the realized voicing). For chords with *multiple simultaneous*
    clash pairs (e.g. an altered dominant `7(b9,11,13)`: root/9th,
    3rd/11th, and 7th/13th are all semitone-adjacent at once), the purely
    geometric "try a few contiguous splits by absolute pitch" search below
    can fail to find *any* valid LH/RH partition even though a musically
    obvious one exists -- put every role on one side of each clash pair
    into a different hand, and >= min_gap separation follows for free from
    the hands' own register separation. Returns a role -> {0, 1} coloring
    if the clash graph is bipartite (always true for the disjoint-pair
    case that motivated this; a genuinely non-bipartite clash graph, e.g.
    three degrees in a mutual semitone cycle, is vanishingly rare and
    simply isn't given this extra fallback), else None.

    Coloring is seeded by role *category* (core chord tones -- root/3rd/
    5th/7th -- vs. tensions -- 9th/11th/13th and their altered spellings)
    rather than by arbitrary per-component traversal order. An altered
    dominant's three clash pairs (root/9th, 3rd/11th, 7th/13th) are three
    *disjoint* graph components -- a plain per-component BFS colors each
    one independently and has no reason to land all three "core" members
    on the same side, so it produces a globally scrambled coloring (e.g.
    root+3rd+7th split across both hands) roughly as often as the correct
    one. Seeding by category instead guarantees every core tone lands
    opposite every tension it clashes with *and* all core tones land on
    the same side as each other (matching LH's 3-voice cap almost
    exactly: root+3rd+7th), which is what actually makes a clash-free
    hand split constructible."""
    roles = sorted({role for role, _ in role_pcs})
    pcs = {role: pc % 12 for role, pc in role_pcs}
    adj = {role: set() for role in roles}
    for i, r1 in enumerate(roles):
        for r2 in roles[i + 1:]:
            gap = (pcs[r1] - pcs[r2]) % 12
            if gap in (1, 11):
                adj[r1].add(r2)
                adj[r2].add(r1)
    if not any(adj[r] for r in roles):
        return None  # no clash pairs at all -- nothing for this to help with
    core_roles = {"root", "3rd", "5th", "7th"}
    color = {r: (0 if r in core_roles else 1) for r in roles}
    # Validate: every clash edge must connect a core role to a
    # non-core (tension) role. If some edge violates this (e.g. two
    # tensions a semitone apart, or a core/core clash), the simple
    # category seed doesn't produce a valid 2-coloring for this chord's
    # graph -- fall back to a plain per-component BFS coloring instead of
    # silently returning an inconsistent one.
    if all(color[r1] != color[r2] for r1 in roles for r2 in adj[r1]):
        return color
    color = {}
    for start in roles:
        if start in color:
            continue
        color[start] = 0
        stack = [start]
        while stack:
            node = stack.pop()
            for neigh in adj[node]:
                if neigh in color:
                    if color[neigh] == color[node]:
                        return None  # odd cycle -- not bipartite
                    continue
                color[neigh] = 1 - color[node]
                stack.append(neigh)
    return color


def _clash_edges(role_pcs: list[tuple]) -> list[tuple]:
    """The semitone-adjacent role pairs `_clash_bipartition` 2-colors --
    factored out so a realized candidate can be checked against the same
    edge set without redoing the full bipartition."""
    roles = sorted({role for role, _ in role_pcs})
    pcs = {role: pc % 12 for role, pc in role_pcs}
    edges = []
    for i, r1 in enumerate(roles):
        for r2 in roles[i + 1:]:
            if (pcs[r1] - pcs[r2]) % 12 in (1, 11):
                edges.append((r1, r2))
    return edges


def _candidate_clash_ok(cand: Candidate, edges: list[tuple], min_gap: int) -> bool:
    """Whether a realized candidate already keeps every semitone-clash
    pair (per `_clash_edges`) >= `min_gap` semitones apart *and* satisfies
    the low-interval limit -- both are spec 07 §7.3 hard gates checked
    downstream by the engine (`spacing.spacing_hard_ok`), and a candidate
    that dodges the cluster rule only to violate LIL is just as
    unusable. Used to decide whether the (RNG-consuming) clash-aware
    direct-construction fallback is actually needed for this specific
    chord instance, rather than always running it whenever a clash graph
    merely exists -- most contiguous splits for *most* voicings of a
    clashy chord type still turn out clean on both counts, and
    unconditionally spending extra RNG draws on every one of them would
    shift downstream randomness for chords that never needed the
    fallback at all."""
    from .spacing import low_interval_limit_ok
    if not low_interval_limit_ok(cand.pitches):
        return False
    # A plain dict(zip(roles, pitches)) silently keeps only the *last*
    # pitch for a doubled role -- if e.g. root is doubled (a very common
    # doubling target) and one copy clashes with the 7th while the other
    # doesn't, last-wins could pick the clean copy and wrongly report the
    # whole candidate clash-free, even though the realized pitch set (all
    # copies) genuinely violates the semitone-cluster rule. Check every
    # realized copy of each clash-adjacent role against every copy of its
    # partner, not just one representative pair.
    role_to_pitches: dict = {}
    for r, p in zip(cand.roles, cand.pitches):
        role_to_pitches.setdefault(r, []).append(p)
    for r1, r2 in edges:
        for p1 in role_to_pitches.get(r1, ()):
            for p2 in role_to_pitches.get(r2, ()):
                if abs(p1 - p2) < min_gap:
                    return False
    return True


def _clash_aware_direct_candidates(role_pcs: list[tuple], role_pc_lookup: dict,
                                    clash_color: dict, lh_window: tuple, rh_window: tuple,
                                    lh_max_voices: int, rh_max_voices: int,
                                    anchor_center: int, rng: random.Random,
                                    max_hand_span: int, max_variants: int = 24,
                                    template: str = "balanced",
                                    forced_dct_role: Optional[str] = None,
                                    max_voices: Optional[int] = None) -> list[Candidate]:
    """Direct (not beam-search-derived) fallback candidate construction for
    when the shared octave-assignment beam search + contiguous-by-pitch
    hand split finds *no* valid partition at all (see `_clash_bipartition`).

    The post-hoc approach of 2-coloring roles and then trying to slot an
    *already beam-placed* assignment into that coloring only works when
    the beam search happens to have placed each colored role's pitch
    inside its designated hand's window already -- which, since the beam
    search optimizes purely for anchor/continuity distance with no
    awareness of hand-window boundaries, is rare (empirically ~0.04% of
    assignment x variant combinations for a representative dense altered
    dominant). This instead places each clash-colored role's octave
    *directly* within its designated hand's window from the start, so the
    window constraint is satisfied by construction rather than by luck."""
    if forced_dct_role is not None and forced_dct_role not in role_pc_lookup:
        return []
    required_roles = [r for r, _ in role_pcs]
    out = []
    for lh_color in (0, 1):
        lh_roles = [r for r in required_roles if clash_color.get(r) == lh_color]
        rh_roles = [r for r in required_roles if clash_color.get(r) == (1 - lh_color)]
        free_roles = [r for r in required_roles if r not in clash_color]
        feasible = True
        for r in free_roles:
            if len(lh_roles) < lh_max_voices and len(lh_roles) <= len(rh_roles):
                lh_roles.append(r)
            elif len(rh_roles) < rh_max_voices:
                rh_roles.append(r)
            elif len(lh_roles) < lh_max_voices:
                lh_roles.append(r)
            else:
                feasible = False
                break
        if not feasible or len(lh_roles) > lh_max_voices or len(rh_roles) > rh_max_voices:
            continue

        lh_opts = {r: role_octave_options(role_pc_lookup[r], lh_window[0], lh_window[1])
                   for r in lh_roles}
        rh_opts = {r: role_octave_options(role_pc_lookup[r], rh_window[0], rh_window[1])
                   for r in rh_roles}
        if any(not opts for opts in lh_opts.values()) or any(not opts for opts in rh_opts.values()):
            continue  # this role simply has no legal pc inside its designated hand's window

        for variant_index in range(max_variants):
            def stable_choice(options, role_index):
                ordered = sorted(
                    options, key=lambda p: (abs(p - anchor_center), p)
                )
                return ordered[(variant_index + role_index) % len(ordered)]

            lh_placed = [
                (r, stable_choice(lh_opts[r], role_index))
                for role_index, r in enumerate(lh_roles)
            ]
            rh_placed = [
                (r, stable_choice(rh_opts[r], role_index + len(lh_roles)))
                for role_index, r in enumerate(rh_roles)
            ]
            forced_copy = None
            if forced_dct_role is not None:
                dct_pitches = [
                    p for r, p in lh_placed + rh_placed
                    if r == forced_dct_role
                ]
                if not dct_pitches:
                    continue
                used = set(dct_pitches)
                for hand_name, hand_window in (
                        ("lh", lh_window), ("rh", rh_window)):
                    for option in role_octave_options(
                            role_pc_lookup[forced_dct_role],
                            hand_window[0], hand_window[1]):
                        if abs(option - dct_pitches[0]) != 12 or option in used:
                            continue
                        forced_copy = (hand_name, option)
                        break
                    if forced_copy is not None:
                        break
                if forced_copy is None:
                    continue
                if max_voices is not None and \
                        len(lh_placed) + len(rh_placed) >= max_voices:
                    continue
                if forced_copy[0] == "lh":
                    lh_placed.append((forced_dct_role, forced_copy[1]))
                else:
                    rh_placed.append((forced_dct_role, forced_copy[1]))
            lh_pitches = [p for _, p in lh_placed]
            rh_pitches = [p for _, p in rh_placed]
            if lh_pitches and (max(lh_pitches) - min(lh_pitches)) > max_hand_span:
                continue
            if rh_pitches and (max(rh_pitches) - min(rh_pitches)) > max_hand_span:
                continue
            pitches = lh_pitches + rh_pitches
            roles = [r for r, _ in lh_placed] + [r for r, _ in rh_placed]
            out.append(Candidate(pitches, roles,
                                  hands={"lh": lh_pitches, "rh": rh_pitches},
                                  meta={
                                      "voicing_template": template,
                                      "forced_dct_copy": forced_copy is not None,
                                  }).dedup_sorted())
    return out


def hand_split_free_placement(role_pcs: list[tuple], lh_window: tuple, rh_window: tuple,
                               lh_max_voices: int, rh_max_voices: int, max_hand_span: int,
                               anchor_center: int, prev_midi: Optional[list],
                               doubling_roles: list[str], max_doublings: int,
                               rng: random.Random, beam_width: int = 120,
                               max_candidates: int = 400, anchor_shift: int = 0,
                               doubling_pcs: Optional[dict] = None,
                               cluster_min_gap: int = 13,
                               template: str = "balanced",
                               role_windows: Optional[dict] = None,
                               forced_dct_role: Optional[str] = None) -> list[Candidate]:
    """Piano hand-split candidate generator (specs 01, 04): beam-search over
    the combined window, then greedily partition into LH (lowest voices) /
    RH (remaining), rejecting partitions that violate voice caps or hand
    span. This is a practical realization of spec 01 §5 / spec 04 §4.

    See `free_placement`'s docstring for why `doubling_pcs` exists: it
    covers doubling-eligible roles (e.g. a probabilistically-thinned 5th)
    that were dropped from the core `role_pcs` degree set."""
    window_lo = min(lh_window[0], rh_window[0])
    window_hi = max(lh_window[1], rh_window[1])
    assignments = beam_octave_assignment(role_pcs, window_lo, window_hi,
                                          anchor_center, prev_midi, beam_width, rng,
                                          anchor_shift=anchor_shift, template=template,
                                          role_windows=role_windows)
    # Expanding doubling variants for every surviving beam assignment (up
    # to `beam_width`, e.g. 120) is wasteful -- profiling showed this
    # fan-out as the dominant cost in the whole engine. The beam search
    # already orders assignments best-first by anchor/continuity cost, so
    # capping to the top N keeps candidate quality/diversity while cutting
    # the combinatorial blow-up; `_select`'s real cost function still runs
    # over whatever survives, so this doesn't change which candidate wins.
    assignments = assignments[:_MAX_BASE_ASSIGNMENTS_FOR_DOUBLING]
    role_pc_lookup = dict(doubling_pcs or {})
    role_pc_lookup.update({role: pc for role, pc in role_pcs})
    # Computed once per chord (roles/pcs are fixed across every octave
    # assignment and doubling variant below) -- see `_clash_bipartition`'s
    # docstring for why this exists: chords with multiple simultaneous
    # semitone-clash pairs (e.g. altered-dominant `7(b9,11,13)`) can have
    # *zero* valid contiguous-by-pitch LH/RH split even though an obvious
    # role-based one exists.
    clash_color = _clash_bipartition(list(role_pc_lookup.items()), cluster_min_gap)
    clash_edges = _clash_edges(list(role_pc_lookup.items())) if clash_color is not None else []
    out = []
    any_clash_clean = False
    for assignment in assignments:
        remaining = max_candidates - len(out)
        if remaining <= 0:
            break
        for variant in apply_doubling_variants(assignment, doubling_roles, role_pc_lookup,
                                                window_lo, window_hi, max_doublings, rng,
                                                anchor_center=anchor_center,
                                                anchor_shift=anchor_shift,
                                                max_variants=min(40, remaining),
                                                forced_dct_role=forced_dct_role):
            sorted_variant = sorted(variant, key=lambda rp: rp[1])
            n = len(sorted_variant)
            lh_target = min(lh_max_voices, n - 1) if n > 1 else n
            found = False
            # try a few split points, pick the ones that satisfy hand caps/spans
            for split in range(min(lh_target, n), 0, -1):
                lh_part = sorted_variant[:split]
                rh_part = sorted_variant[split:]
                if len(lh_part) > lh_max_voices or len(rh_part) > rh_max_voices:
                    continue
                if not rh_part:
                    continue
                lh_pitches = [p for _, p in lh_part]
                rh_pitches = [p for _, p in rh_part]
                if lh_pitches and (max(lh_pitches) - min(lh_pitches)) > max_hand_span:
                    continue
                if rh_pitches and (max(rh_pitches) - min(rh_pitches)) > max_hand_span:
                    continue
                if not all(lo <= p <= hi for p in lh_pitches for lo, hi in [lh_window]):
                    continue
                if not all(lo <= p <= hi for p in rh_pitches for lo, hi in [rh_window]):
                    continue
                pitches = lh_pitches + rh_pitches
                roles = [r for r, _ in lh_part] + [r for r, _ in rh_part]
                forced_copy = (
                    forced_dct_role is not None
                    and sum(r == forced_dct_role for r in roles)
                    > sum(r == forced_dct_role for r, _ in assignment)
                )
                cand = Candidate(pitches, roles,
                                  hands={"lh": lh_pitches, "rh": rh_pitches},
                                  meta={
                                      "voicing_template": template,
                                      "candidate_source": "free_placement",
                                      "generation_phase": "normal",
                                      "dct_repair": False,
                                      "forced_dct_copy": forced_copy,
                                      "octave_offset": 0,
                                  }).dedup_sorted()
                out.append(cand)
                if clash_edges and _candidate_clash_ok(cand, clash_edges, cluster_min_gap):
                    any_clash_clean = True
                found = True
                break  # first valid split for this variant is enough
        if len(out) >= max_candidates:
            break

    # Only spend the (RNG-consuming) clash-aware direct-construction
    # fallback when it's actually needed: not merely "a clash graph
    # exists for this chord type" (`clash_color is not None`), but "none
    # of the contiguous splits found above are actually clash-clean" --
    # see `_candidate_clash_ok`'s docstring. Gating on "exists" alone
    # would run this fallback (and its extra RNG draws) on every instance
    # of a clashy chord type even when the very first contiguous split
    # already happened to satisfy `cluster_min_gap`, needlessly shifting
    # downstream randomness for chords that never needed it.
    if clash_color is not None and not any_clash_clean:
        direct = _clash_aware_direct_candidates(
            role_pcs, role_pc_lookup, clash_color, lh_window, rh_window,
            lh_max_voices, rh_max_voices, anchor_center, rng, max_hand_span,
            template=template,
            forced_dct_role=(
                forced_dct_role if max_doublings > 0 else None
            ),
            max_voices=lh_max_voices + rh_max_voices,
        )
        combined = (direct + out) if forced_dct_role is not None else (out + direct)
        return combined[:max_candidates]
    return out[:max_candidates]


def dense_hand_layout(role_pcs: list[tuple], lh_window: tuple, rh_window: tuple,
                      lh_max_voices: int, rh_max_voices: int,
                      max_hand_span: int, anchor_center: int,
                      max_voices: int, max_candidates: int = 512,
                      template: str = "dense_hand",
                      role_windows: Optional[dict] = None,
                      forced_dct_role: Optional[str] = None,
                      max_doublings: Optional[int] = None) -> list[Candidate]:
    """Enumerate bounded, role-aware piano hand layouts.

    Unlike the normal source, this intentionally does not require the sorted
    pitch order to determine the hand partition. It is a deterministic
    fallback for topology-starved dense chords; the engine still applies all
    sound-set, spacing, DCT, drift, and post-filter gates before selection.
    """
    roles = list(role_pcs)
    if not roles or len(roles) > max_voices:
        return []
    if len(roles) > lh_max_voices + rh_max_voices:
        return []

    core_roles = {"root", "3rd", "5th", "7th"}
    partitions = []
    for mask in range(1, (1 << len(roles)) - 1):
        lh_roles = [role for index, (role, _pc) in enumerate(roles)
                    if mask & (1 << index)]
        rh_roles = [role for index, (role, _pc) in enumerate(roles)
                    if not mask & (1 << index)]
        if not lh_roles or not rh_roles:
            continue
        if len(lh_roles) > lh_max_voices or len(rh_roles) > rh_max_voices:
            continue
        # Core tones in the LH and differentiating tensions in the RH are a
        # preference only; every feasible non-contiguous assignment remains
        # eligible.
        preference = sum(
            0 if ((role in core_roles) == (role in lh_roles))
            else 1
            for role, _pc in roles
        )
        partitions.append((preference, mask, lh_roles, rh_roles))
    partitions.sort(key=lambda item: (item[0], item[1]))

    out = []
    for _preference, _mask, lh_roles, rh_roles in partitions:
        lh_set = set(lh_roles)
        windows = {}
        for role, pc in roles:
            hand_window = lh_window if role in lh_set else rh_window
            if role_windows and role in role_windows:
                hand_window = role_windows[role]
            options = tuple(
                p for p in range(hand_window[0], hand_window[1] + 1)
                if p % 12 == pc % 12
            )
            if not options:
                break
            target = (hand_window[0] + hand_window[1]) / 2
            windows[role] = tuple(sorted(options, key=lambda p: (abs(p - target), p)))
        else:
            # Assign the most constrained roles first, while retaining the
            # original degree order as a stable tie-breaker.
            ordered_roles = sorted(
                roles,
                key=lambda item: (len(windows[item[0]]),
                                  next(i for i, pair in enumerate(roles)
                                       if pair[0] == item[0]))
            )

            def visit(position: int, chosen: dict[str, int]) -> None:
                if len(out) >= max_candidates:
                    return
                if position == len(ordered_roles):
                    lh = sorted(chosen[role] for role in lh_roles)
                    rh = sorted(chosen[role] for role in rh_roles)

                    def emit(extra=None) -> None:
                        entries = list(chosen.items())
                        forced_copy = extra is not None
                        if extra is not None:
                            extra_role, extra_pitch, extra_hand = extra
                            entries.append((extra_role, extra_pitch))
                        candidate_lh = list(lh)
                        candidate_rh = list(rh)
                        if extra is not None:
                            if extra_hand == "lh":
                                candidate_lh.append(extra_pitch)
                                candidate_lh.sort()
                            else:
                                candidate_rh.append(extra_pitch)
                                candidate_rh.sort()
                        if len(entries) > max_voices or \
                                len(candidate_lh) > lh_max_voices or \
                                len(candidate_rh) > rh_max_voices:
                            return
                        if (candidate_lh and
                                candidate_lh[-1] - candidate_lh[0] > max_hand_span) or \
                                (candidate_rh and
                                 candidate_rh[-1] - candidate_rh[0] > max_hand_span):
                            return
                        pitches = candidate_lh + candidate_rh
                        if len(pitches) != len(set(pitches)) or \
                                not low_interval_limit_ok(pitches):
                            return
                        ordered = sorted(
                            ((pitch, role) for role, pitch in entries),
                            key=lambda pair: (pair[0], pair[1])
                        )
                        out.append(Candidate(
                            [pitch for pitch, _role in ordered],
                            [role for _pitch, role in ordered],
                            hands={"lh": candidate_lh, "rh": candidate_rh},
                            meta={
                                "candidate_source": "dense_hand",
                                "generation_phase": "dense_hand",
                                "dct_repair": False,
                                "forced_dct_copy": forced_copy,
                                "octave_offset": 0,
                                "voicing_template": template,
                            },
                        ))

                    # The forced branch is the only case where an added DCT
                    # copy is required; put those variants first so the
                    # bounded fallback cannot hide them behind base layouts.
                    forced_variants = []
                    if (forced_dct_role is not None
                            and forced_dct_role in chosen
                            and (max_doublings is None or max_doublings > 0)):
                        dct_pitch = chosen[forced_dct_role]
                        used = set(chosen.values())
                        for hand_name, hand_window in (
                                ("lh", lh_window), ("rh", rh_window)):
                            copy_window = hand_window
                            if role_windows and forced_dct_role in role_windows:
                                role_window = role_windows[forced_dct_role]
                                copy_window = (
                                    max(copy_window[0], role_window[0]),
                                    min(copy_window[1], role_window[1]),
                                )
                            dct_pc = next(
                                pc for role, pc in roles
                                if role == forced_dct_role
                            )
                            for copy_pitch in sorted(
                                    role_octave_options(
                                        dct_pc, copy_window[0], copy_window[1]
                                    ),
                                    key=lambda pitch: (
                                        abs(pitch - anchor_center), pitch
                                    )):
                                if copy_pitch in used or \
                                        abs(copy_pitch - dct_pitch) != 12:
                                    continue
                                forced_variants.append(
                                    (forced_dct_role, copy_pitch, hand_name)
                                )
                    for extra in forced_variants:
                        emit(extra)
                        if len(out) >= max_candidates:
                            return
                    emit()
                    return
                role, _pc = ordered_roles[position]
                for pitch in windows[role]:
                    if pitch in chosen.values():
                        continue
                    chosen[role] = pitch
                    visit(position + 1, chosen)
                    del chosen[role]
                    if len(out) >= max_candidates:
                        return

            visit(0, {})
    return out


def hand_split_free_placement_templates(*args, templates=("balanced",),
                                        preferred_template: Optional[str] = None,
                                        **kwargs) -> list[Candidate]:
    """Hand-split counterpart to :func:`free_placement_templates`."""
    profiles = tuple(dict.fromkeys(templates or ("balanced",)))
    requested = int(kwargs.get("max_candidates", 400))
    per_profile = max(24, (requested + (2 * len(profiles)) - 1)
                      // (2 * len(profiles)))
    target_profile = (
        preferred_template
        if preferred_template in profiles and preferred_template != "balanced"
        else None
    )
    target_budget = (
        max(per_profile, min(requested, max(96, per_profile * 3)))
        if target_profile is not None else 0
    )
    role_windows_by_template = kwargs.pop("role_windows_by_template", {})
    generated = []
    profile_budgets = {}
    for profile in profiles:
        if profile == "balanced":
            profile_budgets[profile] = requested
        elif profile == target_profile:
            profile_budgets[profile] = target_budget
        else:
            profile_budgets[profile] = per_profile
    generated_by_profile = {}
    for profile in profiles:
        profile_kwargs = dict(kwargs)
        profile_kwargs["template"] = profile
        if profile in role_windows_by_template:
            profile_kwargs["role_windows"] = role_windows_by_template[profile]
        profile_kwargs["max_candidates"] = profile_budgets[profile]
        profile_args = _isolated_profile_args(args, profile, target_profile)
        generated_by_profile[profile] = hand_split_free_placement(
            *profile_args, **profile_kwargs
        )
    merge_profiles = (
        (target_profile,) + tuple(profile for profile in profiles
                                  if profile != target_profile)
        if target_profile is not None else profiles
    )
    for profile in merge_profiles:
        generated.extend(generated_by_profile[profile])
    expanded_limit = sum(profile_budgets.values())
    return _merge_template_candidates(generated, expanded_limit)
