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

The default generator remains natural.  `GenerationQuota` and
`generate_target_corpus()` provide the opt-in §08 batch mode: natural events
count toward minimum feature targets, while targeted events are selected only
through observed local transitions and attested extension-trie paths.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter
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
TARGET_LABEL_DIRS: Dict[str, str] = {
    genre: f"target-{directory}"
    for genre, directory in GENRE_LABEL_DIRS.items()
}

# §08 corpus and minimum-coverage targets.  These are deliberately kept
# separate from the sampling tables: natural events count toward a target and
# only the remaining deficit is eligible for targeted sampling.
TARGET_TOTAL_EVENTS = 250_000
TARGET_EVENTS_BY_GENRE: Dict[str, int] = {
    "jazz": 125_000,
    "pop_rock": 125_000,
}
RARE_TRIAD_TARGETS: Dict[str, Dict[str, int]] = {
    "jazz": {
        "sus2": 1_000,
        "augmented": 1_500,
        "diminished": 5_000,
        "1": 0,
        "5": 1_000,
        "sus4": 3_000,
    },
    "pop_rock": {
        "sus2": 1_000,
        "augmented": 750,
        "diminished": 1_500,
        "1": 2_000,
        "5": 2_500,
        "sus4": 3_000,
    },
}
RARE_EXTENSION_TARGETS: Dict[str, Dict[str, int]] = {
    "jazz": {
        "b9": 3_500,
        "#9": 1_000,
        "11": 2_500,
        "#11": 2_000,
        "13": 1_500,
        "b13": 1_000,
    },
    "pop_rock": {
        "b9": 200,
        "#9": 750,
        "11": 1_250,
        "#11": 200,
        "13": 500,
        "b13": 150,
    },
}
DENSE_EXTENSION_TARGETS: Dict[str, Dict[int, int]] = {
    genre: {2: 15_000, 3: 3_000, 4: 1_000}
    for genre in VALID_GENRES
}
EXTENSION_TARGET_SLOTS = {
    "b9": "ninth",
    "#9": "ninth",
    "11": "eleventh",
    "#11": "eleventh",
    "13": "thirteenth",
    "b13": "thirteenth",
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
) -> Tuple[str, str, str, str]:
    """Descend the 4-slot trie (seventh -> ninth -> eleventh -> thirteenth,
    §4.1 slot order), one interpolated backoff draw per slot (§4.3). Every
    step only samples among the trie's own children, so the resulting
    4-tuple is guaranteed to be a combination attested in training (§4.2)."""
    chosen: List[str] = []
    for depth in range(len(EXT_SLOTS)):
        prefix_str = "ROOT" if depth == 0 else "|".join(chosen)
        node = trie_node_table.get(prefix_str)
        if node is None:
            # Should not happen given a well-formed trie (every attested
            # prefix has a node), but degrade gracefully rather than crash.
            chosen.append("N")
            continue
        dist = _node_child_distribution(node, bass_str, ext_prev_str, params)
        if not dist:
            chosen.append("N")
            continue
        chosen.append(weighted_choice(dist, rng))
    return tuple(chosen)  # (seventh, ninth, eleventh, thirteenth)


def _trie_child_prefix(prefix: str, child: str) -> str:
    return child if prefix == "ROOT" else f"{prefix}|{child}"


def _trie_reaches_target(
    trie_node_table: dict,
    prefix: str,
    depth: int,
    target_depth: int,
    target_value: str,
) -> bool:
    """Whether a prefix has an attested descendant with the requested slot."""
    node = trie_node_table.get(prefix)
    if node is None:
        return False
    children = node.get("marginal", {}).get("counts", {})
    if depth == target_depth:
        return children.get(target_value, 0) > 0
    return any(
        _trie_reaches_target(
            trie_node_table,
            _trie_child_prefix(prefix, child),
            depth + 1,
            target_depth,
            target_value,
        )
        for child, count in children.items()
        if count > 0
    )


def _trie_reaches_density(
    trie_node_table: dict,
    prefix: str,
    depth: int,
    active_count: int,
    minimum_active: int,
) -> bool:
    """Whether a prefix has an attested complete path of a given density."""
    if depth == len(EXT_SLOTS):
        return active_count >= minimum_active
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
        )
        for child, count in children.items()
        if count > 0
    )


