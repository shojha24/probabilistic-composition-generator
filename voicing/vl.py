"""
voicing/vl.py -- voice-leading distance and transformation-aware scoring
bonuses, spec 07 §6.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.optimize import linear_sum_assignment

from .types import ChordEvent, TRIAD_THIRD_FIFTH

# 4-27 set class (dominant 7th / half-diminished 7th), as normalized
# interval-vector-from-root pitch-class sets (root-relative semitones).
DOM7_IC = frozenset({0, 4, 7, 10})
HALF_DIM7_IC = frozenset({0, 3, 6, 10})
SET_CLASS_4_27 = (DOM7_IC, HALF_DIM7_IC)


def vl_distance(a: list[int], b: list[int], unmatched_penalty: float = 2.0) -> float:
    """§6.1: minimum total semitone motion over an optimal matching between
    two pitch multisets, via Hungarian assignment. Unmatched voices in the
    larger set are charged `unmatched_penalty` each rather than being free.
    Symmetric, zero for identical multisets."""
    if not a and not b:
        return 0.0
    n, m = len(a), len(b)
    size = max(n, m)
    # Pad the smaller side with sentinel "no note" columns/rows that cost
    # unmatched_penalty flat, regardless of what they'd be matched to.
    cost = np.zeros((size, size))
    for i in range(size):
        for j in range(size):
            if i < n and j < m:
                cost[i, j] = abs(a[i] - b[j])
            else:
                cost[i, j] = unmatched_penalty
    row_ind, col_ind = linear_sum_assignment(cost)
    return float(cost[row_ind, col_ind].sum())


def normalize_vl(distance: float, n_voices: int) -> float:
    """§6.4: normalize to 4 voices before comparing against E24's 8.37."""
    if n_voices == 0:
        return distance
    return distance * 4.0 / n_voices


# ---------------------------------------------------------------------------
# §6.2 transformation-aware shortcuts
# ---------------------------------------------------------------------------

def _triad_pc_set(root_pc: int, quality: str) -> frozenset:
    third, fifth = TRIAD_THIRD_FIFTH[quality]
    pcs = {root_pc % 12}
    if third is not None:
        pcs.add((root_pc + third) % 12)
    if fifth is not None:
        pcs.add((root_pc + fifth) % 12)
    return frozenset(pcs)


def _is_major_minor_triad_event(chord: ChordEvent) -> bool:
    if chord.triad not in ("major", "minor"):
        return False
    # A "triad" in the PLR sense must carry no extensions.
    return chord.seventh == "N" and chord.ninth == "N" and chord.eleventh == "N" and chord.thirteenth == "N"


def plr_relation(prev: ChordEvent, cur: ChordEvent) -> Optional[str]:
    """Classify the neo-Riemannian relation between two major/minor triads,
    Cohn 1998 §II. Returns "P", "L", "R", "D" (composite L.R / R.L), or None
    if not applicable / not related by one of these. Root-interval-based:
    P: same root, quality flips (major<->minor).
    R: root moves +/-3 (minor 3rd) with a quality flip that keeps the
       "relative" common-tone relationship (e.g. C major -> A minor is R).
    L: root moves +/-4/ +/-8 (major 3rd equivalence) with quality flip
       (leading-tone exchange, e.g. C major -> E minor is L).
    D = L then R composite (major 3rd of root motion with a return to the
        same quality), i.e. root moves a major third, quality unchanged.
    """
    if not (_is_major_minor_triad_event(prev) and _is_major_minor_triad_event(cur)):
        return None
    r1, r2 = prev.root_interval % 12, cur.root_interval % 12
    q1, q2 = prev.triad, cur.triad
    delta = (r2 - r1) % 12

    if delta == 0 and q1 != q2:
        return "P"
    if q1 != q2:
        # R: relative major/minor -- major root -> minor root a minor 3rd
        # below (delta==9) or minor -> major a minor 3rd above (delta==3).
        if q1 == "major" and delta == 9:
            return "R"
        if q1 == "minor" and delta == 3:
            return "R"
        # L: leading-tone exchange -- major root -> minor root a major 3rd
        # below wouldn't apply; canonical: major -> minor a minor 3rd... use
        # common-tone check instead of hardcoding, since it's more robust.
        pcs1 = _triad_pc_set(r1, q1)
        pcs2 = _triad_pc_set(r2, q2)
        if len(pcs1 & pcs2) == 2:
            # two shared tones + quality flip: distinguish L from R by which
            # tones are shared. R shares root+3rd (relative); L shares
            # 3rd+5th (leading tone exchange).
            third1, fifth1 = TRIAD_THIRD_FIFTH[q1]
            shared = pcs1 & pcs2
            root_shared = (r1 % 12) in shared
            third_shared = ((r1 + third1) % 12) in shared if third1 is not None else False
            if root_shared and third_shared:
                return "R"
            return "L"
    if q1 == q2 and delta in (4, 8):
        return "D"  # composite L.R / R.L: major-third root motion, same quality
    return None


def set_class_4_27_member(pcs: frozenset) -> bool:
    """Is this pitch-class set (reduced to its interval pattern) a member of
    set-class 4-27 (dominant 7th / half-diminished 7th)?"""
    if len(pcs) != 4:
        return False
    root = min(pcs)  # not perfectly general, but adequate given our chords
    for anchor in pcs:
        ic = frozenset((p - anchor) % 12 for p in pcs)
        if ic in SET_CLASS_4_27:
            return True
    return False


def _chord_pc_set(chord: ChordEvent) -> frozenset:
    from .types import resolve_degrees
    degs = resolve_degrees(chord)
    return frozenset((chord.root_interval + d.semitone) % 12 for d in degs)


def transformation_bonus(prev: ChordEvent, cur: ChordEvent, plr_bonus: float) -> float:
    """§6.2: cost REDUCTION (a positive number to subtract from cost) for
    PLR-related triads or Cube-Dance-adjacent 4-27 members."""
    rel = plr_relation(prev, cur)
    if rel in ("P", "L", "R"):
        return plr_bonus
    if rel == "D":
        return plr_bonus * 0.5

    pcs1 = _chord_pc_set(prev)
    pcs2 = _chord_pc_set(cur)
    if set_class_4_27_member(pcs1) or set_class_4_27_member(pcs2):
        if len(pcs1 & pcs2) >= 2:
            return plr_bonus
    return 0.0
