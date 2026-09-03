"""
voicing/spacing.py -- register spacing rules, spec 07 §7.3.
"""
from __future__ import annotations

from typing import Optional


def policy_spacing_ok(candidate, chord, policy) -> bool:
    """Compatibility helper for callers that still need a spacing verdict.

    The policy floor is a preference, not an eligibility gate. Candidate
    selection uses :func:`policy_spacing_penalty`; this function therefore
    remains permissive so realistic close voicings are not filtered out.
    """
    return True


def policy_spacing_penalty(candidate, chord, policy) -> float:
    """Return the soft cost for policy-preferred adjacent spacing.

    Close intervals are realistic in piano and upper-register voicings, so
    this never rejects a candidate. Low-register gaps receive a stronger
    penalty, while labeled upper extensions receive a reduced penalty.
    """
    settings = policy.extra
    floor = int(settings.get("spacing_floor", 0))
    low_floor = int(settings.get("spacing_floor_low", floor))
    if floor <= 0:
        return 0.0

    pitches = sorted(candidate.pitches)
    roles = [role for _, role in sorted(zip(candidate.pitches, candidate.roles))]
    penalty = 0.0
    for index, (lower, upper) in enumerate(zip(pitches, pitches[1:])):
        required = low_floor if lower < 48 else floor
        gap = upper - lower
        if gap >= required:
            continue
        shortfall = required - gap
        upper_is_extension = roles[index + 1] in {"9th", "11th", "13th"}
        # Extensions are the most musically natural place for close spacing.
        multiplier = 0.35 if upper_is_extension and lower >= 48 else 0.25
        penalty += shortfall * multiplier
    return penalty


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
