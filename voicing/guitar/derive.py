"""
voicing/guitar/derive.py -- generic programmatic shape derivation, shared
by pop-guitar (spec 02) and jazz-guitar (spec 05).

Spec 02 §3.2/§8 flags guitar shape-library coverage for extended chords
(9th-13th) as an open problem with no literature source ("this spec
therefore states the construction rule rather than citing one", §3.2).
This module is the stated construction rule: rather than hand-transcribing
a shape for every (triad, seventh, ninth, eleventh, thirteenth) combination
that could occur -- most of which have no single canonical, universally-
taught fingering -- it derives a *playable, semitone-correct* shape
algorithmically for any chord-type key, using the same §3.3 omission
priority the rest of the engine already relies on to fit an over-full
degree set onto the available strings.

This is used two ways:
  - `derived` tag: a completeness backstop for (triad, seventh) pairs that
    happen to be under-covered by the hand-authored shapes.
  - `drop2_derived` tag: the agreed derivation for chords carrying a 9th,
    11th, or 13th -- named for the classic "drop" voicing technique this
    construction specializes to guitar's physical constraints (build a
    close-position stack ordered root-up, then redistribute it across the
    available strings, which is exactly what `_try_assignment` below does
    by filling strings upward in `DEGREE_ORDER`). This tag is also what
    spec 05 (jazz-guitar) is expected to use for its own (much larger)
    proportion of derived shapes.

Derivation is NOT a substitute for idiomatic hand-authored shapes -- see
`pop_shapes.py`'s docstring -- it is the fallback that guarantees spec 02
§9.3's completeness requirement ("for all corpus chord events, at least
one shape exists") can never fail because of a hand-authored coverage gap.
"""
from __future__ import annotations

from itertools import product
from typing import Optional

from ..types import ChordEvent, resolve_degrees, DEGREE_ORDER
from ..dct import compute_dct
from .model import Shape, OPEN_STRINGS, STRING_NUMS

# Available strings (low to high) for a movable shape rooted on each
# possible root string -- root string is always first.
ROOT_STRING_LAYOUT = {
    6: (6, 5, 4, 3, 2, 1),
    5: (5, 4, 3, 2, 1),
    4: (4, 3, 2, 1),
}

OMISSION_PRIORITY = ("5th", "root", "9th", "11th", "3rd", "13th", "7th")


def _dummy_chord(chord_type: tuple) -> ChordEvent:
    triad, seventh, ninth, eleventh, thirteenth = chord_type
    return ChordEvent(root_interval=0, triad=triad, bass_interval=0,
                       seventh=seventh, ninth=ninth, eleventh=eleventh, thirteenth=thirteenth)


def _has_extension(chord_type: tuple) -> bool:
    _, _seventh, ninth, eleventh, thirteenth = chord_type
    return ninth != "N" or eleventh != "N" or thirteenth != "N"


