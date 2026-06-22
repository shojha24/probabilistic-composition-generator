"""
generator.py — Multi-instrument chord generator with long-tail reweighting
============================================================================
Generates chords and outputs one multi-voice JFugue Staccato string per
song (chord pad, bass, extensions, arpeggiator, percussion) via
MultitrackComposerVariable.

This version adds, on top of the previous merged implementation:

  - Inverse-frequency reweighting ("beta" scaling) applied independently
    to each of the 6 eventual classification heads (root+triad, bass,
    7th, 9th, 11th, 13th), per genre. beta=1.0 reproduces the trained
    distribution exactly (faithful); beta=0.0 flattens it to uniform
    over the *attested* support; values in between interpolate
    smoothly. There is no count-floor cutoff anywhere -- every reachable
    class stays reachable at every beta, just reweighted.

  - A conditional extension chain (7th -> 9th | 7th -> 11th | 7th,9th ->
    13th | 7th,9th,11th) replacing the old atomic joint-tuple draw. This
    lets each extension head be reweighted independently while
    guaranteeing every sampled (7th,9th,11th,13th) combination is one
    that genuinely co-occurred in the training corpus for that state --
    nothing unattested is ever invented, by construction (see
    ExtensionChain below).

  - Per-chord variable durations (ww/w/h/h./q) with exact timeline
    tracking, debug/randomized CLI inputs, and self-transition
    probability discounting -- unchanged from the prior version.

Design note on scope: per your decision, combinations that NEVER occur
in a given genre's corpus (e.g. a 13th in classical, a 9th in folk)
remain completely unsupported regardless of beta. Reweighting only
redistributes probability mass among classes that already have at
least one observed instance for that state/genre; it never grants
support to a class with zero observed count.
"""

import json
import os
import random
import argparse
from collections import defaultdict
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PC_TO_NOTE = {
    0: "C",  1: "Db", 2: "D",  3: "Eb", 4: "E",  5: "F",
    6: "F#", 7: "G",  8: "Ab", 9: "A", 10: "Bb", 11: "B",
}

