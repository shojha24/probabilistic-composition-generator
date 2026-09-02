"""
chord_gen.py -- Stage 1-3 chord progression generator (Chord Generator Spec §2-§4, §6)
=========================================================================================
Generates symbolic chord-event sequences -- (Root, Triad, Bass, seventh, ninth,
eleventh, thirteenth) per time step, §1.1's native corpus schema -- from the
count tables written by extract_distributions.py:

    {dist-dir}/{genre}/stage1_backbone.json
    {dist-dir}/{genre}/stage2_bass.json
    {dist-dir}/{genre}/stage3_extensions.json
    {dist-dir}/{genre}/meta.json

This implements exactly Stages 1-3 of the §6 pseudocode:

    Stage 1  backoff_sample_stage1   -- order-2 (Root,Triad) HMM, §2
    Stage 2  backoff_sample_stage2   -- 1st-order Bass AR on Root, §3
    Stage 3  select_extension_trie
             walk_trie_with_backoff  -- 4-slot trie walk, §4

Stage 4 (the probabilistic voicer, §5) and JFugue/MIDI rendering are explicitly
out of scope for this file -- see the module docstring in the deprecated
generator.py for how that stage used to be bolted on. Nothing in this file
performs voicing.

Interpolation, not hard-switching (§2.2's "Recommended refinement", carried
through identically to §3.2 and §4.3): every backoff level is combined via
count-derived lambda weights against a single discount constant D (§9 --
the spec calls out D as one constant shared across "all stages/levels", so
there is exactly one --discount flag, not one per level). The single
exception is §4.2's trie *selection* (which trie to walk: per-(Root,Triad),
per-Triad, or genre-wide) -- the spec describes that specific choice with a
hard "if count >= min_count_X" cutover rather than interpolation, so that
part alone uses --min-count-a / --min-count-b thresholds instead of D.

Add-k smoothing (§9 `k`) is applied uniformly to every observed-context
distribution before it participates in interpolation, by re-deriving
probabilities from raw counts as (count + k) / (total + k*support) rather
than trusting the extractor's precomputed (unsmoothed, k=0) "probabilities"
field. This also sidesteps any drift between the two representations.

The generator remains natural. Quota-aware corpus generation lives in
`target_corpus_gen.py`, which reuses the primitives in this module without
changing the default sampling path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Pitch-class tables (needed only to resolve tonic-relative intervals to
# absolute note names at render time, §10.2 -- no voicing happens here)
# ─────────────────────────────────────────────────────────────────────────────
PC_TO_NOTE = {
    0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F",
    6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B",
}
NOTE_TO_PC = {
    "C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4, "F": 5, "E#": 5, "F#": 6, "Gb": 6, "G": 7,
    "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}

# Triad/7th -> Harte quality shorthand, for producing a readable chord label
# alongside the structured fields.
#
# This used to be a hand-copied static table (inherited unmodified from the
# deprecated generator.py) and it was wrong in two ways that only show up
# once you check it against what extract_distributions.py *actually*
# produces rather than against the spec's stated 7-way triad vocabulary:
#
#   1. The extractor's own QUALITY_TO_TRIAD includes an 8th triad, "1"
#      (bare single-note chords, e.g. Harte "C:1"), that the spec's §1.1
#      vocabulary list omits. A hand-copied table naturally omitted it too,
#      so a genuine "1" chord fell through to a "maj" default and was
#      mislabeled as a full major triad.
#   2. The extractor deliberately reuses seventh="bb7" for more than
#      diminished-7th: QUALITY_TO_7TH maps "maj6"/"min6"/"6" to "bb7" as
#      well (it's unambiguous once paired with the triad). The hand-copied
#      table only handled the dim7 case and collapsed (major,"bb7") /
#      (minor,"bb7") to plain "maj"/"min", silently dropping the 6th.
#
# Rather than re-patching a second hand-maintained table (which can drift
# again the next time extract_distributions.py's quality tables change),
# this is derived programmatically from the extractor's own
# QUALITY_TO_TRIAD / QUALITY_TO_7TH at import time -- see
# _build_quality_shorthand() below. That keeps this file deferring to
# whatever the extraction script actually emits, not to the spec's prose
# description of it.
try:
    from extract_distributions import QUALITY_TO_TRIAD as _EXT_QUALITY_TO_TRIAD
    from extract_distributions import QUALITY_TO_7TH as _EXT_QUALITY_TO_7TH
except ImportError:
    _EXT_QUALITY_TO_TRIAD = None
    _EXT_QUALITY_TO_7TH = None

# Quality tokens that layer a ninth/eleventh/thirteenth on top of a base
# seventh (parse_harte sets those slots specifically for these tokens).
# Excluded when deriving the (triad,seventh)->quality reverse map: they'd
# be redundant with -- and an arbitrary tie-break away from -- the plain
# base token that already covers the same (triad,seventh) pair (e.g. "9"
# and "7" both resolve to (major,"b7"); "7" is the right label for the
# *base* chord since ninth/eleventh/thirteenth are reconstructed
# separately, in parens, from their own fields below).
_EXTENSION_IMPLYING_QUALITIES = {
    "9", "min9", "maj9", "11", "min11", "maj11", "13", "min13", "maj13",
}


def _build_quality_shorthand() -> Dict[Tuple[str, str], str]:
    if not _EXT_QUALITY_TO_TRIAD:
        # extract_distributions.py isn't importable (e.g. not on the path).
        # Fall back to a minimal, defensively-complete table covering all
        # 8 triads the extractor is known to emit (including "1"), rather
        # than silently mislabeling anything as "maj".
        return {
            ("major", "N"): "maj", ("major", "b7"): "7", ("major", "7"): "maj7", ("major", "bb7"): "maj6",
            ("minor", "N"): "min", ("minor", "b7"): "min7", ("minor", "7"): "minmaj7", ("minor", "bb7"): "min6",
            ("diminished", "N"): "dim", ("diminished", "b7"): "hdim7", ("diminished", "bb7"): "dim7",
            ("augmented", "N"): "aug", ("augmented", "b7"): "aug7",
            ("sus4", "N"): "sus4", ("sus4", "b7"): "7sus4",
            ("sus2", "N"): "sus2",
            ("5", "N"): "5",
            ("1", "N"): "1",
        }
    reverse: Dict[Tuple[str, str], str] = {}
    for quality, triad in _EXT_QUALITY_TO_TRIAD.items():
        if quality == "" or quality in _EXTENSION_IMPLYING_QUALITIES:
            continue  # skip the bare "" alias and extension-implying tokens
        seventh = _EXT_QUALITY_TO_7TH.get(quality, "N")
        key = (triad, seventh)
        if key not in reverse:  # first (shortest-preference-ordered) match wins
            reverse[key] = quality
    return reverse


QUALITY_SHORTHAND: Dict[Tuple[str, str], str] = _build_quality_shorthand()
QUALITY_SHORTHAND[("major", "bb7")] = "maj6"
QUALITY_SHORTHAND[("minor", "bb7")] = "min6"

# Bare-triad fallback for (triad,seventh) combinations that are structurally
# valid (Stage 1/3 will happily sample them, since they're just opaque
# strings from the count tables) but weren't attested in the extractor's own
# quality tables -- e.g. a bb7 landing on a "5" or "sus2" triad via a stray
# "(6)" paren token. Kept intentionally per-triad-specific (never a blanket
# "maj" default) so an unusual combination degrades to *something honest*
# about its triad rather than silently mislabeling it.
_TRIAD_FALLBACK = {
    "major": "maj", "minor": "min", "diminished": "dim", "augmented": "aug",
    "sus4": "sus4", "sus2": "sus2", "5": "5", "1": "1",
}

EXT_SLOTS = ("seventh", "ninth", "eleventh", "thirteenth")
VALID_GENRES = ("pop_rock", "jazz")  # §10.1
DURATION_BEATS = {
    "ww": 8.0, "w.": 6.0, "w": 4.0, "h.": 3.0,
    "h": 2.0, "q.": 1.5, "q": 1.0,
}
DEFAULT_DURATION_WEIGHTS = {
    "ww": 0.08, "w.": 0.07, "w": 0.50, "h.": 0.09,
    "h": 0.13, "q.": 0.05, "q": 0.08,
}
GENRE_DURATION_WEIGHTS = {
    "pop_rock": {
        "ww": 0.02, "w.": 0.03, "w": 0.60, "h.": 0.05,
        "h": 0.20, "q.": 0.05, "q": 0.05,
    },
    "jazz": {
        "ww": 0.01, "w.": 0.01, "w": 0.30, "h.": 0.08,
        "h": 0.40, "q.": 0.05, "q": 0.15,
    },
}
GENRE_LABEL_DIRS: Dict[str, str] = {
    "jazz": "jazz-labels",
    "pop_rock": "pop-rock-labels",
}

# Stage 1 Level-1 temperature defaults, §10.3.
GENRES_TO_TEMPS: Dict[str, float] = {"pop_rock": 2.2, "jazz": 2.0}

# Self-transition discount default table, §10.3. delta=1.0 is a no-op;
# lower values actively suppress exact (Root,Triad) repeats (§8's
# "over-repetition of the same 'safe' progression" failure mode).
GENRES_TO_SELF_TRANSITION_DISCOUNT: Dict[str, float] = {"pop_rock": 0.2, "jazz": 0.2}

_DEFAULT_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "distributions")


# ─────────────────────────────────────────────────────────────────────────────
# Generation hyperparameters (§9)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GenParams:
    discount: float = 25           # D -- shared interpolation discount, all stages/levels (§9)
    add_k: float = 0              # k -- within-distribution Laplace smoothing (§9)
    min_count_a: int = 200        # §4.2 Level A trie-selection threshold
    min_count_b: int = 500        # §4.2 Level B trie-selection threshold
    epsilon_floor: float = 0.02   # optional ε floor of Level-0 mass mixed into Stage 1 (§9, §8)
    temperature: float = 2.0       # τ1 -- Stage 1 Level-1 softmax temperature (§10.3)
    self_transition_discount: float = 0.2  # δ -- Stage 1 exact-repeat discount (§10.3)


# ─────────────────────────────────────────────────────────────────────────────
# Small probability-table utilities
# ─────────────────────────────────────────────────────────────────────────────
def normalize_with_addk(counts: Dict[str, int], k: float) -> Dict[str, float]:
    """(count + k) / (total + k*|support|), over exactly the observed support.

    k=0 reduces to a plain count-proportional normalization. This is used
    everywhere in place of trusting a precomputed "probabilities" field, so
    that --add-k actually has an effect end to end (§9 `k`).
    """
    if not counts:
        return {}
    total = sum(counts.values())
    support = len(counts)
    denom = total + k * support
    if denom <= 0:
        return {}
    return {key: (c + k) / denom for key, c in counts.items()}


def lam(n: float, d: float) -> float:
    """Witten-Bell-style confidence weight, §2.2: λ = N / (N + D)."""
    if n <= 0:
        return 0.0
    return n / (n + d)


def apply_temperature(probs: Dict[str, float], tau: float) -> Dict[str, float]:
    """Softmax tempering, §10.3. tau=1.0 is a no-op."""
    if not probs or tau == 1.0:
        return probs
    scaled = {k: v ** (1.0 / tau) for k, v in probs.items()}
    total = sum(scaled.values())
    if total <= 0:
        return probs
    return {k: v / total for k, v in scaled.items()}


def weighted_choice(dist: Dict[str, float], rng: random.Random) -> str:
    keys = list(dist.keys())
    weights = list(dist.values())
    return rng.choices(keys, weights=weights, k=1)[0]


def mix_with_floor(dist: Dict[str, float], floor: Dict[str, float], epsilon: float) -> Dict[str, float]:
    """Optional ε-floor mixing (§8, §9): keep some guaranteed entropy even
    when a higher backoff level has support, by blending in `epsilon` of
    the Level-0 marginal regardless of context density."""
    if epsilon <= 0.0 or not floor:
        return dist
    mixed = {k: (1.0 - epsilon) * v for k, v in dist.items()}
    for k, v in floor.items():
        mixed[k] = mixed.get(k, 0.0) + epsilon * v
    total = sum(mixed.values())
    if total <= 0:
        return dist
    return {k: v / total for k, v in mixed.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 -- Harmonic backbone, order-2 (Root,Triad) HMM with 3-level
# interpolated backoff (§2)
# ─────────────────────────────────────────────────────────────────────────────
def stage1_distribution(
    ctx1: Optional[str],
    ctx2: Optional[str],
    stage1: dict,
    params: GenParams,
) -> Dict[str, float]:
    """P(Root_t,Triad_t) interpolated across level2 (trigram) / level1
    (bigram, tempered) / level0 (unigram floor), §2.2.

    ctx1 = previous state ("root_triad"), or None if there's no history yet.
    ctx2 = "state_{t-2}|state_{t-1}", or None if there's < 2 chords of history.
    """
    level0_counts = stage1["level0"]["counts"]
    l0 = normalize_with_addk(level0_counts, params.add_k)
    if not l0:
        return {}

    l1_counts = stage1["level1"]["counts"].get(ctx1, {}) if ctx1 is not None else {}
    n1 = sum(l1_counts.values())
    l1 = apply_temperature(normalize_with_addk(l1_counts, params.add_k), params.temperature)

    l2_counts = stage1["level2"]["counts"].get(ctx2, {}) if ctx2 is not None else {}
    n2 = sum(l2_counts.values())
    l2 = normalize_with_addk(l2_counts, params.add_k)

    lambda1 = lam(n1, params.discount)
    lambda2 = lam(n2, params.discount)

    result = {}
    for state, p0 in l0.items():
        p1 = lambda1 * l1.get(state, 0.0) + (1.0 - lambda1) * p0
        pf = lambda2 * l2.get(state, 0.0) + (1.0 - lambda2) * p1
        result[state] = pf

    total = sum(result.values())
    if total <= 0:
        return l0
    result = {k: v / total for k, v in result.items()}

    result = mix_with_floor(result, l0, params.epsilon_floor)

    if ctx1 is not None and params.self_transition_discount < 1.0:
        result = _apply_self_transition_discount(result, ctx1, params.self_transition_discount)

    return result


def _apply_self_transition_discount(dist: Dict[str, float], self_state: str, delta: float) -> Dict[str, float]:
    """Discount P(exact repeat of the previous (Root,Triad)) by delta and
    redistribute the removed mass proportionally over everything else,
    §10.3. delta=1.0 is a no-op (never reaches here); delta=0.0 forbids
    immediate repeats outright."""
    self_prob = dist.get(self_state)
    if not self_prob:
        return dist
    others = {k: v for k, v in dist.items() if k != self_state}
    other_total = sum(others.values())
    if other_total <= 0:
        return dist
    removed = self_prob * (1.0 - delta)
    out = dict(dist)
    out[self_state] = self_prob * delta
    for k, v in others.items():
        out[k] = v + removed * (v / other_total)
    return out


def _attested_transition_counts(
    ctx1: Optional[str],
    ctx2: Optional[str],
    stage1: dict,
) -> Dict[str, int]:
    """Return successor counts observed for the current local context."""
    counts: Dict[str, int] = {}
    if ctx1 is not None:
        for state, count in stage1["level1"]["counts"].get(ctx1, {}).items():
            counts[state] = max(counts.get(state, 0), count)
    if ctx2 is not None:
        for state, count in stage1["level2"]["counts"].get(ctx2, {}).items():
            counts[state] = max(counts.get(state, 0), count)
    return counts


def _attested_successor_distribution(
    dist: Dict[str, float],
    ctx1: Optional[str],
    ctx2: Optional[str],
    stage1: dict,
) -> Dict[str, float]:
    """Restrict a normal distribution to locally observed successor edges."""
    counts = _attested_transition_counts(ctx1, ctx2, stage1)
    if not counts:
        return {}
    restricted = {
        state: probability * counts[state]
        for state, probability in dist.items()
        if counts.get(state, 0) > 0
    }
    total = sum(restricted.values())
    if total <= 0:
        return {}
    return {state: probability / total for state, probability in restricted.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 -- Bass AR, 2-level interpolated backoff (§3)
# ─────────────────────────────────────────────────────────────────────────────
def stage2_distribution(
    root_key: str,
    bass_prev: Optional[str],
    stage2: dict,
    params: GenParams,
) -> Dict[str, float]:
    """P(Bass_t | Root_t) interpolated with P(Bass_t | Root_t, Bass_{t-1}), §3.2."""
    l0_counts = stage2["level0"]["counts"].get(root_key, {})
    l0 = normalize_with_addk(l0_counts, params.add_k)
    if not l0:
        return {}

    ctx1b = f"{root_key}|{bass_prev}" if bass_prev is not None else None
    l1_counts = stage2["level1b"]["counts"].get(ctx1b, {}) if ctx1b is not None else {}
    n1 = sum(l1_counts.values())
    l1 = normalize_with_addk(l1_counts, params.add_k)

    lambda1 = lam(n1, params.discount)
    result = {bass: lambda1 * l1.get(bass, 0.0) + (1.0 - lambda1) * p0 for bass, p0 in l0.items()}
    total = sum(result.values())
    if total <= 0:
        return l0
    return {k: v / total for k, v in result.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 -- 4-slot extension trie walk (§4)
# ─────────────────────────────────────────────────────────────────────────────
def _trie_root_total(trie: dict, key: str) -> int:
    node = trie.get(key, {}).get("ROOT")
    if not node:
        return 0
    return sum(node.get("marginal", {}).get("counts", {}).values())


def _extension_trie_sources(
    state: str,
    triad: str,
    stage3: dict,
    params: GenParams,
) -> List[Tuple[dict, str, float]]:
    """Return valid trie sources and their selection weights.

    A reliable state-specific trie remains authoritative.  When only a
    low-count triad trie is available, blend complete trie walks with the
    genre trie rather than mixing children from two tries; this preserves
    attested root-to-leaf paths.
    """
    trie_a, trie_b, trie_c = stage3["trie_A"], stage3["trie_B"], stage3["trie_C"]
    if state in trie_a and _trie_root_total(trie_a, state) >= params.min_count_a:
        return [(trie_a[state], "A", 1.0)]

    triad_trie = trie_b.get(triad)
    triad_count = _trie_root_total(trie_b, triad)
    genre_trie = trie_c.get("ALL", {})
    if triad_trie and triad_count >= params.min_count_b:
        return [(triad_trie, "B", 1.0)]
    if triad_trie and triad_count > 0:
        if not genre_trie:
            return [(triad_trie, "B", 1.0)]
        confidence = triad_count / (triad_count + max(float(params.discount), 1.0))
        return [
            (triad_trie, "B", confidence),
            (genre_trie, "C", 1.0 - confidence),
        ]
    if genre_trie:
        return [(genre_trie, "C", 1.0)]
    return []


def select_extension_trie(
    state: str,
    triad: str,
    stage3: dict,
    params: GenParams,
    rng: Optional[random.Random] = None,
) -> Tuple[dict, str]:
    """§4.2 trie-selection backoff: A (per Root,Triad) -> B (per Triad) ->
    C (genre-wide). The four-argument form uses the spec's hard cutover;
    generation passes an RNG to smooth low-count triad selection."""

    # Keep the four-argument form deterministic for callers that use this
    # helper directly. Generation passes its RNG to enable support-aware
    # smoothing for low-count triads.
    if rng is None:
        trie_a, trie_b, trie_c = stage3["trie_A"], stage3["trie_B"], stage3["trie_C"]

        if state in trie_a and _trie_root_total(trie_a, state) >= params.min_count_a:
            return trie_a[state], "A"
        if triad in trie_b and _trie_root_total(trie_b, triad) >= params.min_count_b:
            return trie_b[triad], "B"
        return trie_c.get("ALL", {}), "C"

    sources = _extension_trie_sources(state, triad, stage3, params)
    if not sources:
        return {}, "C"
    if len(sources) == 1:
        return sources[0][0], sources[0][1]
    source_dist = {level: weight for _trie, level, weight in sources}
    chosen_level = weighted_choice(source_dist, rng)
    for trie, level, _weight in sources:
        if level == chosen_level:
            return trie, level
    return sources[0][0], sources[0][1]


def _node_child_distribution(
    node: dict,
    bass_str: str,
    ext_prev_str: str,
    params: GenParams,
) -> Dict[str, float]:
    """§4.3 node-level backoff: level2 (bass+history) -> level1 (bass) ->
    level0 (trie marginal, guaranteed floor by construction of §4.2)."""
    marginal_counts = node.get("marginal", {}).get("counts", {})
    l0 = normalize_with_addk(marginal_counts, params.add_k)
    if not l0:
        return {}

    bass_counts = node.get("by_bass", {}).get("counts", {}).get(bass_str, {})
    n1 = sum(bass_counts.values())
    l1 = normalize_with_addk(bass_counts, params.add_k)

    composite = f"{bass_str}||{ext_prev_str}"
    hist_counts = node.get("by_bass_history", {}).get("counts", {}).get(composite, {})
    n2 = sum(hist_counts.values())
    l2 = normalize_with_addk(hist_counts, params.add_k)

    lambda1 = lam(n1, params.discount)
    lambda2 = lam(n2, params.discount)

    result = {}
    for child, p0 in l0.items():
        p1 = lambda1 * l1.get(child, 0.0) + (1.0 - lambda1) * p0
        pf = lambda2 * l2.get(child, 0.0) + (1.0 - lambda2) * p1
        result[child] = pf
    total = sum(result.values())
    if total <= 0:
        return l0
    return {k: v / total for k, v in result.items()}


def walk_extension_trie(
    trie_node_table: dict,
    bass_str: str,
    ext_prev_str: str,
    params: GenParams,
    rng: random.Random,
    maximum_active: Optional[int] = None,
) -> Tuple[str, str, str, str]:
    """Descend the 4-slot trie (seventh -> ninth -> eleventh -> thirteenth,
    §4.1 slot order), one interpolated backoff draw per slot (§4.3). Every
    step only samples among the trie's own children, so the resulting
    4-tuple is guaranteed to be a combination attested in training (§4.2)."""
    if maximum_active is not None and not 0 <= maximum_active <= len(EXT_SLOTS):
        raise ValueError(
            f"maximum_active must be between 0 and {len(EXT_SLOTS)}"
        )

    chosen: List[str] = []
    active_count = 0
    for depth in range(len(EXT_SLOTS)):
        prefix_str = "ROOT" if depth == 0 else "|".join(chosen)
        node = trie_node_table.get(prefix_str)
        if node is None:
            # Should not happen given a well-formed trie (every attested
            # prefix has a node), but degrade gracefully rather than crash.
            chosen.append("N")
            continue
        dist = _node_child_distribution(node, bass_str, ext_prev_str, params)
        if maximum_active is not None:
            dist = {
                child: probability
                for child, probability in dist.items()
                if _trie_reaches_density(
                    trie_node_table,
                    _trie_child_prefix(prefix_str, child),
                    depth + 1,
                    active_count + (child != "N"),
                    0,
                    maximum_active,
                )
            }
        if not dist:
            chosen.append("N")
            continue
        child = weighted_choice(dist, rng)
        chosen.append(child)
        active_count += child != "N"
    return tuple(chosen)  # (seventh, ninth, eleventh, thirteenth)


def _trie_child_prefix(prefix: str, child: str) -> str:
    return child if prefix == "ROOT" else f"{prefix}|{child}"


def _trie_reaches_density(
    trie_node_table: dict,
    prefix: str,
    depth: int,
    active_count: int,
    minimum_active: int,
    maximum_active: Optional[int] = None,
) -> bool:
    """Whether a prefix has an attested complete path of a given density."""
    if maximum_active is not None and active_count > maximum_active:
        return False
    if depth == len(EXT_SLOTS):
        return (
            active_count >= minimum_active
            and (
                maximum_active is None
                or active_count <= maximum_active
            )
        )
    node = trie_node_table.get(prefix)
    if node is None:
        return False
    children = node.get("marginal", {}).get("counts", {})
    return any(
        _trie_reaches_density(
            trie_node_table,
            _trie_child_prefix(prefix, child),
            depth + 1,
            active_count + (child != "N"),
            minimum_active,
            maximum_active,
        )
        for child, count in children.items()
        if count > 0
    )


# ─────────────────────────────────────────────────────────────────────────────
# Harte reconstruction (readability only -- no voicing/rendering happens here)
# ─────────────────────────────────────────────────────────────────────────────
def reconstruct_harte(root_name: str, triad: str, bass_name: str,
                       seventh: str, ninth: str, eleventh: str, thirteenth: str) -> str:
    if triad is None:
        return "N"
    base = QUALITY_SHORTHAND.get((triad, seventh))
    if base is None:
        # No attested quality token covers this exact (triad,seventh) pair
        # (e.g. a bb7 landing on an unusual triad). Never fall back to a
        # generic "maj"/"min" here -- that would silently discard both the
        # true triad *and* the seventh. Keep the real triad token and
        # surface the seventh explicitly instead.
        base = _TRIAD_FALLBACK.get(triad, triad)
        if seventh != "N":
            base = f"{base}({seventh})"
    exts = [t for t in (ninth, eleventh, thirteenth) if t != "N"]
    result = root_name + ":" + base
    if exts:
        result += "(" + ",".join(exts) + ")"
    if bass_name and bass_name != root_name:
        result += "/" + bass_name
    return result


def is_no_chord_eligible(event: dict) -> bool:
    """Return whether an event may be safely replaced without affecting quotas."""
    return (
        not event.get("is_no_chord", event.get("harte") == "N")
        and event.get("triad") in {"major", "minor"}
        and event.get("bass_interval") == 0
        and all(event.get(slot, "N") == "N" for slot in EXT_SLOTS)
    )


def _no_chord_rng(seed: int | None) -> random.Random:
    material = f"no_chord:{seed!r}".encode("utf-8")
    return random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))


def apply_no_chord_filter(
    songs: List[List[dict]],
    *,
    rate: float = 0.01,
    mode: str = "exact",
    seed: int | None = None,
    eligible_predicate=None,
) -> List[List[dict]]:
    """Replace eligible timed events with canonical Harte ``N`` records."""
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not 0 <= rate <= 1:
        raise ValueError("no-chord rate must be between 0 and 1")
    if mode not in {"exact", "probabilistic"}:
        raise ValueError("no-chord mode must be 'exact' or 'probabilistic'")
    predicate = eligible_predicate or is_no_chord_eligible
    result = [[dict(event) for event in song] for song in songs]
    candidates = [
        (song_index, event_index)
        for song_index, song in enumerate(result)
        for event_index, event in enumerate(song)
        if predicate(event)
    ]
    total = sum(len(song) for song in result)
    target = int(rate * total + 0.5)
    if mode == "exact":
        if target > len(candidates):
            raise ValueError(
                f"requested {target} no-chord events at rate {rate}, "
                f"but only {len(candidates)} eligible events are available"
            )
        selected = set(_no_chord_rng(seed).sample(candidates, target))
    else:
        if rate == 0:
            selected = set()
        elif not candidates:
            raise ValueError("no-chord rate requires at least one eligible event")
        else:
            probability = rate * total / len(candidates)
            if probability > 1:
                raise ValueError(
                    f"no-chord probability {probability} exceeds 1; "
                    f"rate {rate} requires more eligible events"
                )
            rng = _no_chord_rng(seed)
            selected = {candidate for candidate in candidates if rng.random() < probability}
    for song_index, event_index in selected:
        event = result[song_index][event_index]
        event.update({
            "is_no_chord": True,
            "root_interval": None,
            "triad": None,
            "bass_interval": None,
            "seventh": "N",
            "ninth": "N",
            "eleventh": "N",
            "thirteenth": "N",
            "root": None,
            "bass": None,
            "harte": "N",
        })
    for song in result:
        for event in song:
            event.setdefault("is_no_chord", False)
    return result


def no_chord_summary(events: List[dict]) -> dict:
    """Return descriptive no-chord counts for a completed timed event list."""
    if events and isinstance(events[0], list):
        events = [event for song in events for event in song]
    total_duration = sum(float(event.get("duration_seconds", 0.0)) for event in events)
    no_chord_events = [
        event for event in events
        if event.get("is_no_chord", event.get("harte") == "N")
    ]
    no_chord_duration = sum(
        float(event.get("duration_seconds", 0.0)) for event in no_chord_events
    )
    return {
        "enabled": bool(no_chord_events),
        "event_count": len(no_chord_events),
        "event_rate": len(no_chord_events) / len(events) if events else 0.0,
        "duration_seconds": no_chord_duration,
        "duration_rate": no_chord_duration / total_duration if total_duration else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GenState:
    """Mirrors §6's `state` object: everything the next time step's
    backoff sampling needs to condition on."""
    prev_states: List[str] = field(default_factory=list)  # last <=2 (Root,Triad) states
    bass_prev: Optional[str] = None
    ext_prev_str: str = "START"  # sentinel matching extract_distributions.py's run-start marker


class ChordGenerator:
    """Runs Stages 1-3 of §6's pseudocode against one genre's trained
    tables. Stage 4 (voicing) is intentionally not implemented here."""

    def __init__(
        self,
        genre: str = "pop_rock",
        tonic_pc: int | str = 0,
        num_chords: int = 16,
        temperature: Optional[float] = None,
        self_transition_discount: Optional[float] = None,
        seed: Optional[int] = None,
        dist_dir: str = _DEFAULT_DIST_DIR,
        bpm: int = 120,
        params: Optional[GenParams] = None,
        duration_weights: Optional[Dict[str, float]] = None,
    ):
        if genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {genre!r}")

        if isinstance(tonic_pc, str):
            note = tonic_pc[0].upper() + tonic_pc[1:]
            resolved = NOTE_TO_PC.get(note)
            if resolved is None:
                raise ValueError(f"Unrecognised tonic: {tonic_pc!r}")
            tonic_pc = resolved

        self.genre = genre
        self.tonic_pc = int(tonic_pc) % 12
        self.num_chords = num_chords
        self.bpm = bpm
        if bpm <= 0:
            raise ValueError(f"bpm must be positive, got {bpm}")
        self.rng = random.Random(seed)
        self.duration_weights = dict(
            duration_weights
            if duration_weights is not None
            else GENRE_DURATION_WEIGHTS.get(genre, DEFAULT_DURATION_WEIGHTS)
        )
        unknown = set(self.duration_weights) - set(DURATION_BEATS)
        if unknown:
            raise ValueError(f"Unknown duration token(s): {sorted(unknown)}")
        if not self.duration_weights or sum(self.duration_weights.values()) <= 0:
            raise ValueError("duration_weights must contain a positive total weight")

        self.params = params if params is not None else GenParams()
        self.params.temperature = float(
            temperature if temperature is not None else GENRES_TO_TEMPS.get(genre, 1.0)
        )
        self.params.self_transition_discount = float(
            self_transition_discount
            if self_transition_discount is not None
            else GENRES_TO_SELF_TRANSITION_DISCOUNT.get(genre, 1.0)
        )

        self._load_distributions(dist_dir)

    def _load_distributions(self, dist_dir: str) -> None:
        genre_dir = os.path.join(dist_dir, self.genre)

        def _load(name: str) -> dict:
            path = os.path.join(genre_dir, name)
            if not os.path.isfile(path):
                raise FileNotFoundError(
                    f"Expected distribution file not found: {path}. "
                    f"Run extract_distributions.py first (see spec §11.1)."
                )
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

        self.stage1 = _load("stage1_backbone.json")
        self.stage2 = _load("stage2_bass.json")
        self.stage3 = _load("stage3_extensions.json")
        self.meta = _load("meta.json")

        if not self.stage1.get("level0", {}).get("counts"):
            raise ValueError(
                f"Stage1 level0 is empty for genre {self.genre!r} -- no guaranteed floor, "
                f"cannot generate (see extract_distributions.py's validate())."
            )

    def _first_state(self) -> str:
        """No spec-mandated rule for picking the very first chord of a song
        (§6's init_state() is left abstract). Prefer the tonic-position
        major/minor triad if it was observed at all; otherwise fall back to
        a frequency-weighted draw over every observed Level-0 state, which
        keeps the very first chord consistent with the same corpus
        statistics everything downstream already relies on."""
        for candidate in ("0_major", "0_minor"):
            if candidate in self.stage1["level0"]["counts"]:
                return candidate
        counts = self.stage1["level0"]["counts"]
        states = list(counts.keys())
        weights = list(counts.values())
        return self.rng.choices(states, weights=weights, k=1)[0]

    def generate(self) -> List[dict]:
        """Generate one naturally sampled song from the trained tables."""
        state_hist = GenState()
        events: List[dict] = []
        current_time = 0.0
        seconds_per_beat = 60.0 / self.bpm

        for t in range(self.num_chords):
            # ---- Stage 1 ------------------------------------------------
            if t == 0:
                chosen_state = self._first_state()
            else:
                ctx1 = state_hist.prev_states[-1] if len(state_hist.prev_states) >= 1 else None
                ctx2 = (
                    f"{state_hist.prev_states[-2]}|{state_hist.prev_states[-1]}"
                    if len(state_hist.prev_states) >= 2 else None
                )
                dist1 = stage1_distribution(ctx1, ctx2, self.stage1, self.params)
                chosen_state = (
                    weighted_choice(dist1, self.rng)
                    if dist1
                    else self._first_state()
                )

            root_str, triad = chosen_state.rsplit("_", 1)
            root_interval = int(root_str)

            # ---- Stage 2 --------------------------------------------------
            if triad in ("1", "5"):
                bass_str = "0"
                bass_interval = 0
            else:
                dist2 = stage2_distribution(root_str, state_hist.bass_prev, self.stage2, self.params)
                bass_str = weighted_choice(dist2, self.rng) if dist2 else "0"
                bass_interval = int(bass_str)

            # ---- Stage 3 ----------------------------------------------------
            if triad in ("1", "5"):
                seventh, ninth, eleventh, thirteenth = "N", "N", "N", "N"
            else:
                trie_node_table, _level = select_extension_trie(
                    chosen_state, triad, self.stage3, self.params, self.rng
                )
                seventh, ninth, eleventh, thirteenth = walk_extension_trie(
                    trie_node_table,
                    bass_str,
                    state_hist.ext_prev_str,
                    self.params,
                    self.rng,
                )

            # ---- Resolve absolute pitches for readability (§10.2) ----------
            root_pc = (root_interval + self.tonic_pc) % 12
            bass_pc = (root_pc + bass_interval) % 12
            root_name = PC_TO_NOTE[root_pc]
            bass_name = PC_TO_NOTE[bass_pc]
            harte = reconstruct_harte(root_name, triad, bass_name, seventh, ninth, eleventh, thirteenth)
            duration_token = self.rng.choices(
                list(self.duration_weights), weights=list(self.duration_weights.values()), k=1
            )[0]
            duration_beats = DURATION_BEATS[duration_token]
            duration_seconds = duration_beats * seconds_per_beat
            time_start = current_time
            current_time += duration_seconds

            events.append({
                "is_no_chord": False,
                "root_interval": root_interval,
                "triad": triad,
                "bass_interval": bass_interval,
                "seventh": seventh,
                "ninth": ninth,
                "eleventh": eleventh,
                "thirteenth": thirteenth,
                "root": root_name,
                "bass": bass_name,
                "harte": harte,
                "duration_token": duration_token,
                "duration_beats": duration_beats,
                "duration_seconds": duration_seconds,
                "time_start": time_start,
                "time_end": current_time,
            })

            state_hist.prev_states.append(chosen_state)
            if len(state_hist.prev_states) > 2:
                state_hist.prev_states.pop(0)
            state_hist.bass_prev = bass_str
            state_hist.ext_prev_str = "|".join((seventh, ninth, eleventh, thirteenth))

        return events

    def write_song(
        self,
        path: str,
        events: Optional[List[dict]] = None,
    ) -> None:
        if events is None:
            events = self.generate()
        payload = {
            "genre": self.genre,
            "tonic_pc": self.tonic_pc,
            "bpm": self.bpm,
            "num_chords": len(events),
            "duration_total_seconds": sum(
                event["duration_seconds"] for event in events
            ),
            "chords": events,
        }
        if any(event.get("is_no_chord", event.get("harte") == "N") for event in events):
            payload["no_chord"] = no_chord_summary(events)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# CLI (§10.1)
# ─────────────────────────────────────────────────────────────────────────────
def _parse_range(text: str) -> Tuple[int, int]:
    lo_str, hi_str = text.split(",")
    lo, hi = int(lo_str), int(hi_str)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def parse_args():
    p = argparse.ArgumentParser(description="Stage 1-3 chord progression generator (spec §2-§4, §6)")

    p.add_argument("--genre", default="jazz", choices=VALID_GENRES)
    p.add_argument("--tonic", default="C", help="Tonic note (C, F#, Bb ...) or integer 0-11")
    p.add_argument("--num", type=int, default=48, help="Chords per song")
    p.add_argument("--bpm", type=int, default=120, help="Tempo metadata only -- Stages 1-3 are tempo-agnostic")

    p.add_argument("--debug", action="store_true",
                    help="Shortcut for --random-genre --random-tonic --random-num --random-bpm")
    p.add_argument("--random-genre", action="store_true")
    p.add_argument("--random-tonic", action="store_true")
    p.add_argument("--random-num", action="store_true")
    p.add_argument("--random-num-range", default="24,96")
    p.add_argument("--random-bpm", action="store_true")
    p.add_argument("--random-bpm-range", default="60,180")

    p.add_argument("--temperature", type=float, default=None,
                    help="tau_1: Stage 1 Level-1 softmax temperature (§10.3). "
                         "Default: per-genre value from GENRES_TO_TEMPS (not re-rolled per song).")
    p.add_argument("--self-transition-discount", type=float, default=None,
                    help="delta: Stage 1 exact chord-repeat discount (§10.3).")

    p.add_argument("--discount", type=float, default=25,
                    help="D: interpolation discount constant, shared across all stages/levels (§9).")
    p.add_argument("--add-k", type=float, default=0,
                    help="k: within-distribution Laplace smoothing constant (§9).")
    p.add_argument("--min-count-a", type=int, default=200,
                    help="Stage 3 §4.2 Level A trie-selection threshold.")
    p.add_argument("--min-count-b", type=int, default=500,
                    help="Stage 3 §4.2 Level B trie-selection threshold.")
    p.add_argument("--epsilon-floor", type=float, default=0.02,
                    help="Optional epsilon floor of Level-0 mass mixed into Stage 1 (§8, §9).")

    p.add_argument("--songs", type=int, default=100)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--no-chord-rate", type=float, default=0.01)
    p.add_argument("--no-chord-mode", choices=("exact", "probabilistic"), default="exact")
    p.add_argument("--no-chord-off", action="store_true")
    p.add_argument("--dist-dir", default=_DEFAULT_DIST_DIR)
    p.add_argument(
        "--out-dir",
        default=None,
        help="Output directory override. By default, use gen/jazz-labels or "
             "gen/pop-rock-labels according to each song's genre.",
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.debug:
        args.random_genre = True
        args.random_tonic = True
        args.random_num = True
        args.random_bpm = True

    try:
        fixed_tonic: int | str = int(args.tonic)
    except ValueError:
        fixed_tonic = args.tonic

    num_lo, num_hi = _parse_range(args.random_num_range)
    bpm_lo, bpm_hi = _parse_range(args.random_bpm_range)

    base_params = GenParams(
        discount=args.discount,
        add_k=args.add_k,
        min_count_a=args.min_count_a,
        min_count_b=args.min_count_b,
        epsilon_floor=args.epsilon_floor,
    )

    if args.out_dir is not None:
        output_dirs = {genre: args.out_dir for genre in VALID_GENRES}
        output_description = args.out_dir
    else:
        output_dirs = {
            genre: os.path.join("gen", GENRE_LABEL_DIRS[genre])
            for genre in VALID_GENRES
        }
        output_description = "genre-specific directories below gen/"

    for output_dir in set(output_dirs.values()):
        os.makedirs(output_dir, exist_ok=True)

    print(f"Generating {args.songs} song(s) -> {output_description}")
    generated = []
    for i in range(args.songs):
        song_genre = random.choice(VALID_GENRES) if args.random_genre else args.genre
        song_tonic = random.randint(0, 11) if args.random_tonic else fixed_tonic
        song_num = random.randint(num_lo, num_hi) if args.random_num else args.num
        song_bpm = random.randint(bpm_lo, bpm_hi) if args.random_bpm else args.bpm

        print(f"  -> song {i}: genre={song_genre} tonic={song_tonic} num={song_num} bpm={song_bpm}")

        gen = ChordGenerator(
            genre=song_genre,
            tonic_pc=song_tonic,
            num_chords=song_num,
            temperature=args.temperature,
            self_transition_discount=args.self_transition_discount,
            bpm=song_bpm,
            seed=args.seed + i if args.seed is not None else None,
            dist_dir=args.dist_dir,
            params=GenParams(**{**base_params.__dict__}),
        )
        events = gen.generate()
        generated.append((song_genre, gen, events))
    rate = 0.0 if args.no_chord_off else args.no_chord_rate
    by_genre = {
        genre: [(index, events) for index, (song_genre, _gen, events) in enumerate(generated)
                if song_genre == genre]
        for genre in VALID_GENRES
    }
    filtered = {}
    for genre, indexed_songs in by_genre.items():
        filtered.update({
            index: events
            for index, events in zip(
                (index for index, _events in indexed_songs),
                apply_no_chord_filter(
                    [events for _index, events in indexed_songs],
                    rate=rate, mode=args.no_chord_mode, seed=args.seed,
                ),
            )
        })
    for i, (song_genre, gen, _events) in enumerate(generated):
        gen.write_song(
            os.path.join(output_dirs[song_genre], f"song_{i}.json"),
            filtered[i],
        )

    print("Done.")


if __name__ == "__main__":
    main()