def walk_extension_trie_density(
    trie_node_table: dict,
    bass_str: str,
    ext_prev_str: str,
    minimum_active: int,
    params: GenParams,
    rng: random.Random,
) -> Optional[Tuple[str, str, str, str]]:
    """Walk a trie while requiring at least ``minimum_active`` extensions."""
    if not 1 <= minimum_active <= len(EXT_SLOTS):
        raise ValueError(f"minimum_active must be between 1 and {len(EXT_SLOTS)}")
    if not _trie_reaches_density(
        trie_node_table, "ROOT", 0, 0, minimum_active
    ):
        return None

    chosen: List[str] = []
    active_count = 0
    for depth in range(len(EXT_SLOTS)):
        prefix = "ROOT" if depth == 0 else "|".join(chosen)
        node = trie_node_table.get(prefix)
        if node is None:
            return None
        dist = _node_child_distribution(node, bass_str, ext_prev_str, params)
        dist = {
            child: probability
            for child, probability in dist.items()
            if _trie_reaches_density(
                trie_node_table,
                _trie_child_prefix(prefix, child),
                depth + 1,
                active_count + (child != "N"),
                minimum_active,
            )
        }
        if not dist:
            return None
        child = weighted_choice(dist, rng)
        chosen.append(child)
        active_count += child != "N"
    return tuple(chosen)


def walk_extension_trie_target(
    trie_node_table: dict,
    bass_str: str,
    ext_prev_str: str,
    target_slot: str,
    target_value: str,
    params: GenParams,
    rng: random.Random,
) -> Optional[Tuple[str, str, str, str]]:
    """Walk an attested trie path while requiring one extension token."""
    try:
        target_depth = EXT_SLOTS.index(target_slot)
    except ValueError:
        raise ValueError(f"Unknown extension slot: {target_slot!r}") from None

    if not _trie_reaches_target(
        trie_node_table, "ROOT", 0, target_depth, target_value
    ):
        return None

    chosen: List[str] = []
    for depth in range(len(EXT_SLOTS)):
        prefix = "ROOT" if depth == 0 else "|".join(chosen)
        node = trie_node_table.get(prefix)
        if node is None:
            return None
        dist = _node_child_distribution(node, bass_str, ext_prev_str, params)
        if not dist:
            return None

        if depth == target_depth:
            if target_value not in dist:
                return None
            dist = {target_value: dist[target_value]}
        elif depth < target_depth:
            dist = {
                child: probability
                for child, probability in dist.items()
                if _trie_reaches_target(
                    trie_node_table,
                    _trie_child_prefix(prefix, child),
                    depth + 1,
                    target_depth,
                    target_value,
                )
            }
            if not dist:
                return None

        chosen.append(weighted_choice(dist, rng))

    return tuple(chosen)


# ─────────────────────────────────────────────────────────────────────────────
# Harte reconstruction (readability only -- no voicing/rendering happens here)
# ─────────────────────────────────────────────────────────────────────────────
def reconstruct_harte(root_name: str, triad: str, bass_name: str,
                       seventh: str, ninth: str, eleventh: str, thirteenth: str) -> str:
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
    require_observed_successor: bool = False
    pending_target_state: Optional[str] = None