def _try_assignment(cur_required: list, role_semi: dict, string_nums: tuple, root_pc: int,
                     cluster_min_gap: int = 10, max_fret_span: int = 6):
    """Place `cur_required` roles onto `string_nums` (root claims the
    first/lowest string if present, remaining roles fill upward in
    `DEGREE_ORDER` priority -- the "close stack redistributed across
    strings" that gives this module's `drop2_derived` tag its name), then
    brute-force the per-string open/+12 fret choice.

    Which *string* each non-root role lands on also matters a lot for
    span (a role's nearest fret differs wildly by string), so this also
    searches over which subset of the non-root strings to use and every
    permutation of roles across them -- cheap at these sizes (<=5 non-root
    roles/strings) and this is a build-time-only cost (shapes are derived
    once, at library-load time, not per chord).

    A shape is a *fixed, transposition-invariant* fret pattern (a barre
    shifts every fretted note by the same amount), so two roles a
    semitone or eleven semitones apart (mod 12) in the *native* fretting
    stay exactly that distance apart in absolute pitch no matter which
    root the shape is eventually realized at. That means the spec 07
    §7.3 tight-cluster rule (min real-semitone gap for a pc-gap of 1)
    can and should be enforced here, at derivation time, not left to be
    discovered later as "no register-legal candidate exists" (this is
    what produced e.g. a min7(9) shape with the 9th and b3rd only 1
    semitone apart -- correct semitone *content*, but an avoidable m2
    clash the naive minimal-span search had no way to know to avoid).

    Among clash-free options, the *lowest native fretted position*
    (`max(combo)`) is preferred over minimal span -- a movable shape's
    only "knob" for reaching a given real chord root is its shift, and
    every native fret already used eats into the shift headroom before
    `max_fret` is exceeded (this is what let a `root_string=4` derivation
    for a dense 6-role chord land at native frets 7-9 with span 3, rather
    than a smaller-but-higher span-2 solution at frets 9-12 that leaves
    almost no shift headroom -- e.g. `G:7(9,11,13)` needs shift=5 from a
    D anchor, which fits under the frets-7-9 solution but not the
    frets-9-12 one, even though the latter has a smaller span). Span is
    only the final tiebreak, and is still hard-capped at `max_fret_span`
    when any such solution exists; only if literally nothing fits under
    the cap is a wider-span fallback returned.

    Returns `(frets, degrees, span, violations)` each aligned to
    `string_nums` (the last two describing the *best available* option,
    which may still have `violations > 0` if no clash-free fingering
    exists at all for this exact role set -- `derive_shape` uses that
    count to decide whether to keep relaxing the omission ladder rather
    than silently emitting a clashing shape), or `(None, None, None,
    None)` if the roles don't fit on the available strings at all."""
    from itertools import combinations, permutations

    ordered = [r for r in DEGREE_ORDER if r in cur_required]
    n = len(string_nums)
    if len(ordered) > n:
        return None, None, None
    has_root = "root" in ordered
    non_root_roles = [r for r in ordered if r != "root"]
    non_root_string_idxs = list(range(1, n)) if has_root else list(range(n))
    root_idx = 0 if has_root else None

    def fret_choices(s_idx, role):
        if role == "root":
            return [0]
        target_pc = (root_pc + role_semi[role]) % 12
        open_pc = OPEN_STRINGS[STRING_NUMS.index(string_nums[s_idx])] % 12
        f0 = (target_pc - open_pc) % 12
        return [f0, f0 + 12]

    def cluster_violations(assignment, combo):
        abs_pitches = [OPEN_STRINGS[STRING_NUMS.index(string_nums[i])] + f
                       for (i, _r), f in zip(assignment, combo)]
        n_violations = 0
        for a, b in combinations(abs_pitches, 2):
            if abs(a - b) % 12 in (1, 11) and abs(a - b) < cluster_min_gap:
                n_violations += 1
        return n_violations

    best_capped = None   # span <= max_fret_span: (violations, max_fret, span, frets, roles_out)
    best_any = None      # fallback with no span cap, same key
    for string_subset in combinations(non_root_string_idxs, len(non_root_roles)):
        for perm in permutations(non_root_roles):
            assignment = list(zip(string_subset, perm))
            if root_idx is not None:
                assignment.append((root_idx, "root"))
            choice_lists = [fret_choices(i, r) for i, r in assignment]
            for combo in product(*choice_lists):
                span = max(combo) - min(combo)
                violations = cluster_violations(assignment, combo)
                key = (violations, max(combo), span)
                frets = ["x"] * n
                roles_out = [None] * n
                for (i, r), f in zip(assignment, combo):
                    frets[i] = f
                    roles_out[i] = r
                entry = (violations, max(combo), span, tuple(frets), tuple(roles_out))
                if best_any is None or key < (best_any[0], best_any[1], best_any[2]):
                    best_any = entry
                if span <= max_fret_span and (
                        best_capped is None or key < (best_capped[0], best_capped[1], best_capped[2])):
                    best_capped = entry
    best = best_capped if best_capped is not None else best_any
    if best is None:
        return None, None, None, None
    violations, _max_fret, span, frets, roles_out = best
    return frets, roles_out, span, violations


