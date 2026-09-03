"""
voicing/dct.py -- disambiguation-critical tone (DCT), spec 07 §5.

compute_dct operates on the chord's active *extension slots* (seventh,
ninth, eleventh, thirteenth) against the corpus chord-type vocabulary
(spec 07 §2.4/§5.1). Triad members (3rd/5th) are never independently
removable in the (triad, seventh, ninth, eleventh, thirteenth) tuple space
-- the tuple has no representation of "major triad minus the 3rd" other
than a wholesale change of `triad` itself -- so only the four extension
slots participate in the differentiator search. This matches every worked
example in §5.1 (all of which turn on seventh/ninth removal) and correctly
yields DCT=None for bare triads, since a triad with no active extension
slot has no candidate differentiators at all.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from .types import ChordEvent, Degree, DEGREE_RANK, EXT_SLOTS, SLOT_TO_ROLE
from .corpus import is_vocabulary_member


@lru_cache(maxsize=4096)
def _compute_dct_for_type(chord_type: tuple, gen_dir: Optional[str]):
    """Cache vocabulary membership checks shared by build-time derivation."""
    triad, seventh, ninth, eleventh, thirteenth = chord_type
    chord = ChordEvent(
        root_interval=0, triad=triad, bass_interval=0,
        seventh=seventh, ninth=ninth, eleventh=eleventh,
        thirteenth=thirteenth,
    )
    kwargs = {} if gen_dir is None else {"gen_dir": gen_dir}
    active_slots = [s for s in EXT_SLOTS if getattr(chord, s) != "N"]
    differentiators = []
    for slot in active_slots:
        candidate = dict(
            triad=chord.triad,
            seventh=chord.seventh, ninth=chord.ninth,
            eleventh=chord.eleventh, thirteenth=chord.thirteenth,
        )
        candidate[slot] = "N"
        ct = (candidate["triad"], candidate["seventh"], candidate["ninth"],
              candidate["eleventh"], candidate["thirteenth"])
        if is_vocabulary_member(ct, **kwargs):
            differentiators.append(SLOT_TO_ROLE[slot])

    if not differentiators:
        return None, ()

    differentiators.sort(key=lambda r: DEGREE_RANK[r])
    return differentiators[-1], tuple(differentiators[:-1])


def compute_dct(chord: ChordEvent, degrees: list[Degree],
                 gen_dir: str = None) -> tuple[Optional[str], list[str]]:
    """Returns (dct_role, secondary_differentiator_roles).

    dct_role is the highest-numbered differentiator's role (e.g. "7th",
    "9th"), or None if the chord has no differentiators (bare triads, or
    an extension whose removal never lands on a vocabulary member).
    secondary_differentiator_roles are the OTHER differentiators found,
    which must satisfy the weaker predicate (b) alone (§5.3).
    """
    dct_role, secondary = _compute_dct_for_type(chord.chord_type(), gen_dir)
    return dct_role, list(secondary)


def dct_pitch_class(chord: ChordEvent, degrees: list[Degree], dct_role: str) -> Optional[int]:
    """Semitone offset (above root) of the DCT role, accounting for §2.5
    merges (a merged degree keeps the higher role's identity, so a
    "9th"-merged degree found under role "9th" is exactly what we want)."""
    for d in degrees:
        if d.role == dct_role:
            return d.semitone
    return None


# ---------------------------------------------------------------------------
# §5.2 exposure predicates
# ---------------------------------------------------------------------------

def predicate_top(p: int, midi: list[int]) -> bool:
    """(a) Topmost."""
    return len(midi) > 0 and p == max(midi)


def predicate_isolated(p: int, midi: list[int]) -> bool:
    """(b) Registrally isolated: nearest neighbour below >= 3 semitones (or
    none), nearest neighbour above >= 2 semitones (or none). "Below" is the
    masking side per §1/§5.2's text ("no chord tone sits 1-2 semitones below
    p masking it")."""
    below = max((x for x in midi if x < p), default=None)
    above = min((x for x in midi if x > p), default=None)
    ok_below = below is None or (p - below) >= 3
    ok_above = above is None or (above - p) >= 2
    return ok_below and ok_above


def predicate_octave(p: int, midi: list[int]) -> bool:
    """(c) Octave-exposed: p is doubled at p +/- 12 and the lower copy of
    the pair also satisfies predicate (b)."""
    for delta in (12, -12):
        other = p + delta
        if other in midi:
            lower = min(p, other)
            if predicate_isolated(lower, midi):
                return True
    return False


PREDICATES = {"top": predicate_top, "isolated": predicate_isolated, "octave": predicate_octave}


def exposure_ok(p: int, midi: list[int]) -> bool:
    """Full §5.2 exposure predicate: at least one of (a)/(b)/(c)."""
    return predicate_top(p, midi) or predicate_isolated(p, midi) or predicate_octave(p, midi)


def exposure_branch(p: int, midi: list[int]) -> Optional[str]:
    """Which branch(es) a pitch satisfies; used for dct_mode diversity
    tracking (§5.5). Returns the first satisfied of top/isolated/octave, or
    None if the exposure predicate fails outright."""
    if predicate_top(p, midi):
        return "top"
    if predicate_octave(p, midi):
        return "octave"
    if predicate_isolated(p, midi):
        return "isolated"
    return None


def secondary_ok(p: int, midi: list[int]) -> bool:
    """§5.3: secondary differentiators need only predicate (b)."""
    return predicate_isolated(p, midi)