@dataclass
class GenerationQuota:
    """Minimum feature counts shared across the songs in one target batch."""
    genre: str
    total_events: int
    triad_targets: Dict[str, int]
    extension_targets: Dict[str, int]
    dense_targets: Dict[int, int] = field(default_factory=dict)
    min_target_spacing: int = 2
    target_buffer: int = 2
    dense_target_buffer: int = 50
    position: int = 0
    last_target_position: int = -10**9
    triad_counts: Counter = field(default_factory=Counter)
    extension_counts: Counter = field(default_factory=Counter)
    dense_counts: Counter = field(default_factory=Counter)
    triad_root_counts: Dict[str, Counter] = field(default_factory=dict)
    triad_target_root_counts: Dict[str, Counter] = field(default_factory=dict)
    extension_target_root_counts: Dict[str, Counter] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {self.genre!r}")
        if self.total_events <= 0:
            raise ValueError("total_events must be positive")
        if self.min_target_spacing < 1:
            raise ValueError("min_target_spacing must be at least 1")
        if self.target_buffer < 0:
            raise ValueError("target_buffer must be non-negative")
        if self.dense_target_buffer < 0:
            raise ValueError("dense_target_buffer must be non-negative")
        if any(count < 0 for count in self.triad_targets.values()):
            raise ValueError("triad target counts must be non-negative")
        if any(count < 0 for count in self.extension_targets.values()):
            raise ValueError("extension target counts must be non-negative")
        if any(
            minimum_active < 1 or minimum_active > len(EXT_SLOTS)
            for minimum_active in self.dense_targets
        ):
            raise ValueError("dense target levels must be between 1 and 4")
        if any(count < 0 for count in self.dense_targets.values()):
            raise ValueError("dense target counts must be non-negative")
        unknown = set(self.extension_targets) - set(EXTENSION_TARGET_SLOTS)
        if unknown:
            raise ValueError(f"Unknown extension target(s): {sorted(unknown)}")

    @classmethod
    def for_genre(cls, genre: str, total_events: int) -> "GenerationQuota":
        """Create the §08 targets, proportionally scaled for smaller tests."""
        if genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {genre!r}")
        if total_events <= 0:
            raise ValueError("total_events must be positive")
        reference_events = TARGET_EVENTS_BY_GENRE[genre]

        def scaled(source: Dict[str, int]) -> Dict[str, int]:
            return {
                key: (
                    value
                    if total_events >= reference_events
                    else (value * total_events + reference_events - 1)
                    // reference_events
                )
                for key, value in source.items()
                if value > 0
            }

        return cls(
            genre=genre,
            total_events=total_events,
            triad_targets=scaled(RARE_TRIAD_TARGETS[genre]),
            extension_targets=scaled(RARE_EXTENSION_TARGETS[genre]),
            dense_targets=scaled(DENSE_EXTENSION_TARGETS[genre]),
        )

    def remaining(self, kind: str, key: str) -> int:
        targets = self.triad_targets if kind == "triad" else self.extension_targets
        counts = self.triad_counts if kind == "triad" else self.extension_counts
        return max(targets.get(key, 0) - counts.get(key, 0), 0)

    def due_position(self, kind: str, key: str) -> Optional[int]:
        target = (
            self.triad_targets.get(key, 0)
            if kind == "triad"
            else self.extension_targets.get(key, 0)
        )
        if target <= 0:
            return None
        count = (
            self.triad_counts.get(key, 0)
            if kind == "triad"
            else self.extension_counts.get(key, 0)
        )
        if count >= target:
            return None
        # Schedule a couple of extra opportunities so a valid local successor
        # is still available near the end of a finite batch. The public target
        # remains a minimum, so harmless overshoot is preferable to a deficit.
        schedule_target = target + self.target_buffer
        return (
            (count + 1) * self.total_events + schedule_target - 1
        ) // schedule_target

    def is_due(self, kind: str, key: str) -> bool:
        due = self.due_position(kind, key)
        return due is not None and self.position >= due

    def dense_remaining(self, minimum_active: int) -> int:
        return max(
            self.dense_targets.get(minimum_active, 0)
            - self.dense_counts.get(minimum_active, 0),
            0,
        )

    def dense_due_position(self, minimum_active: int) -> Optional[int]:
        target = self.dense_targets.get(minimum_active, 0)
        count = self.dense_counts.get(minimum_active, 0)
        if target <= 0 or count >= target:
            return None
        schedule_target = target + self.dense_target_buffer
        return (
            (count + 1) * self.total_events + schedule_target - 1
        ) // schedule_target

    def dense_is_due(self, minimum_active: int) -> bool:
        due = self.dense_due_position(minimum_active)
        return due is not None and self.position >= due

    def can_target(self) -> bool:
        return self.position - self.last_target_position >= self.min_target_spacing

    def mark_target(self) -> None:
        self.last_target_position = self.position

    def record(
        self,
        event: dict,
        *,
        targeted_triad: bool = False,
        targeted_extension: Optional[str] = None,
    ) -> None:
        triad = str(event["triad"])
        root = str(event["root_interval"])
        self.triad_counts[triad] += 1
        self.triad_root_counts.setdefault(triad, Counter())[root] += 1
        for slot in EXT_SLOTS:
            value = str(event[slot])
            if value != "N":
                self.extension_counts[value] += 1
        active_count = sum(
            str(event[slot]) != "N"
            for slot in EXT_SLOTS
        )
        for minimum_active in self.dense_targets:
            if active_count >= minimum_active:
                self.dense_counts[minimum_active] += 1
        if targeted_triad:
            self.triad_target_root_counts.setdefault(triad, Counter())[root] += 1
        if targeted_extension is not None:
            if any(
                str(event[slot]) == targeted_extension
                for slot in EXT_SLOTS
            ):
                self.extension_target_root_counts.setdefault(
                    targeted_extension, Counter()
                )[root] += 1
        self.position += 1

    def deficits(self) -> Dict[str, Dict[str, int]]:
        return {
            "triads": {
                key: self.remaining("triad", key)
                for key in self.triad_targets
                if self.remaining("triad", key)
            },
            "extensions": {
                key: self.remaining("extension", key)
                for key in self.extension_targets
                if self.remaining("extension", key)
            },
            "dense": {
                f"{minimum_active}+": self.dense_remaining(minimum_active)
                for minimum_active in self.dense_targets
                if self.dense_remaining(minimum_active)
            },
        }

    def meets_targets(self) -> bool:
        return not any(self.deficits().values())


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
        self._target_trie_cache: Dict[Tuple[str, str], Optional[dict]] = {}
        self._dense_trie_cache: Dict[Tuple[str, int], Optional[dict]] = {}
        self._reachable_states_cache: Optional[set] = None

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

    def _target_extension_trie(
        self,
        state: str,
        triad: str,
        target_value: str,
    ) -> Optional[dict]:
        """Find the narrowest trie that attests a requested extension."""
        if triad in ("1", "5"):
            return None

        cache_key = (state, target_value)
        if cache_key in self._target_trie_cache:
            return self._target_trie_cache[cache_key]

        target_slot = EXTENSION_TARGET_SLOTS[target_value]
        trie_a = self.stage3["trie_A"].get(state)
        if trie_a and _trie_reaches_target(
            trie_a, "ROOT", 0, EXT_SLOTS.index(target_slot), target_value
        ):
            self._target_trie_cache[cache_key] = trie_a
            return trie_a

        trie_b = self.stage3["trie_B"].get(triad)
        if trie_b and _trie_reaches_target(
            trie_b, "ROOT", 0, EXT_SLOTS.index(target_slot), target_value
        ):
            self._target_trie_cache[cache_key] = trie_b
            return trie_b

        # A genre-wide fallback is only support-compatible when the triad has
        # no triad-level trie of its own. Otherwise, declining the target is
        # preferable to inventing an extension/triad combination.
        if not trie_b:
            trie_c = self.stage3["trie_C"].get("ALL")
            if trie_c and _trie_reaches_target(
                trie_c, "ROOT", 0, EXT_SLOTS.index(target_slot), target_value
            ):
                self._target_trie_cache[cache_key] = trie_c
                return trie_c

        self._target_trie_cache[cache_key] = None
        return None

    def _dense_extension_trie(
        self,
        state: str,
        triad: str,
        minimum_active: int,
    ) -> Optional[dict]:
        """Find a trie with an attested path at the requested density."""
        if triad in ("1", "5"):
            return None

        cache_key = (state, minimum_active)
        if cache_key in self._dense_trie_cache:
            return self._dense_trie_cache[cache_key]

        candidates = [
            self.stage3["trie_A"].get(state),
            self.stage3["trie_B"].get(triad),
        ]
        if not candidates[1]:
            candidates.append(self.stage3["trie_C"].get("ALL"))
        for trie in candidates:
            if trie and _trie_reaches_density(
                trie, "ROOT", 0, 0, minimum_active
            ):
                self._dense_trie_cache[cache_key] = trie
                return trie

        self._dense_trie_cache[cache_key] = None
        return None

    def _locally_reachable_states(self) -> set:
        """States reachable from the same starts used by natural generation."""
        if self._reachable_states_cache is not None:
            return self._reachable_states_cache

        counts = self.stage1["level0"]["counts"]
        preferred = [
            state
            for state in ("0_major", "0_minor")
            if state in counts
        ]
        starts = preferred[:1] or list(counts)
        reachable = set(starts)
        pending = list(starts)
        while pending:
            state = pending.pop()
            for successor in self.stage1["level1"]["counts"].get(state, {}):
                if successor not in reachable:
                    reachable.add(successor)
                    pending.append(successor)
        self._reachable_states_cache = reachable
        return reachable

    def _target_state(
        self,
        dist1: Dict[str, float],
        ctx1: Optional[str],
        ctx2: Optional[str],
        quota: Optional[GenerationQuota],
    ) -> Tuple[Optional[str], bool, Optional[str]]:
        """Choose a due target only from observed local successor edges."""
        if quota is None or not quota.can_target():
            return None, False, None

        local_counts = _attested_transition_counts(ctx1, ctx2, self.stage1)
        if not local_counts:
            return None, False, None

        due_triads = {
            triad
            for triad in quota.triad_targets
            if quota.is_due("triad", triad)
        }

        def target_root_count(state: str) -> int:
            root, triad = state.rsplit("_", 1)
            return quota.triad_target_root_counts.get(triad, {}).get(root, 0)

        def state_weight(state: str, root_count: Optional[int] = None) -> float:
            if root_count is None:
                root_count = target_root_count(state)
            return (
                dist1.get(state, 0.0)
                * local_counts.get(state, 0)
                / (1.0 + root_count)
            )

        # Prefer roots that have not received a targeted event yet. If no
        # such root is locally reachable, allow a one-step observed bridge:
        # current -> bridge -> target. Both edges remain corpus-attested.
        triad_paths = []
        for target_state in dist1:
            if target_state.rsplit("_", 1)[1] not in due_triads:
                continue
            root_count = target_root_count(target_state)
            direct_weight = state_weight(target_state, root_count)
            if target_state in local_counts and direct_weight > 0:
                triad_paths.append(
                    (target_state, None, target_state, root_count, direct_weight)
                )

            if quota.position + 1 >= quota.total_events:
                continue
            for bridge, bridge_count in local_counts.items():
                if bridge == target_state:
                    continue
                edge_count = self.stage1["level1"]["counts"].get(
                    bridge, {}
                ).get(target_state, 0)
                if edge_count <= 0:
                    continue
                bridge_weight = (
                    dist1.get(bridge, 0.0)
                    * bridge_count
                    * edge_count
                    / (1.0 + root_count)
                )
                if bridge_weight > 0:
                    triad_paths.append(
                        (bridge, target_state, target_state, root_count, bridge_weight)
                    )

        if triad_paths:
            reachable = self._locally_reachable_states()
            supported_roots = {
                state.rsplit("_", 1)[0]
                for state in self.stage1["level0"]["counts"]
                if state.rsplit("_", 1)[1] in due_triads
                and state in reachable
            }
            if not supported_roots:
                supported_roots = {path[2].rsplit("_", 1)[0] for path in triad_paths}
            minimum_root_count = min(
                quota.triad_target_root_counts.get(
                    path[2].rsplit("_", 1)[1], {}
                ).get(path[2].rsplit("_", 1)[0], 0)
                for path in triad_paths
                if path[2].rsplit("_", 1)[0] in supported_roots
            )
            balanced = [
                path
                for path in triad_paths
                if path[3] <= minimum_root_count + 1
                and path[2].rsplit("_", 1)[0] in supported_roots
            ]
            if not balanced:
                return None, False, None
            uncovered = [path for path in balanced if path[3] == 0]
            candidates = uncovered or balanced
            chosen_path = self.rng.choices(
                candidates, weights=[path[4] for path in candidates], k=1
            )[0]
            chosen_state, pending_target, _target, _root_count, _weight = chosen_path
            return chosen_state, pending_target is None, pending_target

        due_extensions = [
            value
            for value in quota.extension_targets
            if quota.is_due("extension", value)
        ]
        due_dense = [
            minimum_active
            for minimum_active in quota.dense_targets
            if quota.dense_is_due(minimum_active)
        ]

        extension_candidates = {}
        for state in dist1:
            if state not in local_counts:
                continue
            root, triad = state.rsplit("_", 1)
            supported_extensions = [
                value
                for value in due_extensions
                if self._target_extension_trie(state, triad, value) is not None
            ]
            if supported_extensions:
                root_count = min(
                    quota.extension_target_root_counts.get(value, {}).get(root, 0)
                    for value in supported_extensions
                )
                weight = (
                    dist1.get(state, 0.0)
                    * local_counts.get(state, 0)
                    / (1.0 + root_count)
                )
                if weight > 0:
                    extension_candidates[state] = weight
        if extension_candidates:
            return weighted_choice(extension_candidates, self.rng), False, None

        if not due_dense:
            return None, False, None

        def supports_dense(state: str) -> bool:
            _root, triad = state.rsplit("_", 1)
            return any(
                self._dense_extension_trie(state, triad, minimum_active) is not None
                for minimum_active in due_dense
            )

        dense_paths = []
        for target_state in dist1:
            if not supports_dense(target_state):
                continue
            if target_state in local_counts:
                weight = state_weight(target_state)
                if weight > 0:
                    dense_paths.append(
                        (target_state, None, weight)
                    )

            if quota.position + 1 >= quota.total_events:
                continue
            for bridge, bridge_count in local_counts.items():
                if bridge == target_state:
                    continue
                edge_count = self.stage1["level1"]["counts"].get(
                    bridge, {}
                ).get(target_state, 0)
                if edge_count <= 0:
                    continue
                weight = (
                    dist1.get(bridge, 0.0)
                    * bridge_count
                    * edge_count
                )
                if weight > 0:
                    dense_paths.append(
                        (bridge, target_state, weight)
                    )

        if not dense_paths:
            return None, False, None
        chosen_state, pending_target, _weight = self.rng.choices(
            dense_paths,
            weights=[path[2] for path in dense_paths],
            k=1,
        )[0]
        return chosen_state, False, pending_target

    def _target_extension(
        self,
        state: str,
        triad: str,
        quota: Optional[GenerationQuota],
    ) -> Optional[Tuple[str, str, dict]]:
        """Select the most overdue supported extension target for a state."""
        if quota is None or not quota.can_target() or triad in ("1", "5"):
            return None

        candidates = []
        for value in quota.extension_targets:
            if not quota.is_due("extension", value):
                continue
            trie = self._target_extension_trie(state, triad, value)
            if trie is None:
                continue
            due = quota.due_position("extension", value) or quota.position
            candidates.append((value, trie, max(quota.position - due + 1, 1)))
        if not candidates:
            return None

        value = weighted_choice(
            {candidate[0]: candidate[2] for candidate in candidates}, self.rng
        )
        for candidate_value, trie, _weight in candidates:
            if candidate_value == value:
                return EXTENSION_TARGET_SLOTS[value], value, trie
        return None

    def _target_dense_extension(
        self,
        state: str,
        triad: str,
        quota: Optional[GenerationQuota],
    ) -> Optional[Tuple[int, dict]]:
        """Select the densest due extension target supported by this chord."""
        if quota is None or not quota.can_target() or triad in ("1", "5"):
            return None
        due_levels = sorted(
            (
                minimum_active
                for minimum_active in quota.dense_targets
                if quota.dense_is_due(minimum_active)
            ),
            reverse=True,
        )
        for minimum_active in due_levels:
            trie = self._dense_extension_trie(
                state, triad, minimum_active
            )
            if trie is not None:
                return minimum_active, trie
        return None

    def generate(self, quota: Optional[GenerationQuota] = None) -> List[dict]:
        if quota is not None and quota.genre != self.genre:
            raise ValueError(
                f"quota genre {quota.genre!r} does not match generator genre {self.genre!r}"
            )
        state_hist = GenState()
        events: List[dict] = []
        current_time = 0.0
        seconds_per_beat = 60.0 / self.bpm

        for t in range(self.num_chords):
            targeted_triad = False
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
                if not dist1:
                    chosen_state = self._first_state()
                    state_hist.pending_target_state = None
                    state_hist.require_observed_successor = False
                elif state_hist.pending_target_state is not None:
                    pending_state = state_hist.pending_target_state
                    pending_triad = pending_state.rsplit("_", 1)[1]
                    local_counts = _attested_transition_counts(
                        ctx1, ctx2, self.stage1
                    )
                    pending_dense = (
                        quota is not None
                        and any(
                            quota.dense_is_due(minimum_active)
                            and self._dense_extension_trie(
                                pending_state,
                                pending_triad,
                                minimum_active,
                            ) is not None
                            for minimum_active in quota.dense_targets
                        )
                    )
                    if (
                        quota is not None
                        and (
                            quota.is_due("triad", pending_triad)
                            or pending_dense
                        )
                        and local_counts.get(pending_state, 0) > 0
                    ):
                        chosen_state = pending_state
                        targeted_triad = quota.is_due("triad", pending_triad)
                    else:
                        chosen_state = weighted_choice(dist1, self.rng)
                    state_hist.pending_target_state = None
                elif state_hist.require_observed_successor:
                    successor_dist = _attested_successor_distribution(
                        dist1, ctx1, ctx2, self.stage1
                    )
                    if successor_dist:
                        chosen_state = weighted_choice(successor_dist, self.rng)
                    else:
                        (
                            chosen_state,
                            targeted_triad,
                            pending_target,
                        ) = self._target_state(
                            dist1, ctx1, ctx2, quota
                        )
                        state_hist.pending_target_state = pending_target
                        if chosen_state is None:
                            chosen_state = weighted_choice(dist1, self.rng)
                    state_hist.require_observed_successor = False
                else:
                    (
                        chosen_state,
                        targeted_triad,
                        pending_target,
                    ) = self._target_state(
                        dist1, ctx1, ctx2, quota
                    )
                    if pending_target is not None and t == self.num_chords - 1:
                        chosen_state = None
                    else:
                        state_hist.pending_target_state = pending_target
                    if chosen_state is None:
                        chosen_state = weighted_choice(dist1, self.rng)

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
            targeted_extension = False
            targeted_extension_value: Optional[str] = None
            targeted_dense = False
            if triad in ("1", "5"):
                seventh, ninth, eleventh, thirteenth = "N", "N", "N", "N"
            else:
                target_tuple = None
                if state_hist.pending_target_state is None:
                    target = self._target_extension(
                        chosen_state, triad, quota
                    )
                    if target is not None:
                        target_slot, target_value, target_trie = target
                        target_tuple = walk_extension_trie_target(
                            target_trie,
                            bass_str,
                            state_hist.ext_prev_str,
                            target_slot,
                            target_value,
                            self.params,
                            self.rng,
                        )

                    if target_tuple is None:
                        dense_target = self._target_dense_extension(
                            chosen_state, triad, quota
                        )
                        if dense_target is not None:
                            minimum_active, dense_trie = dense_target
                            target_tuple = walk_extension_trie_density(
                                dense_trie,
                                bass_str,
                                state_hist.ext_prev_str,
                                minimum_active,
                                self.params,
                                self.rng,
                            )
                            targeted_dense = target_tuple is not None

                if target_tuple is not None:
                    seventh, ninth, eleventh, thirteenth = target_tuple
                    if not targeted_dense:
                        targeted_extension = target is not None
                        targeted_extension_value = target_value
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

            if quota is not None:
                quota.record(
                    events[-1],
                    targeted_triad=targeted_triad,
                    targeted_extension=targeted_extension_value,
                )
                if targeted_triad or targeted_extension or targeted_dense:
                    quota.mark_target()
                    if state_hist.pending_target_state is None:
                        state_hist.require_observed_successor = True

            state_hist.prev_states.append(chosen_state)
            if len(state_hist.prev_states) > 2:
                state_hist.prev_states.pop(0)
            state_hist.bass_prev = bass_str
            state_hist.ext_prev_str = "|".join((seventh, ninth, eleventh, thirteenth))

        return events

    def generate_batch(
        self,
        num_songs: int,
        total_events: int,
        quota: Optional[GenerationQuota] = None,
        song_tonics: Optional[List[int]] = None,
        song_bpms: Optional[List[int]] = None,
    ) -> List[List[dict]]:
        """Generate independently-segmented songs with an exact event total."""
        if num_songs <= 0:
            raise ValueError("num_songs must be positive")
        if total_events < num_songs:
            raise ValueError("total_events must be at least num_songs")
        if quota is not None and quota.total_events != total_events:
            raise ValueError("quota.total_events must equal total_events")
        if song_tonics is not None and len(song_tonics) != num_songs:
            raise ValueError("song_tonics must contain one tonic per song")
        if song_bpms is not None and len(song_bpms) != num_songs:
            raise ValueError("song_bpms must contain one BPM per song")
        if song_bpms is not None and any(bpm <= 0 for bpm in song_bpms):
            raise ValueError("song_bpms must contain only positive BPM values")

        base, remainder = divmod(total_events, num_songs)
        songs = []
        original_num_chords = self.num_chords
        original_tonic_pc = self.tonic_pc
        original_bpm = self.bpm
        try:
            for index in range(num_songs):
                self.num_chords = base + (1 if index < remainder else 0)
                if song_tonics is not None:
                    self.tonic_pc = int(song_tonics[index]) % 12
                if song_bpms is not None:
                    self.bpm = song_bpms[index]
                songs.append(self.generate(quota=quota))
        finally:
            self.num_chords = original_num_chords
            self.tonic_pc = original_tonic_pc
            self.bpm = original_bpm
        return songs

    def write_song(self, path: str, events: Optional[List[dict]] = None) -> None:
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
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


