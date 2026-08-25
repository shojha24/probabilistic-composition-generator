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
                       anchor_center: int, max_variants: int = 24):
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
        opts.sort(key=lambda p: abs(p - anchor_center))
        return [p for p in opts if _isolated_against(p, others)]

    dct_idx = next((i for i, r in enumerate(roles) if r == dct_role), None)
    if dct_idx is None:
        return  # DCT dropped -- never allowed, caller must reject anyway

    if sampled_branch == "top":
        # Trivially satisfies predicate_top (and usually predicate_isolated
        # too, given the resulting gap) by placing the DCT's pc strictly
        # above every other currently-placed pitch.
        pc = base_pitches[dct_idx] % 12
        current_max = max(p for j, p in enumerate(base_pitches) if j != dct_idx)
        dct_options = [p for p in range(window_lo, window_hi + 1)
                       if p % 12 == pc and p > current_max]
        dct_options.sort(key=lambda p: abs(p - anchor_center))
    else:
        # "isolated" and "octave" branches: "isolated" is what this helper
        # can directly guarantee; an "octave"-branch candidate that instead
        # ends up isolated still passes spec 07 §9 step 7's branch
        # relaxation once the ladder reaches it, so this is a reasonable
        # best-effort even when the originally sampled branch was "octave".
        dct_options = _options_for(dct_idx, base_pitches)
    if not dct_options:
        return

    sec_idxs = [next((i for i, r in enumerate(roles) if r == sr), None)
                for sr in secondary_roles]
    if any(i is None for i in sec_idxs):
        return

    count = 0
    for dp in dct_options:
        trial = list(base_pitches)
        trial[dct_idx] = dp
        # Secondary roles are repositioned against the trial pitches
        # (including the just-moved DCT), one at a time; if any has no
        # isolated option left, this dct_option is unusable.
        ok = True
        for sidx in sec_idxs:
            sopts = _options_for(sidx, trial)
            if not sopts:
                ok = False
                break
            trial[sidx] = sopts[0]
        if not ok:
            continue
        yield Candidate(trial, roles, candidate.shape_id, candidate.hands,
                         dict(candidate.meta)).dedup_sorted()
        count += 1
        if count >= max_variants:
            return




def post_filter_repair(candidate: "Candidate", window_lo: int, window_hi: int,
                        anchor_center: int, min_gap: int,
                        post_filter_fn, prev, ctx: dict, policy) -> Optional["Candidate"]:
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
        return None
    bottom_idx = min(range(len(pitches)), key=lambda i: pitches[i])
    pc = pitches[bottom_idx] % 12
    others = [p for j, p in enumerate(pitches) if j != bottom_idx]
    options = [p for p in range(window_lo, window_hi + 1) if p % 12 == pc]
    options.sort(key=lambda p: abs(p - anchor_center))
    for p in options:
        if p == pitches[bottom_idx]:
            continue
        # avoid reintroducing a semitone-cluster clash against any other
        # already-placed pitch at the new position.
        if any(((p - q) % 12 in (1, 11)) and abs(p - q) < min_gap for q in others):
            continue
        trial_pitches = list(pitches)
        trial_pitches[bottom_idx] = p
        trial = Candidate(trial_pitches, roles, candidate.shape_id, candidate.hands,
                           dict(candidate.meta)).dedup_sorted()
        if post_filter_fn(trial, prev, ctx, policy):
            return trial
    return None


@lru_cache(maxsize=4096)
def role_octave_options(pc: int, window_lo: int, window_hi: int) -> tuple:
    """Cached: this is pure/static in its arguments and gets called with the
    same (pc, window) pairs an enormous number of times during doubling
    exploration -- profiling showed this rebuilding the same `range()`
    filter ~3.8M times in a 3-song run before caching."""
    return tuple(p for p in range(window_lo, window_hi + 1) if p % 12 == pc % 12)