NOTE_TO_PC = {
    "C": 0,  "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4,  "Fb": 4, "F": 5,  "E#": 5, "F#": 6, "Gb": 6, "G": 7,
    "G#": 8, "Ab": 8, "A": 9,  "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}

TRIAD_TONES = {
    "major":      [0, 4, 7],
    "minor":      [0, 3, 7],
    "diminished": [0, 3, 6],
    "augmented":  [0, 4, 8],
    "sus4":       [0, 5, 7],
    "sus2":       [0, 2, 7],
    "5":          [0, 7],
}

EXT_TO_SEMI = {
    "7": 11, "b7": 10, "bb7": 9, "b9": 1, "#9": 3,
    "9": 2, "11": 5, "#11": 6, "b13": 8, "13": 9,
}

QUALITY_SHORTHAND = {
    ("major",      "N"):   "maj",
    ("major",      "b7"):  "7",
    ("major",      "7"):   "maj7",
    ("major",      "bb7"): "maj",
    ("minor",      "N"):   "min",
    ("minor",      "b7"):  "min7",
    ("minor",      "7"):   "minmaj7",
    ("minor",      "bb7"): "min",
    ("diminished", "N"):   "dim",
    ("diminished", "b7"):  "hdim7",
    ("diminished", "bb7"): "dim7",
    ("diminished", "7"):   "dim",
    ("augmented",  "N"):   "aug",
    ("augmented",  "b7"):  "aug7",
    ("augmented",  "7"):   "aug",
    ("sus4",       "N"):   "sus4",
    ("sus4",       "b7"):  "7sus4",
    ("sus4",       "7"):   "sus4",
    ("sus2",       "N"):   "sus2",
    ("sus2",       "b7"):  "sus2",
    ("5",          "N"):   "5",
    ("5",          "b7"):  "5",
}

VALID_GENRES = ("pop_rock", "jazz", "classical", "folk")

# Base temperature per genre. Unchanged from the prior version -- this
# scales level-1 (root+triad) transition *probabilities* on top of
# whatever beta reweighting already did to the underlying counts. Kept
# separate from beta because temperature reshapes probabilities at
# generation time per-call, while beta reshapes the loaded distribution
# itself based on raw counts; the two compose but answer different
# questions ("how peaked should sampling be" vs "how much should rare
# classes be boosted relative to their true support").
GENRES_TO_TEMPS = {
    "pop_rock":  1.0,
    "jazz":      1.0,
    "classical": 1.0,
    "folk":      1.0,
}

GENRES_TO_SELF_TRANSITION_DISCOUNT = {
    "pop_rock":  0.8,
    "jazz":      1.0,
    "classical": 0.9,
    "folk":      0.8,
}

# ─────────────────────────────────────────────────────────────────────────────
# Long-tail reweighting (beta) configuration
# ─────────────────────────────────────────────────────────────────────────────
#
# beta = 1.0 -> faithful to the trained corpus (no change).
# beta = 0.0 -> fully uniform over the attested support for that state.
# 0 < beta < 1 -> smooth interpolation; every attested class keeps
#                 nonzero probability at every beta, so there's no
#                 arbitrary inclusion/exclusion cutoff to pick.
#
# Heads: "root_triad" (level-1 transitions), "bass" (level-2 bass),
# "seventh", "ninth", "eleventh", "thirteenth" (the 4 extension-chain
# heads, sampled conditionally -- see ExtensionChain).
#
# These starting values are deliberately conservative (closer to 1.0
# than 0.0) and are meant to be tuned empirically against realized
# per-class support in your generated corpus, not treated as final.
DEFAULT_BETA = 1.0

BETA_CONFIG = {
    "pop_rock": {
        "root_triad": 0.65, "bass": 0.55,
        "seventh": 0.6, "ninth": 0.5, "eleventh": 0.5, "thirteenth": 0.5,
    },
    "jazz": {
        "root_triad": 0.7, "bass": 0.6,
        "seventh": 0.8, "ninth": 0.6, "eleventh": 0.5, "thirteenth": 0.5,
    },
    "classical": {
        "root_triad": 0.7, "bass": 0.55,
        "seventh": 0.6, "ninth": 0.5, "eleventh": 0.5, "thirteenth": 1.0,
        # thirteenth fixed at 1.0: classical has literally zero observed
        # non-N 13th values for any state (see module docstring on scope)
        # -- there is nothing to redistribute toward, so beta is a no-op
        # here regardless of value. Left at 1.0 for clarity.
    },
    "folk": {
        "root_triad": 0.65, "bass": 0.55,
        "seventh": 0.7, "ninth": 1.0, "eleventh": 0.6, "thirteenth": 1.0,
        # ninth/thirteenth fixed at 1.0 for the same reason as classical's
        # thirteenth above -- folk has no observed 9th or 13th variation.
    },
}

# Duration tokens available per chord, in beats (quarter note = 1 beat).
DURATION_BEATS = {
    "ww": 8.0, "w.": 6.0, "w": 4.0, "h.": 3.0, "h": 2.0, "q.": 1.5, "q": 1.0,
}

DEFAULT_DURATION_WEIGHTS = {
    "ww": 0.08, "w.": 0.07, "w": 0.50, "h.": 0.09, "h": 0.13, "q.": 0.05, "q": 0.08,
}

GENRE_DURATION_WEIGHTS = {
    "pop_rock": {
        "ww": 0.02, "w.": 0.03, "w":  0.60, "h.": 0.05,
        "h":  0.20, "q.": 0.05, "q":  0.05,
    },
    "jazz": {
        "ww": 0.01, "w.": 0.01, "w":  0.30, "h.": 0.08,
        "h":  0.40, "q.": 0.05, "q":  0.15,
    },
    "classical": {
        "ww": 0.10, "w.": 0.05, "w":  0.40, "h.": 0.10,
        "h":  0.25, "q.": 0.05, "q":  0.05,
    },
    "folk": {
        "ww": 0.25, "w.": 0.10, "w":  0.45, "h.": 0.05,
        "h":  0.10, "q.": 0.02, "q":  0.03,
    }
}

_DEFAULT_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "distributions")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _weighted_sample(prob_dict: dict, rng: random.Random) -> str:
    """Draw a single key from `prob_dict` at random, weighted by its values."""
    keys    = list(prob_dict.keys())
    weights = list(prob_dict.values())
    return rng.choices(keys, weights=weights, k=1)[0]


def reweight_counts(counts: dict, beta: float) -> dict:
    """Reweight a {class: count} dict by count**beta, normalized to sum 1.

    beta=1.0 reproduces the original count-proportional distribution
    exactly. beta=0.0 produces a uniform distribution over the same
    (nonzero-count) support. Values in (0,1) interpolate smoothly --
    there is no cutoff; every class with count>0 keeps nonzero
    probability at every beta in [0,1].

    Args:
        counts: Mapping of class label to raw occurrence count.
        beta: Exponent in [0, 1]. 1.0 = faithful, 0.0 = uniform.

    Returns:
        Dict of the same nonzero-count keys mapping to normalized
        probabilities.
    """
    nonzero = {k: c for k, c in counts.items() if c > 0}
    if not nonzero:
        return {}
    if beta == 1.0:
        total = sum(nonzero.values())
        return {k: c / total for k, c in nonzero.items()}
    weighted = {k: c ** beta for k, c in nonzero.items()}
    total = sum(weighted.values())
    return {k: v / total for k, v in weighted.items()}


