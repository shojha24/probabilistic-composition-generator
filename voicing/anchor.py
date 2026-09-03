"""
voicing/anchor.py -- register anchoring, spec 07 §7.1-7.2.
"""
from __future__ import annotations


def anchor_center(tonic_pc: int, window_lo: int, window_hi: int) -> int:
    """§7.1: the MIDI pitch p with p % 12 == tonic_pc minimizing
    |p - (window_lo+window_hi)/2|. Computed once per song, from tonic_pc,
    never per chord."""
    mid = (window_lo + window_hi) / 2.0
    best_p = None
    best_dist = None
    # search a generous span; anchor candidates repeat every octave
    lo_octave = (window_lo // 12) - 1
    hi_octave = (window_hi // 12) + 1
    for octave in range(lo_octave, hi_octave + 1):
        p = octave * 12 + (tonic_pc % 12)
        dist = abs(p - mid)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_p = p
    return best_p


def drift_penalty(centroid: float, center: int, drift_free: int) -> float:
    """§7.2 soft term: quadratic beyond drift_free semitones of excursion."""
    excursion = abs(centroid - center) - drift_free
    return max(0.0, excursion) ** 2


def window_ok(midi: list[int], window_lo: int, window_hi: int) -> bool:
    """§7.2 hard clamp."""
    return all(window_lo <= p <= window_hi for p in midi)


def drift_ok(centroid: float, center: int, drift_tol: int) -> bool:
    """§7.2 hard, secondary clamp."""
    return abs(centroid - center) <= drift_tol


def centroid_of(midi: list[int]) -> float:
    if not midi:
        return 0.0
    return sum(midi) / len(midi)