def beam_octave_assignment(role_pcs: list[tuple], window_lo: int, window_hi: int,
                            anchor_center: int, prev_midi: Optional[list],
                            beam_width: int = 120,
                            rng: Optional[random.Random] = None,
                            anchor_shift: int = 0) -> list[list[tuple]]:
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
    beams = [([], 0.0)]  # (assignment_so_far, partial_cost)
    prev_sorted = sorted(prev_midi) if prev_midi else []

    for role, pc in role_pcs:
        options = role_octave_options(pc, window_lo, window_hi)
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
                if placed and not low_interval_limit_ok(placed + [p]):
                    continue
                extra = abs(p - anchor_center) * 0.5
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
                             max_variants: int = 40) -> list[list[tuple]]:
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
    valid_roles = [r for r in doubling_roles if r in role_pc_lookup]
    if max_doublings <= 0 or not valid_roles:
        return variants

    # Precompute each role's full (cached) octave-option tuple once; the
    # per-attempt `used`-set filtering below is cheap, but re-deriving the
    # option list itself on every attempt (as before) was the single
    # largest hotspot in profiling (~3.8M redundant calls in a 3-song run).
    role_options = {role: role_octave_options(role_pc_lookup[role], window_lo, window_hi)
                    for role in valid_roles}

    seen_keys = {frozenset(p for _, p in assignment)}
    attempts = 0
    # A generous-but-bounded search budget: the deterministic fallback below
    # guarantees at least one feasible maximally-doubled variant regardless,
    # so this only needs to be "enough for good diversity," not "enough to
    # guarantee success."
    max_attempts = max_variants * 3
    while len(variants) < max_variants and attempts < max_attempts:
        attempts += 1
        n_doublings = rng.randint(1, max_doublings)
        current = list(assignment)
        used = {p for _, p in current}
        for _ in range(n_doublings):
            role = rng.choice(valid_roles)
            options = [p for p in role_options[role] if p not in used]
            if not options:
                continue
            p = _weighted_octave_choice(options, anchor_center, rng, direction_bias=anchor_shift)
            current.append((role, p))
            used.add(p)
        key = frozenset(p for _, p in current)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        variants.append(current)

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
        if added >= max_doublings:
            break
        if p in used:
            continue
        current.append((role, p))
        used.add(p)
        added += 1
    key = tuple(sorted(p for _, p in current))
    if key not in seen_keys:
        variants.append(current)

    return variants[:max(max_variants, len(variants))]


_MAX_BASE_ASSIGNMENTS_FOR_DOUBLING = 120


def free_placement(role_pcs: list[tuple], window_lo: int, window_hi: int,
                    anchor_center: int, prev_midi: Optional[list],
                    doubling_roles: list[str], max_doublings: int,
                    rng: random.Random, beam_width: int = 120,
                    max_candidates: int = 400, anchor_shift: int = 0,
                    doubling_pcs: Optional[dict] = None,
                    cluster_min_gap: int = 13) -> list[Candidate]:
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
                                          anchor_shift=anchor_shift)
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
        for variant in apply_doubling_variants(assignment, doubling_roles, role_pc_lookup,
                                                window_lo, window_hi, max_doublings, rng,
                                                anchor_center=anchor_center,
                                                anchor_shift=anchor_shift):
            pitches = [p for _, p in variant]
            roles = [r for r, _ in variant]
            cand = Candidate(pitches, roles).dedup_sorted()
            out.append(cand)
            if clash_edges and _candidate_clash_ok(cand, clash_edges, cluster_min_gap):
                any_clash_clean = True
        if len(out) >= max_candidates:
            break

    # Same lazy trigger as `hand_split_free_placement`: only pay for the
    # (RNG-consuming) direct-construction fallback when nothing the beam
    # search found is actually clash-clean.
    if clash_color is not None and not any_clash_clean:
        direct = _clash_aware_free_candidates(
            role_pcs, role_pc_lookup, clash_color, window_lo, window_hi,
            anchor_center, rng, min_gap=cluster_min_gap,
        )
        return out[:max_candidates] + direct
    return out[:max_candidates]