def generate_target_corpus(
    events_per_genre: int = TARGET_EVENTS_BY_GENRE["jazz"],
    songs_per_genre: int = 100,
    *,
    events_by_genre: Optional[Dict[str, int]] = None,
    tonic_pc: int | str = 0,
    bpm: int = 120,
    temperature: Optional[float] = None,
    self_transition_discount: Optional[float] = None,
    seed: Optional[int] = None,
    dist_dir: str = _DEFAULT_DIST_DIR,
    params: Optional[GenParams] = None,
    duration_weights: Optional[Dict[str, float]] = None,
    output_dirs: Optional[Dict[str, str]] = None,
    random_tonic: bool = False,
    random_bpm: bool = False,
    random_bpm_range: Tuple[int, int] = (60, 180),
) -> Dict[str, List[List[dict]]]:
    """Generate the quota-driven §08 corpus without mixing genre models.

    Random tonic and BPM values may vary by song without changing the exact
    event budgets. Song lengths remain an exact partition of each genre budget.
    """
    if events_per_genre <= 0:
        raise ValueError("events_per_genre must be positive")
    if songs_per_genre <= 0:
        raise ValueError("songs_per_genre must be positive")
    if events_by_genre is None:
        events_by_genre = {
            genre: events_per_genre
            for genre in VALID_GENRES
        }
    else:
        unknown = set(events_by_genre) - set(VALID_GENRES)
        missing = set(VALID_GENRES) - set(events_by_genre)
        if unknown or missing:
            raise ValueError(
                f"events_by_genre must contain exactly {VALID_GENRES}"
            )
        if any(value <= 0 for value in events_by_genre.values()):
            raise ValueError("events_by_genre values must be positive")
    if any(value < songs_per_genre for value in events_by_genre.values()):
        raise ValueError("each genre event budget must be at least songs_per_genre")
    bpm_lo, bpm_hi = random_bpm_range
    if bpm_lo > bpm_hi:
        bpm_lo, bpm_hi = bpm_hi, bpm_lo
    if random_bpm and bpm_lo <= 0:
        raise ValueError("random_bpm_range must contain only positive BPM values")

    if output_dirs is not None:
        missing = set(VALID_GENRES) - set(output_dirs)
        if missing:
            raise ValueError(f"output_dirs is missing genre(s): {sorted(missing)}")
        for path in output_dirs.values():
            os.makedirs(path, exist_ok=True)

    result: Dict[str, List[List[dict]]] = {}
    for genre_index, genre in enumerate(VALID_GENRES):
        event_budget = events_by_genre[genre]
        quota = GenerationQuota.for_genre(genre, event_budget)
        generator_params = (
            GenParams(**vars(params)) if params is not None else GenParams()
        )
        genre_seed = (
            seed + genre_index * 1_000_003
            if seed is not None
            else None
        )
        generator = ChordGenerator(
            genre=genre,
            tonic_pc=tonic_pc,
            num_chords=event_budget // songs_per_genre,
            temperature=temperature,
            self_transition_discount=self_transition_discount,
            seed=genre_seed,
            dist_dir=dist_dir,
            bpm=bpm,
            params=generator_params,
            duration_weights=duration_weights,
        )
        property_seed = (
            genre_seed + 7_919
            if genre_seed is not None
            else None
        )
        property_rng = random.Random(property_seed)
        song_tonics = (
            [property_rng.randint(0, 11) for _ in range(songs_per_genre)]
            if random_tonic
            else None
        )
        song_bpms = (
            [
                property_rng.randint(bpm_lo, bpm_hi)
                for _ in range(songs_per_genre)
            ]
            if random_bpm
            else None
        )
        songs = generator.generate_batch(
            songs_per_genre,
            event_budget,
            quota=quota,
            song_tonics=song_tonics,
            song_bpms=song_bpms,
        )
        if not quota.meets_targets():
            raise RuntimeError(
                f"Could not meet {genre} generation targets: {quota.deficits()}"
            )
        result[genre] = songs

        if output_dirs is not None:
            for index, events in enumerate(songs):
                if song_tonics is not None:
                    generator.tonic_pc = song_tonics[index]
                if song_bpms is not None:
                    generator.bpm = song_bpms[index]
                generator.write_song(
                    os.path.join(output_dirs[genre], f"song_{index}.json"),
                    events,
                )

    return result


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
    p.add_argument(
        "--target-corpus",
        action="store_true",
        help="Generate the §08 corpus target: 250,000 events split evenly by genre.",
    )
    p.add_argument(
        "--target-events",
        type=int,
        default=None,
        help="Generate this many quota-aware events in total, split between genres.",
    )
    p.add_argument(
        "--target-songs",
        type=int,
        default=None,
        help="Songs per genre in target mode (defaults to --songs).",
    )
    p.add_argument("--seed", type=int, default=None)
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

    if args.target_corpus and args.target_events is not None:
        raise ValueError("--target-corpus and --target-events cannot be combined")

    target_total = (
        TARGET_TOTAL_EVENTS
        if args.target_corpus
        else args.target_events
    )
    if target_total is not None:
        if target_total <= 0:
            raise ValueError("target event count must be positive")
        target_songs = args.target_songs if args.target_songs is not None else args.songs
        if target_songs <= 0:
            raise ValueError("target song count must be positive")

        jazz_events = target_total // 2
        events_by_genre = {
            "jazz": jazz_events,
            "pop_rock": target_total - jazz_events,
        }
        if min(events_by_genre.values()) < target_songs:
            raise ValueError("target event count must provide at least one event per song")

        if args.out_dir is None:
            output_dirs = {
                genre: os.path.join("gen", TARGET_LABEL_DIRS[genre])
                for genre in VALID_GENRES
            }
            output_description = "separate target directories below gen/"
        else:
            output_dirs = {
                genre: os.path.join(args.out_dir, GENRE_LABEL_DIRS[genre])
                for genre in VALID_GENRES
            }
            output_description = f"genre-specific directories below {args.out_dir}"

        ignored_random_options = []
        if args.random_genre:
            ignored_random_options.append("--random-genre")
        if args.random_num:
            ignored_random_options.append("--random-num")
        if ignored_random_options:
            print(
                "Target mode ignores "
                + ", ".join(ignored_random_options)
                + " to preserve the requested genre split and exact event total."
            )
        print(
            f"Generating {target_total} target event(s) "
            f"({jazz_events} jazz, {target_total - jazz_events} pop/rock) "
            f"-> {output_description}"
        )
        generate_target_corpus(
            songs_per_genre=target_songs,
            events_by_genre=events_by_genre,
            tonic_pc=fixed_tonic,
            bpm=args.bpm,
            temperature=args.temperature,
            self_transition_discount=args.self_transition_discount,
            seed=args.seed,
            dist_dir=args.dist_dir,
            params=base_params,
            output_dirs=output_dirs,
            random_tonic=args.random_tonic,
            random_bpm=args.random_bpm,
            random_bpm_range=(bpm_lo, bpm_hi),
        )
        print("Done.")
        return

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
        gen.write_song(
            os.path.join(output_dirs[song_genre], f"song_{i}.json"),
            events,
        )

    print("Done.")


if __name__ == "__main__":
    main()