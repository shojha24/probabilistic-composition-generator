"""
voicing/spacing.py -- register spacing rules, spec 07 §7.3.
"""
from __future__ import annotations

from typing import Optional


def low_interval_limit_ok(midi: list[int]) -> bool:
    """Hard: no interval smaller than a perfect 5th (7 semitones) below
    MIDI 48, and no interval smaller than a major 3rd (4 semitones) below
    MIDI 55."""
    s = sorted(midi)
    for i in range(len(s) - 1):
        for j in range(i + 1, len(s)):
            interval = s[j] - s[i]
            lower = s[i]
            if lower < 48 and interval < 7:
                return False
            if 48 <= lower < 55 and interval < 4:
                return False
    return True


def semitone_cluster_ok(midi: list[int], min_gap: int = 13) -> bool:
    """Hard: any two active degrees whose pitch classes are a semitone
    apart (mod 12) must be >= min_gap semitones apart in realized pitch,
    unless the voicer explicitly permits clusters (jazz synth's controlled
    exemption is applied by the caller before invoking this with a lower
    min_gap, or by pre-filtering exempted pairs out of `midi`)."""
    s = sorted(midi)
    for i in range(len(s)):
        for j in range(i + 1, len(s)):
            pc_gap = (s[j] - s[i]) % 12
            if pc_gap in (1, 11):
                if s[j] - s[i] < min_gap:
                    return False
    return True


def spacing_hard_ok(midi: list[int], min_gap: int = 13) -> bool:
    return low_interval_limit_ok(midi) and semitone_cluster_ok(midi, min_gap)


def spacing_penalty(midi: list[int], max_close_pairs: int = 2) -> float:
    """§7.3 soft component: penalize gaps > 12 between adjacent inner
    voices; penalize more than `max_close_pairs` intervals of <= 2
    semitones."""
    s = sorted(midi)
    penalty = 0.0
    close_pairs = 0
    for i in range(len(s) - 1):
        gap = s[i + 1] - s[i]
        if gap > 12:
            penalty += 0.3 * (gap - 12)
        if gap <= 2:
            close_pairs += 1
    if close_pairs > max_close_pairs:
        penalty += 0.5 * (close_pairs - max_close_pairs)
    return penalty