def derive_shape(chord_type: tuple, root_string: int, shape_id: str,
                  max_fret_span: int = 6, bass_module_active: bool = True,
                  gen_dir: Optional[str] = None, cluster_min_gap: int = 10,
                  initial_dropped: tuple = ()) -> Optional[tuple]:
    """Derive one movable shape for `chord_type` rooted on `root_string`.
    Returns `(Shape, dropped_roles)`, or `None` if infeasible even after
    the full §3.3 omission ladder (a genuine coverage gap the caller
    should escalate, not silently swallow). `Shape.frets`/`.degrees` are
    always full 6-tuples aligned to `STRING_NUMS` (strings not in this
    root's layout -- e.g. the low E/A strings for a D-rooted shape -- are
    muted).

    `initial_dropped` seeds the omission ladder as already having dropped
    those roles (e.g. `("root", "5th")` for an explicit rootless/no-5th
    "thin" variant) -- this is how `pop_shapes.py` derives a deliberately
    thinner *additional* shape for the same `(chord_type, root_string)`
    alongside the "fullest playable" one this function normally prefers:
    a dense 6-role extended chord's one fullest-role shape can be the
    *only* shape at that anchor even when a thinner, better-registered
    voicing (rootless comping is standard jazz/pop-guitar practice, not
    an exotic fallback) would have been perfectly idiomatic -- but this
    function stops at the first violation-free fit, so without seeding
    it never explores that thinner option on its own."""
    chord = _dummy_chord(chord_type)
    degrees = resolve_degrees(chord)
    dct_role, _ = compute_dct(chord, degrees, gen_dir=gen_dir)
    role_semi = {d.role: d.semitone for d in degrees}
    required = [d.role for d in degrees]

    string_nums = ROOT_STRING_LAYOUT[root_string]
    n_strings = len(string_nums)
    root_pc = OPEN_STRINGS[STRING_NUMS.index(root_string)] % 12

    dropped: list = [r for r in initial_dropped if r != dct_role]
    idx = 0
    clash_fallback = None  # (shape_frets, shape_degs, dropped_snapshot) -- see below
    while True:
        cur_required = [r for r in required if r not in dropped]
        if cur_required and len(cur_required) <= n_strings:
            frets, degs, span, violations = _try_assignment(
                cur_required, role_semi, string_nums, root_pc,
                cluster_min_gap=cluster_min_gap, max_fret_span=max_fret_span)
            if frets is not None and span <= max_fret_span:
                if violations == 0:
                    full_frets = ["x"] * 6
                    full_degs = [None] * 6
                    for i, s_num in enumerate(string_nums):
                        full_idx = STRING_NUMS.index(s_num)
                        full_frets[full_idx] = frets[i]
                        full_degs[full_idx] = degs[i]
                    shape = Shape(
                        id=shape_id, root_string=root_string,
                        frets=tuple(full_frets), degrees=tuple(full_degs),
                        chord_types=(chord_type,), barre=True, open_only=False,
                        native_root_pc=root_pc,
                        tags=("drop2_derived",) if _has_extension(chord_type) else ("derived",),
                    )
                    return shape, dropped
                # A clash exists at this ladder level -- remember the
                # *first* (fewest-drops, most musically complete) one as
                # a last resort, but keep relaxing the ladder in hopes of
                # a clash-free fit further down (dropping the clashing
                # role itself is exactly what the ladder is for).
                if clash_fallback is None:
                    full_frets = ["x"] * 6
                    full_degs = [None] * 6
                    for i, s_num in enumerate(string_nums):
                        full_idx = STRING_NUMS.index(s_num)
                        full_frets[full_idx] = frets[i]
                        full_degs[full_idx] = degs[i]
                    clash_fallback = (tuple(full_frets), tuple(full_degs), list(dropped))
        advanced = False
        while idx < len(OMISSION_PRIORITY):
            role = OMISSION_PRIORITY[idx]
            idx += 1
            if role not in cur_required:
                continue
            if role == "root" and not bass_module_active:
                continue
            if role == dct_role:
                advanced = False
                break
            dropped.append(role)
            advanced = True
            break
        if not advanced:
            if clash_fallback is not None:
                full_frets, full_degs, dropped_snapshot = clash_fallback
                shape = Shape(
                    id=shape_id, root_string=root_string,
                    frets=full_frets, degrees=full_degs,
                    chord_types=(chord_type,), barre=True, open_only=False,
                    native_root_pc=root_pc,
                    tags=("drop2_derived",) if _has_extension(chord_type) else ("derived",),
                )
                return shape, dropped_snapshot
            return None