def _clash_aware_free_candidates(role_pcs: list[tuple], role_pc_lookup: dict,
                                  clash_color: dict, window_lo: int, window_hi: int,
                                  anchor_center: int, rng: random.Random,
                                  min_gap: int = 13, max_variants: int = 24) -> list[Candidate]:
    """`_clash_aware_direct_candidates`'s counterpart for hand-less voicers
    (pads/synths via plain `free_placement`): places every clash-colored
    role in one of two registers -- strictly below `anchor_center` or at
    /above `anchor_center + min_gap` -- which guarantees >= `min_gap`
    separation between any two roles on opposite sides by construction,
    rather than relying on the anchor-nearness-only beam search to
    accidentally spread them apart (see `free_placement`'s comment on why
    it doesn't)."""
    required_roles = [r for r, _ in role_pcs]
    out = []
    for lo_color in (0, 1):
        lo_roles = [r for r in required_roles if clash_color.get(r) == lo_color]
        hi_roles = [r for r in required_roles if clash_color.get(r) == (1 - lo_color)]
        free_roles = [r for r in required_roles if r not in clash_color]

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

        for _ in range(max_variants):
            placed = [(r, _weighted_octave_choice(list(lo_opts[r]), anchor_center, rng))
                      for r in lo_roles]
            placed += [(r, _weighted_octave_choice(list(hi_opts[r]), anchor_center, rng))
                       for r in hi_roles]
            placed += [(r, _weighted_octave_choice(list(free_opts[r]), anchor_center, rng))
                       for r in free_roles]
            pitches = [p for _, p in placed]
            roles = [r for r, _ in placed]
            out.append(Candidate(pitches, roles).dedup_sorted())
    return out


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
                                    max_hand_span: int, max_variants: int = 24) -> list[Candidate]:
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

        for _ in range(max_variants):
            lh_placed = [(r, _weighted_octave_choice(list(lh_opts[r]), anchor_center, rng))
                         for r in lh_roles]
            rh_placed = [(r, _weighted_octave_choice(list(rh_opts[r]), anchor_center, rng))
                         for r in rh_roles]
            lh_pitches = [p for _, p in lh_placed]
            rh_pitches = [p for _, p in rh_placed]
            if lh_pitches and (max(lh_pitches) - min(lh_pitches)) > max_hand_span:
                continue
            if rh_pitches and (max(rh_pitches) - min(rh_pitches)) > max_hand_span:
                continue
            pitches = lh_pitches + rh_pitches
            roles = [r for r, _ in lh_placed] + [r for r, _ in rh_placed]
            out.append(Candidate(pitches, roles,
                                  hands={"lh": lh_pitches, "rh": rh_pitches}).dedup_sorted())
    return out


def hand_split_free_placement(role_pcs: list[tuple], lh_window: tuple, rh_window: tuple,
                               lh_max_voices: int, rh_max_voices: int, max_hand_span: int,
                               anchor_center: int, prev_midi: Optional[list],
                               doubling_roles: list[str], max_doublings: int,
                               rng: random.Random, beam_width: int = 120,
                               max_candidates: int = 400, anchor_shift: int = 0,
                               doubling_pcs: Optional[dict] = None,
                               cluster_min_gap: int = 13) -> list[Candidate]:
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
                                          anchor_shift=anchor_shift)
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
        for variant in apply_doubling_variants(assignment, doubling_roles, role_pc_lookup,
                                                window_lo, window_hi, max_doublings, rng,
                                                anchor_center=anchor_center,
                                                anchor_shift=anchor_shift):
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
                cand = Candidate(pitches, roles,
                                  hands={"lh": lh_pitches, "rh": rh_pitches}).dedup_sorted()
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
        )
        return out[:max_candidates] + direct
    return out[:max_candidates]

