"""
voicing/corpus.py -- corpus chord-type vocabulary, used by:
  - compute_dct (spec 07 §5.1): "member of the corpus chord-type vocabulary"
  - rootless-ambiguity checks (spec 04 §3.1 condition 3, inherited by 05/06)

Vocabulary is pooled across both gen/*-labels/ directories (spec 07 §2.4
measures "9600 chord events" over gen/*-labels/ as a whole, not per genre).
"""
from __future__ import annotations

import glob
import json
import os
from functools import lru_cache

from .types import ChordEvent, resolve_degrees

_DEFAULT_GEN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gen")


@lru_cache(maxsize=1)
def _load_all_events(gen_dir: str = _DEFAULT_GEN_DIR) -> tuple:
    events = []
    for path in sorted(glob.glob(os.path.join(gen_dir, "*-labels", "*.json"))):
        with open(path) as f:
            d = json.load(f)
        for c in d.get("chords", []):
            events.append(ChordEvent.from_dict(c))
    return tuple(events)


@lru_cache(maxsize=1)
def chord_type_vocabulary(gen_dir: str = _DEFAULT_GEN_DIR) -> frozenset:
    """The set of (triad, seventh, ninth, eleventh, thirteenth) tuples
    observed anywhere in the corpus (root-invariant, spec 07 §2.4/§8.2)."""
    return frozenset(
        e.chord_type()
        for e in _load_all_events(gen_dir)
        if not e.is_no_chord
    )


@lru_cache(maxsize=1)
def chord_type_counts(gen_dir: str = _DEFAULT_GEN_DIR) -> dict:
    counts: dict = {}
    for e in _load_all_events(gen_dir):
        if e.is_no_chord:
            continue
        ct = e.chord_type()
        counts[ct] = counts.get(ct, 0) + 1
    return counts


def is_vocabulary_member(chord_type: tuple, gen_dir: str = _DEFAULT_GEN_DIR) -> bool:
    return chord_type in chord_type_vocabulary(gen_dir)


def pitch_class_set_for_type(root_pc: int, chord_type: tuple) -> frozenset:
    """Realized pitch-class set for a (triad, 7th, 9th, 11th, 13th) tuple at
    a given root pitch class. Used by the rootless-ambiguity check, which
    asks whether a *rootless* pitch-class set matches some OTHER root's
    labelled chord type exactly."""
    triad, seventh, ninth, eleventh, thirteenth = chord_type
    ce = ChordEvent(root_interval=0, triad=triad, bass_interval=0,
                    seventh=seventh, ninth=ninth, eleventh=eleventh,
                    thirteenth=thirteenth)
    degs = resolve_degrees(ce)
    return frozenset((root_pc + d.semitone) % 12 for d in degs)


@lru_cache(maxsize=1)
def _vocabulary_pc_sets_by_root(gen_dir: str = _DEFAULT_GEN_DIR) -> tuple:
    """Precompute (root_pc, chord_type, full_pc_set) for every corpus chord
    type at every root 0-11, for fast rootless-collision lookup."""
    out = []
    for ct in chord_type_vocabulary(gen_dir):
        for root_pc in range(12):
            pcs = pitch_class_set_for_type(root_pc, ct)
            out.append((root_pc, ct, pcs))
    return tuple(out)


def rootless_collision(rootless_pc_set: frozenset, exclude_root_pc: int,
                        exclude_chord_type: tuple,
                        gen_dir: str = _DEFAULT_GEN_DIR) -> tuple | None:
    """Spec 04 §3.1 condition 3 / §6.2 / §9.3: does the rootless pitch-class
    set exactly match some *other* labelled (root, chord_type) pair in the
    corpus vocabulary? Returns the colliding (root_pc, chord_type) if so,
    else None. The chord's own (root, type) is excluded from the search."""
    for root_pc, ct, pcs in _vocabulary_pc_sets_by_root(gen_dir):
        if root_pc == exclude_root_pc and ct == exclude_chord_type:
            continue
        if pcs == rootless_pc_set:
            return (root_pc, ct)
    return None
