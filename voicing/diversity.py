"""
voicing/diversity.py -- diversity accounting, spec 07 §8.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable, Optional


def octave_band(pitch: int, bands: dict) -> str:
    """bands: {"low": (lo,hi), "mid": (lo,hi), "high": (lo,hi)}"""
    for name, (lo, hi) in bands.items():
        if lo <= pitch <= hi:
            return name
    # fall back to nearest band if out of range (shouldn't happen once
    # window clamps are enforced)
    return min(bands.items(), key=lambda kv: min(abs(pitch - kv[1][0]), abs(pitch - kv[1][1])))[0]


def shape_signature(voicer_id: str, midi: list[int], bands: dict, dct_mode: Optional[str],
                     extra: tuple = ()) -> tuple:
    """§8.1. `extra` lets guitar voicers append (root_string, family) etc."""
    if not midi:
        return (voicer_id, (), "low", dct_mode) + extra
    lo = min(midi)
    intervals = tuple(sorted(p - lo for p in midi))
    band = octave_band(lo, bands)
    return (voicer_id, intervals, band, dct_mode) + extra


class DiversityCounter:
    """Persistent counter across a whole generation run (spec 07 §8.3),
    shared across voicers/instruments per §11 ("shared diversity counter").
    """

    def __init__(self):
        self._counts: dict = defaultdict(lambda: defaultdict(int))

    def count_for(self, chord_type: tuple, signature: tuple) -> int:
        return self._counts[chord_type][signature]

    def mean_count(self, chord_type: tuple) -> float:
        d = self._counts[chord_type]
        if not d:
            return 0.0
        return sum(d.values()) / len(d)

    def penalty(self, chord_type: tuple, signature: tuple) -> float:
        c = self.count_for(chord_type, signature)
        m = self.mean_count(chord_type)
        return math.log(1 + c) - math.log(1 + m)

    def record(self, chord_type: tuple, signature: tuple) -> None:
        self._counts[chord_type][signature] += 1

    def reset(self) -> None:
        self._counts = defaultdict(lambda: defaultdict(int))

    # ------------------------------------------------------------------
    # §8.4 coverage report
    # ------------------------------------------------------------------
    def coverage_report(self) -> dict:
        report = {}
        for chord_type, sigs in self._counts.items():
            total = sum(sigs.values())
            n_sig = len(sigs)
            if total == 0:
                entropy = 0.0
            else:
                probs = [c / total for c in sigs.values()]
                h = -sum(p * math.log2(p) for p in probs if p > 0)
                max_h = math.log2(n_sig) if n_sig > 1 else 1.0
                entropy = h / max_h if max_h > 0 else 0.0
            report[str(chord_type)] = {
                "occurrences": total,
                "distinct_signatures": n_sig,
                "normalized_entropy": entropy,
            }
        return report

    def gate_passed(self, min_occurrences: int = 20, min_signatures: int = 8,
                     min_entropy: float = 0.6) -> tuple[bool, list]:
        failures = []
        for chord_type, sigs in self._counts.items():
            total = sum(sigs.values())
            if total < min_occurrences:
                continue
            n_sig = len(sigs)
            probs = [c / total for c in sigs.values()]
            h = -sum(p * math.log2(p) for p in probs if p > 0)
            max_h = math.log2(n_sig) if n_sig > 1 else 1.0
            entropy = h / max_h if max_h > 0 else 0.0
            if n_sig < min_signatures or entropy < min_entropy:
                failures.append((chord_type, n_sig, entropy))
        return (len(failures) == 0, failures)