def _build_tone_pool(triad: str, seventh: str, ninth: str,
                     eleventh: str, thirteenth: str) -> list[int]:
    """Build the sorted set of semitone offsets (from the root) for a chord."""
    pool = list(TRIAD_TONES.get(triad, [0, 4, 7]))
    for token in (seventh, ninth, eleventh, thirteenth):
        semi = EXT_TO_SEMI.get(token)
        if semi is not None and semi not in pool:
            pool.append(semi)
    return sorted(set(pool))


def _smooth_bass(prev_abs_pc: int, curr_root_pc: int,
                 sampled_semi: int, bass_dist: dict) -> int:
    """Choose a bass semitone offset, deferring to voice-leading smoothness
    only when the sampled offset wasn't already the distribution's top pick.

    Unchanged from the corpus-anchored version: candidates are restricted
    to offsets `bass_dist` itself assigns nonzero probability to, and a
    dominant (argmax) sampled choice is never overridden by geometry
    alone. This keeps bass smoothing from manufacturing inversions the
    trained distribution has no support for -- see the inversion-inflation
    discussion this replaces.
    """
    def cost(a, b):
        diff = min(abs(a - b) % 12, 12 - abs(a - b) % 12)
        if diff == 0: return -2.0
        if diff <= 2: return -1.0
        if diff <= 4: return  0.0
        return diff * 0.5

    if not bass_dist:
        return sampled_semi

    dist = {int(k): v for k, v in bass_dist.items()}
    sampled_prob = dist.get(sampled_semi)
    if sampled_prob is None:
        return sampled_semi

    max_prob = max(dist.values())
    if sampled_prob >= max_prob:
        return sampled_semi

    best_semi  = sampled_semi
    best_cost  = cost(prev_abs_pc, (curr_root_pc + sampled_semi) % 12)
    for semi, prob in dist.items():
        if prob <= 0:
            continue
        c = cost(prev_abs_pc, (curr_root_pc + semi) % 12)
        if c < best_cost:
            best_cost, best_semi = c, semi
    return best_semi


def _reconstruct_harte(root_name: str, triad: str, bass_name: str,
                        seventh: str, ninth: str,
                        eleventh: str, thirteenth: str) -> str:
    """Reconstruct a Harte-style chord label from its component parts."""
    if (triad, seventh) in QUALITY_SHORTHAND:
        base = QUALITY_SHORTHAND[(triad, seventh)]
    else:
        fallback = {"major": "maj", "minor": "min", "diminished": "dim",
                    "augmented": "aug", "sus4": "sus4", "sus2": "sus2", "5": "5"}
        base = fallback.get(triad, "maj")

    exts = [t for t in (ninth, eleventh, thirteenth) if t != "N"]
    result = root_name + ":" + base
    if exts:
        result += "(" + ",".join(exts) + ")"
    if bass_name and bass_name != root_name:
        result += "/" + bass_name
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Extension chain: replaces the atomic joint-tuple draw
# ─────────────────────────────────────────────────────────────────────────────

class ExtensionChain:
    """Samples (seventh, ninth, eleventh, thirteenth) via 4 sequential
    conditional draws instead of one atomic joint-tuple draw, so each head
    can be reweighted independently while guaranteeing every sampled
    4-tuple is one that genuinely co-occurred in the training corpus.

    Built once per level-1 state from that state's joint extension-tuple
    counts (the same counts already present in level2_extensions_*.json).
    No new data is required: at sampling step k, the candidate set for
    head k is exactly "the set of values that appear in some attested
    tuple sharing the already-chosen prefix of heads 0..k-1," with counts
    aggregated across all such tuples. This makes it impossible to
    produce a combination with zero attested support, at any beta.
    """

    HEADS = ("seventh", "ninth", "eleventh", "thirteenth")

    def __init__(self, joint_tuple_counts: dict):
        """
        Args:
            joint_tuple_counts: Mapping of joint tuple string
                (e.g. "(b7,9,11,13)") to raw count, as found in a
                level2_extensions_<genre>.json "counts" entry for one
                level-1 state.
        """
        self._parsed = []
        for tup_str, count in joint_tuple_counts.items():
            tokens = tuple(tup_str.strip("()").split(","))
            self._parsed.append((tokens, count))

    def _candidates_for_prefix(self, prefix: tuple) -> dict:
        """Aggregate counts for the next head's values given a chosen prefix."""
        k = len(prefix)
        agg = defaultdict(int)
        for tokens, count in self._parsed:
            if tokens[:k] == prefix:
                agg[tokens[k]] += count
        return dict(agg)

    def sample(self, rng: random.Random, betas: dict) -> tuple[str, str, str, str]:
        """Sample one (seventh, ninth, eleventh, thirteenth) combination.

        Args:
            rng: Random source to draw from.
            betas: Dict mapping head name to that head's beta (see
                reweight_counts). Heads not present default to 1.0
                (faithful, no reweighting).

        Returns:
            A 4-tuple of token strings (e.g. ("b7", "9", "N", "N")),
            guaranteed to be a combination attested in this state's
            joint-tuple counts.
        """
        chosen = ()
        for head in self.HEADS:
            candidates = self._candidates_for_prefix(chosen)
            beta = betas.get(head, 1.0)
            probs = reweight_counts(candidates, beta)
            chosen = chosen + (_weighted_sample(probs, rng),)
        return chosen


# ─────────────────────────────────────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────────────────────────────────────

class ChordGenerator:
    def __init__(self, genre="pop_rock", tonic_pc=0, num_chords=16,
                 temperature=None, seed=None, dist_dir=_DEFAULT_DIST_DIR,
                 bpm=120, self_transition_discount=None,
                 duration_weights=None, beta_config=None):
        """
        temperature: softmax temperature applied to level-1 transition
            *probabilities* at generation time. If None, uses the genre's
            settled value from GENRES_TO_TEMPS. Composes with beta (which
            reshapes the underlying counts at load time) -- the two answer
            different questions and can be used together or independently.

        self_transition_discount: unchanged from the prior version; see
            GENRES_TO_SELF_TRANSITION_DISCOUNT.

        duration_weights: unchanged from the prior version.

        beta_config: dict mapping head name ("root_triad", "bass",
            "seventh", "ninth", "eleventh", "thirteenth") to a beta value
            in [0, 1] for THIS genre. If None, uses BETA_CONFIG[genre].
            Pass an explicit dict to override per-call without touching
            the module-level defaults. Any head omitted falls back to
            DEFAULT_BETA (1.0, faithful).
        """
        if genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {genre!r}")

        if isinstance(tonic_pc, str):
            note = tonic_pc[0].upper() + tonic_pc[1:]
            tonic_pc = NOTE_TO_PC.get(note)
            if tonic_pc is None:
                raise ValueError(f"Unrecognised tonic: {tonic_pc!r}")

        self.genre       = genre
        self.tonic_pc     = int(tonic_pc) % 12
        self.num_chords   = num_chords
        self.temperature  = float(
            temperature if temperature is not None else GENRES_TO_TEMPS.get(genre, 1.0)
        )
        self.bpm          = bpm
        self.rng          = random.Random(seed)

        self.self_transition_discount = float(
            self_transition_discount
            if self_transition_discount is not None
            else GENRES_TO_SELF_TRANSITION_DISCOUNT.get(genre, 1.0)
        )

        self.duration_weights = duration_weights if duration_weights is not None else GENRE_DURATION_WEIGHTS.get(genre, DEFAULT_DURATION_WEIGHTS)
        unknown = set(self.duration_weights) - set(DURATION_BEATS)
        if unknown:
            raise ValueError(f"Unknown duration token(s): {unknown}")

        self.beta_config = beta_config if beta_config is not None else BETA_CONFIG.get(genre, {})

        self._load_distributions(dist_dir)

    def _beta(self, head: str) -> float:
        """Look up this generator's beta for `head`, defaulting to faithful."""
        return self.beta_config.get(head, DEFAULT_BETA)

    def _load_distributions(self, dist_dir: str) -> None:
        """Load this generator's distributions and apply beta reweighting
        (root_triad, bass) plus self-transition discounting.

        Extension counts are kept as raw per-state joint-tuple dicts
        (not pre-reweighted) so that ExtensionChain can apply its own
        per-head betas conditionally at sample time -- reweighting them
        here, before the conditional decomposition, isn't possible since
        the per-head betas apply to conditional marginals that don't
        exist as a flat structure until the chain unrolls them.
        """
        def _load(name):
            path = os.path.join(dist_dir, f"{name}_{self.genre}.json")
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

        try:
            l1  = _load("level1_transitions")
            l2e = _load("level2_extensions")
            l2b = _load("level2_bass")
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Distribution file not found: {e}.")

        l1_counts = l1["counts"]
        l2b_counts = l2b["counts"]
        self._l2e_counts = l2e["counts"]   # raw joint-tuple counts, per state

        # root_triad: reweight each state's outgoing transition COUNTS by
        # beta, then renormalize. This replaces using the precomputed
        # "probabilities" field directly, since that field is always
        # beta=1.0 (faithful) by construction.
        beta_rt = self._beta("root_triad")
        self._l1 = {
            state: reweight_counts(targets, beta_rt)
            for state, targets in l1_counts.items()
        }

        # bass: same treatment, independent beta.
        beta_bass = self._beta("bass")
        self._l2b = {
            state: reweight_counts(targets, beta_bass)
            for state, targets in l2b_counts.items()
        }

        # Build one ExtensionChain per state, lazily reused across all
        # chords that land on that state.
        self._ext_chains = {
            state: ExtensionChain(tuples)
            for state, tuples in self._l2e_counts.items()
        }

        self._state_freq = {st: sum(targets.values()) for st, targets in l1_counts.items()}

        if self.self_transition_discount < 1.0:
            self._l1 = self._apply_self_transition_discount(
                self._l1, self.self_transition_discount
            )

    @staticmethod
    def _apply_self_transition_discount(transitions: dict, factor: float) -> dict:
        adjusted = {}
        for state, targets in transitions.items():
            if state not in targets:
                adjusted[state] = dict(targets)
                continue

            self_prob = targets[state]
            other_total = sum(p for t, p in targets.items() if t != state)

            if other_total <= 0:
                adjusted[state] = dict(targets)
                continue

            removed = self_prob * (1.0 - factor)
            new_targets = dict(targets)
            new_targets[state] = self_prob * factor
            for t, p in targets.items():
                if t != state:
                    new_targets[t] = p + removed * (p / other_total)

            adjusted[state] = new_targets

        return adjusted

    def _tonic_state(self) -> str:
        for candidate in ("0_major", "0_minor"):
            if candidate in self._l1:
                return candidate
        if self._state_freq:
            states  = list(self._state_freq.keys())
            weights = [self._state_freq[s] for s in states]
            return self.rng.choices(states, weights=weights, k=1)[0]
        return "0_major"

    def _apply_temperature(self, prob_dict: dict) -> dict:
        if self.temperature == 1.0:
            return prob_dict
        scaled = {k: v ** (1.0 / self.temperature) for k, v in prob_dict.items()}
        total  = sum(scaled.values())
        return {k: v / total for k, v in scaled.items()}

    def _level1_walk(self) -> list[str]:
        states, current = [], self._tonic_state()
        for _ in range(self.num_chords):
            states.append(current)
            transitions = self._l1.get(current)
            if not transitions:
                current = self._tonic_state()
                continue
            current = _weighted_sample(self._apply_temperature(transitions), self.rng)
        return states

    def _sample_duration_token(self) -> str:
        tokens  = list(self.duration_weights.keys())
        weights = list(self.duration_weights.values())
        return self.rng.choices(tokens, weights=weights, k=1)[0]

    def _sample_extensions(self, state: str) -> tuple[str, str, str, str]:
        """Sample (seventh, ninth, eleventh, thirteenth) for `state` via
        the conditional ExtensionChain, using this generator's per-head
        betas. Falls back to all-N if the state has no extension data
        at all (shouldn't normally happen for a state with any chords).
        """
        chain = self._ext_chains.get(state)
        if chain is None:
            return ("N", "N", "N", "N")
        betas = {
            "seventh":    self._beta("seventh"),
            "ninth":      self._beta("ninth"),
            "eleventh":   self._beta("eleventh"),
            "thirteenth": self._beta("thirteenth"),
        }
        return chain.sample(self.rng, betas)

    def generate(self) -> list[dict]:
        """Generate a full chord sequence for this generator's configuration."""
        seconds_per_beat = 60.0 / self.bpm
        current_time = 0.0

        raw, prev_bass_abs = [], None

        for state in self._level1_walk():
            interval_str, triad = state.rsplit("_", 1)
            root_pc = (int(interval_str) + self.tonic_pc) % 12

            dur_token     = self._sample_duration_token()
            chord_beats   = DURATION_BEATS[dur_token]
            chord_len_sec = seconds_per_beat * chord_beats

            seventh, ninth, eleventh, thirteenth = self._sample_extensions(state)

            bass_dist = self._l2b.get(state)
            bass_semi = int(_weighted_sample(bass_dist, self.rng)) if bass_dist else 0

            tone_pool = _build_tone_pool(triad, seventh, ninth, eleventh, thirteenth)
            if prev_bass_abs is not None and bass_dist:
                bass_semi = _smooth_bass(prev_bass_abs, root_pc, bass_semi, bass_dist)
            bass_abs_pc   = (root_pc + bass_semi) % 12
            prev_bass_abs = bass_abs_pc

            root_name = PC_TO_NOTE[root_pc]
            bass_name = PC_TO_NOTE[bass_abs_pc]
            harte = _reconstruct_harte(root_name, triad, bass_name,
                                       seventh, ninth, eleventh, thirteenth)

            raw.append({
                "time_start":     round(current_time, 4),
                "time_end":       round(current_time + chord_len_sec, 4),
                "duration_token": dur_token,
                "root":           root_name,
                "triad":          triad,
                "bass":           bass_name,
                "seventh":        seventh,
                "ninth":          ninth,
                "eleventh":       eleventh,
                "thirteenth":     thirteenth,
                "harte":          harte,
                "root_pc":        root_pc,
                "bass_abs_pc":    bass_abs_pc,
                "bass_semi":      bass_semi,
                "tone_pool":      tone_pool,
            })

            current_time += chord_len_sec

        return raw

    def to_lab_rows(self, chords: Optional[list[dict]] = None) -> list[str]:
        if chords is None:
            chords = self.generate()
        rows = []
        for c in chords:
            row = "\t".join([
                f"{c['time_start']:.4f}", f"{c['time_end']:.4f}",
                c["root"], c["triad"], c["bass"],
                c["seventh"], c["ninth"], c["eleventh"], c["thirteenth"],
                c["harte"],
            ])
            rows.append(row)
        return rows

    def write_lab(self, path: str, chords: Optional[list[dict]] = None) -> None:
        rows = self.to_lab_rows(chords)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# JFugue helpers
# ─────────────────────────────────────────────────────────────────────────────

def _midi_to_jfugue(midi: int) -> str:
    _NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{_NAMES[midi % 12]}{(midi // 12) - 1}"


def _chord_token(midi_notes: list[int], duration: str) -> str:
    """
    Build a JFugue simultaneous-note token that advances the cursor by
    exactly `duration`.

    JFugue 5 rule: when notes are joined by `+`, the cursor advances by
    the duration of the *last* note in the chain. Notes before the last
    must be written WITHOUT a duration suffix so they share the last
    note's clock tick:

        C5+E5+G5w   <- cursor advances by one whole note

    Putting a duration suffix on every note (e.g. "C5w+E5w+G5w") makes
    JFugue advance the cursor once per repeated suffix, which silently
    desyncs this voice against any other voice playing concurrently --
    not audible with a single voice, but it accumulates across a song
    once multiple voices need to stay tempo-locked (as in
    MultitrackComposerVariable).
    """
    if not midi_notes:
        return f"R{duration}"
    tokens = [_midi_to_jfugue(m) for m in midi_notes]
    return "+".join(tokens[:-1] + [tokens[-1] + duration])


# ─────────────────────────────────────────────────────────────────────────────
# MultitrackComposerVariable
# ─────────────────────────────────────────────────────────────────────────────

class MultitrackComposerVariable:
    """
    Translates a chord sequence (with per-chord variable durations) into a
    multi-voice JFugue Staccato string. Five voices: chord pad, bass,
    upper extensions, arpeggiator, and GM percussion.

    Voice layout
    ────────────
    V0  Chord pad      — block chords, mid-register (oct 4-5), held for
                         the chord's full duration_token
    V1  Bass           — single note, low register (oct 2-3), held for
                         the chord's full duration_token
    V2  Extensions     — stack of 9th/11th/13th tones, upper register
                         (oct 5-6); rest when none are present
    V3  Arpeggiator    — sixteenth-note pattern, SCALED to the chord's
                         actual duration (see _slots_for_chord) rather
                         than a fixed 16-slot bar -- a half-note chord
                         gets 8 slots, a dotted-whole gets 24, etc., so
                         the arp voice never desyncs from the pad/bass
                         voices under variable chord lengths.
    V9  Percussion     — kick/snare pattern, also slot-scaled per chord
                         (see _measure_drums), preserving the same
                         "kick on odd quarter-beats, snare on even
                         quarter-beats" backbone at any chord length.

    Every voice begins with T{bpm} V{n} I{instrument} so all voices share
    the same tempo and the instrument is set before any note data.
    """

    _INST_PAD  = [0, 4, 16, 48, 49, 54, 88]
    _INST_BASS = [32, 33, 34, 38, 43]
    _INST_EXT  = [4, 5, 8, 11, 12]
    _INST_ARP  = [73, 75, 26, 66, 80]

    _KICK   = "BASS_DRUM"
    _SNARE  = "ACOUSTIC_SNARE"
    _HIHAT  = "CLOSED_HI_HAT"
    _CRASH  = "CRASH_CYMBAL_1"

    def __init__(self, chords: list[dict], song_id: int, seed: Optional[int] = None):
        self.chords  = chords
        self.song_id = song_id
        rng = random.Random(seed if seed is not None else song_id)

        self.inst_pad  = rng.choice(self._INST_PAD)
        self.inst_bass = rng.choice(self._INST_BASS)
        self.inst_ext  = rng.choice(self._INST_EXT)
        self.inst_arp  = rng.choice(self._INST_ARP)

        self._rng = rng

    @staticmethod
    def _slots_for_chord(chord: dict) -> int:
        """Number of sixteenth-note slots spanning this chord's full duration.

        Every DURATION_BEATS value is an integer multiple of 0.25 beats
        (a sixteenth note), so this is always an exact integer -- no
        rounding, no partial slots, no desync between voices.
        """
        beats = DURATION_BEATS[chord.get("duration_token", "w")]
        return int(round(beats * 4))

    # ── per-chord builders ──────────────────────────────────────────────

    def _measure_pad(self, chord: dict) -> str:
        """V0: block chord (triad + optional 7th), held for the chord's
        full duration token. Smart-inversion rule unchanged from the
        single-voice version: a bass a semitone/major-seventh away from
        the root pushes the root up an octave so the bass can breathe.
        """
        root_pc      = chord["root_pc"]
        bass_pc      = chord["bass_abs_pc"]
        triad_semis  = list(TRIAD_TONES.get(chord["triad"], [0, 4, 7]))
        oct_base     = 60

        bass_rel = (bass_pc - root_pc) % 12
        if bass_rel in (1, 11) and len(triad_semis) >= 3:
            triad_semis = [triad_semis[1], triad_semis[2], triad_semis[0] + 12]

        seventh_semi = EXT_TO_SEMI.get(chord.get("seventh", "N"))
        if seventh_semi is not None:
            triad_semis.append(seventh_semi)

        midi_notes = [oct_base + root_pc + s for s in sorted(set(triad_semis))]
        dur = chord.get("duration_token", "w")
        return _chord_token(midi_notes, dur)

    def _measure_bass(self, chord: dict) -> str:
        """V1: single bass note, octave 2-3, held for the chord's full
        duration token."""
        root_pc   = chord["root_pc"]
        bass_pc   = chord["bass_abs_pc"]
        bass_semi = chord["bass_semi"]
        oct_base  = 36

        bass_midi = oct_base + bass_pc
        if bass_pc > root_pc and bass_semi > 7:
            bass_midi -= 12
        while bass_midi > 55: bass_midi -= 12
        while bass_midi < 24: bass_midi += 12

        dur = chord.get("duration_token", "w")
        return f"{_midi_to_jfugue(bass_midi)}{dur}"

    def _measure_ext(self, chord: dict) -> str:
        """V2: stack of extension notes (9th/11th/13th), octave 5-6.
        Rests for the chord's full duration if none are present."""
        root_pc  = chord["root_pc"]
        oct_base = 72
        dur = chord.get("duration_token", "w")

        ext_midis = []
        for key in ("ninth", "eleventh", "thirteenth"):
            semi = EXT_TO_SEMI.get(chord.get(key, "N"))
            if semi is not None:
                ext_midis.append(oct_base + root_pc + semi)

        if not ext_midis:
            return f"R{dur}"
        return _chord_token(sorted(ext_midis), dur)

    def _measure_arp(self, chord: dict) -> str:
        """V3: sixteenth-note arpeggio pattern, scaled to this chord's
        actual duration (see _slots_for_chord) rather than a fixed
        16-slot bar. 80% chord-tone / 20% non-chord-tone (NCT). Rests on
        slot-0-of-each-quarter-beat with 60% probability, same ratio as
        the original fixed-bar version, just re-applied at every quarter
        boundary regardless of how many quarters this chord spans.
        """
        root_pc   = chord["root_pc"]
        tone_pool = chord["tone_pool"]
        nct_pool  = [s for s in range(12) if s not in tone_pool]
        oct_base  = 72

        n_slots = self._slots_for_chord(chord)
        tokens = []
        for slot in range(n_slots):
            if slot % 4 == 0 and self._rng.random() < 0.6:
                tokens.append("Rs")
            elif self._rng.random() < 0.80 or not nct_pool:
                semi = self._rng.choice(tone_pool)
                midi = oct_base + root_pc + semi
                tokens.append(f"{_midi_to_jfugue(midi)}s")
            else:
                semi = self._rng.choice(nct_pool)
                midi = oct_base + root_pc + semi
                tokens.append(f"{_midi_to_jfugue(midi)}s")

        return " ".join(tokens)

    def _measure_drums(self, chord: dict, is_first_chord: bool) -> str:
        """V9: kick/snare/hihat pattern, scaled to this chord's actual
        duration (see _slots_for_chord). Preserves the original's
        "kick on odd quarter-beats (1,3,...), snare on even quarter-beats
        (2,4,...)" backbone at any chord length -- a quarter-note chord
        gets a single kick, a dotted-half gets kick-snare-kick, a full
        bar (whole note) reproduces the original fixed pattern exactly.
        Hi-hat fills every other slot, with occasional rests, same as
        the original.

        `is_first_chord` controls whether a leading crash is prepended
        (only ever on the very first chord of the song, matching the
        original's "bar 0 gets an extra leading crash" behavior).
        """
        n_slots = self._slots_for_chord(chord)
        n_quarters = max(1, n_slots // 4)

        tokens = []
        if is_first_chord:
            tokens.append(f"[{self._CRASH}]s")

        for slot in range(n_slots):
            quarter_idx = slot // 4
            is_quarter_downbeat = (slot % 4 == 0)
            if is_quarter_downbeat and quarter_idx % 2 == 0:
                tokens.append(f"[{self._KICK}]s")
            elif is_quarter_downbeat and quarter_idx % 2 == 1:
                tokens.append(f"[{self._SNARE}]s")
            else:
                if self._rng.random() < 0.15:
                    tokens.append("Rs")
                else:
                    tokens.append(f"[{self._HIHAT}]s")

        return " ".join(tokens)

    # ── public API ────────────────────────────────────────────────────

    def render_to_jfugue(self, bpm: int) -> str:
        """Build the full multi-voice JFugue Staccato string.

        Every voice starts with T{bpm} so JFugue uses the same tempo when
        computing absolute tick positions across voices.
        """
        voice_configs = [
            (0, self.inst_pad,  self._measure_pad),
            (1, self.inst_bass, self._measure_bass),
            (2, self.inst_ext,  self._measure_ext),
            (3, self.inst_arp,  self._measure_arp),
        ]

        voice_strings = []
        for v_num, inst, measure_fn in voice_configs:
            parts = [f"T{bpm}", f"V{v_num}", f"I{inst}"]
            for chord in self.chords:
                parts.append(measure_fn(chord))
            voice_strings.append(" ".join(parts))

        drum_parts = [f"T{bpm}", "V9"]
        for i, chord in enumerate(self.chords):
            drum_parts.append(self._measure_drums(chord, is_first_chord=(i == 0)))
        voice_strings.append(" ".join(drum_parts))

        return "  ".join(voice_strings)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="Chord sequence generator")

    p.add_argument("--genre",       default="pop_rock", choices=VALID_GENRES)
    p.add_argument("--tonic",       default="C",
                   help="Tonic note (C, F#, Bb …) or integer 0-11")
    p.add_argument("--num",         type=int, default=48,
                   help="Chords per song")
    p.add_argument("--bpm",         type=int, default=120)

    p.add_argument("--debug", action="store_true",
                   help="Shortcut for --random-genre --random-tonic "
                        "--random-num --random-bpm all at once")
    p.add_argument("--random-genre", action="store_true")
    p.add_argument("--random-tonic", action="store_true")
    p.add_argument("--random-num",   action="store_true")
    p.add_argument("--random-num-range", default="10,50")
    p.add_argument("--random-bpm",   action="store_true")
    p.add_argument("--random-bpm-range", default="60,180")

    p.add_argument("--temperature", type=float, default=None,
                   help="Softmax temperature for level-1 transitions. "
                        "Default: uniform per-genre value from "
                        "GENRES_TO_TEMPS (not re-rolled per song).")
    p.add_argument("--self-transition-discount", type=float, default=None)

    p.add_argument("--songs",       type=int, default=5)
    p.add_argument("--seed",        type=int, default=None)
    p.add_argument("--dist-dir",    default=_DEFAULT_DIST_DIR)
    p.add_argument("--out-dir",     default="gen/labels")
    p.add_argument("--scores-file", default="gen/generated_scores.txt")
    return p.parse_args()


def _parse_range(text: str) -> tuple[int, int]:
    lo_str, hi_str = text.split(",")
    lo, hi = int(lo_str), int(hi_str)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


if __name__ == "__main__":
    args = _parse_args()

    if args.debug:
        args.random_genre = True
        args.random_tonic = True
        args.random_num   = True
        args.random_bpm   = True

    try:
        fixed_tonic = int(args.tonic)
    except ValueError:
        fixed_tonic = args.tonic

    num_lo, num_hi = _parse_range(args.random_num_range)
    bpm_lo, bpm_hi = _parse_range(args.random_bpm_range)

    OUTPUT_TXT_FILE = args.scores_file
    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(os.path.dirname(OUTPUT_TXT_FILE), exist_ok=True)

    print(f"Starting batch generation of {args.songs} songs...")
    if args.debug:
        print("Debug mode: randomizing genre, tonic, chord count, and bpm per song.")
    print(f"BPM: {'random ' + str((bpm_lo, bpm_hi)) if args.random_bpm else args.bpm} | "
          f"Genre: {'random' if args.random_genre else args.genre.upper()}")

    with open(OUTPUT_TXT_FILE, "w") as f:
        for i in range(args.songs):
            print(f"  -> Generating song {i}...")

            song_genre = random.choice(VALID_GENRES) if args.random_genre else args.genre
            song_tonic = random.randint(0, 11) if args.random_tonic else fixed_tonic
            song_num   = random.randint(num_lo, num_hi) if args.random_num else args.num
            song_bpm   = random.randint(bpm_lo, bpm_hi) if args.random_bpm else args.bpm

            gen = ChordGenerator(
                genre       = song_genre,
                tonic_pc    = song_tonic,
                num_chords  = song_num,
                temperature = args.temperature,
                bpm         = song_bpm,
                seed        = args.seed + i if args.seed is not None else None,
                dist_dir    = args.dist_dir,
                self_transition_discount = args.self_transition_discount,
            )
            chords = gen.generate()

            lab_path = os.path.join(args.out_dir, f"SONG_{i}.lab")
            gen.write_lab(lab_path, chords)

            jfugue_str = MultitrackComposerVariable(
                chords, song_id=i, seed=args.seed + i if args.seed is not None else None
            ).render_to_jfugue(bpm=song_bpm)

            f.write(f"START_SONG_{i}\n")
            f.write(jfugue_str + "\n")
            f.write("END_SONG\n")

    print(f"\nSuccess! Wrote rendering instructions to {OUTPUT_TXT_FILE}")
    print("Ready to run HumanizedMidiRenderer.java.")