"""
generator.py — Phase 2: Synthetic Chord Sequence Generator
===========================================================
Consumes the Phase 1 JSON distributions and produces fully-formed
ChordFormer 10-column .lab annotation sequences in Harte syntax.

Pipeline per generation:
  1. Load genre-conditioned Level 1 / Level 2 distributions
  2. Level 1 Markov walk  → (root_interval, triad) skeleton
  3. Level 2 sampler      → extensions + raw bass interval (root-relative)
  4. Algorithmic Bassist  → voice-leading smoother over dynamic tone pool
  5. Absolute pitch translation + Harte string reconstruction
  6. 10-column .lab row formatting

Usage (module):
    from generator import ChordGenerator
    gen = ChordGenerator(genre="jazz", tonic_pc=5, num_chords=32, temperature=1.2)
    rows = gen.generate()          # list of 10-column strings
    gen.write_lab("output.lab")

Usage (CLI):
    python generator.py --genre jazz --tonic F --num 32 --temperature 1.2 --out out.lab
    python generator.py --genre pop_rock --tonic C --num 16 --seed 42
"""

import json
import os
import random
import argparse
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Pitch-class → preferred note name (flats preferred, matching extraction script)
PC_TO_NOTE = {
    0: "C",  1: "Db", 2: "D",  3: "Eb", 4: "E",  5: "F",
    6: "F#", 7: "G",  8: "Ab", 9: "A", 10: "Bb", 11: "B",
}

NOTE_TO_PC = {
    "C": 0,  "B#": 0,
    "C#": 1, "Db": 1,
    "D": 2,
    "D#": 3, "Eb": 3,
    "E": 4,  "Fb": 4,
    "F": 5,  "E#": 5,
    "F#": 6, "Gb": 6,
    "G": 7,
    "G#": 8, "Ab": 8,
    "A": 9,
    "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

# Triad class → semitone intervals above root (root-relative pool seeds)
TRIAD_TONES = {
    "major":      [0, 4, 7],
    "minor":      [0, 3, 7],
    "diminished": [0, 3, 6],
    "augmented":  [0, 4, 8],
    "sus4":       [0, 5, 7],
    "sus2":       [0, 2, 7],
    "5":          [0, 7],
}

# Extension token → semitones above root
EXT_TO_SEMI = {
    "7":   11,
    "b7":  10,
    "bb7":  9,
    "b9":   1,
    "#9":   3,
    "9":    2,
    "11":   5,
    "#11":  6,
    "b13":  8,
    "13":   9,
}

# (triad, seventh) → canonical Harte quality shorthand
# The extensions (9th, 11th, 13th) are appended separately as paren tokens.
QUALITY_SHORTHAND = {
    ("major",      "N"):   "maj",
    ("major",      "b7"):  "7",
    ("major",      "7"):   "maj7",
    ("major",      "bb7"): "maj",       # theoretically odd; graceful fallback
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

# Default distributions root for when the genre JSONs live alongside this file
_DEFAULT_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "distributions")


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pc_distance(a: int, b: int) -> int:
    """Shortest semitone distance between two pitch classes (0-11). Max = 6."""
    diff = abs(a - b) % 12
    return min(diff, 12 - diff)


def _build_tone_pool(triad: str, seventh: str, ninth: str,
                     eleventh: str, thirteenth: str) -> list[int]:
    """
    Build the full set of root-relative semitone offsets that are
    harmonically active in this chord. Used by the voice-leading smoother
    to populate the legal bass-note fallback candidates.
    """
    pool = list(TRIAD_TONES.get(triad, [0, 4, 7]))
    for token in (seventh, ninth, eleventh, thirteenth):
        semi = EXT_TO_SEMI.get(token)
        if semi is not None and semi not in pool:
            pool.append(semi)
    return sorted(set(pool))


def _bass_cost(prev_abs_pc: int, curr_abs_pc: int) -> float:
    """
    Voice-leading cost function.
    Negative = reward, positive = penalty.
    """
    dist = _pc_distance(prev_abs_pc, curr_abs_pc)
    if dist == 0:
        return -2.0   # pedal point / common tone: heavy reward
    if dist <= 2:
        return -1.0   # stepwise motion: reward
    if dist <= 4:
        return  0.0   # 3rd / 4th: neutral
    return dist * 0.5  # leap > 4th: proportional penalty


def _smooth_bass(prev_abs_pc: int, curr_root_pc: int,
                 sampled_semi: int, tone_pool: list[int]) -> int:
    """
    If the empirically sampled bass note causes a penalised leap from the
    previous bass note, select the tone-pool alternative with the lowest cost.
    If the sampled note is already good (cost ≤ 0), preserve it unchanged —
    never overwrite a well-voiced empirical sample.

    Returns the best root-relative semitone offset for the bass note.
    """
    sampled_abs  = (curr_root_pc + sampled_semi) % 12
    current_cost = _bass_cost(prev_abs_pc, sampled_abs)

    if current_cost <= 0:
        return sampled_semi   # empirical sample is fine; keep it

    best_semi = sampled_semi
    best_cost = current_cost
    for semi in tone_pool:
        abs_pc = (curr_root_pc + semi) % 12
        cost   = _bass_cost(prev_abs_pc, abs_pc)
        if cost < best_cost:
            best_cost = cost
            best_semi = semi

    return best_semi


def _reconstruct_harte(root_name: str, triad: str, bass_name: str,
                        seventh: str, ninth: str,
                        eleventh: str, thirteenth: str) -> str:
    """
    Reconstruct a valid Harte shorthand string from the 6D tuple.
    Examples:
        G, major, G,  b7,  b9,  N,   N    →  G:7(b9)
        C, major, E,  7,   9,   #11, N    →  C:maj7(9,#11)/E
        B, dim,   Ab, bb7, N,   N,   N    →  B:dim7/Ab
        F, sus4,  F,  b7,  9,   N,   N    →  F:7sus4(9)
    """

    # Safely resolve base quality
    if (triad, seventh) in QUALITY_SHORTHAND:
        base = QUALITY_SHORTHAND[(triad, seventh)]
    else:
        # Fallback mapping if the specific triad+7th combo is statistically rare
        fallback_map = {
            "major": "maj", "minor": "min", "diminished": "dim",
            "augmented": "aug", "sus4": "sus4", "sus2": "sus2", "5": "5"
        }
        base = fallback_map.get(triad, "maj")
        
        # If it's a fallback, and a 7th exists, append it via parens later
        # to ensure the data isn't lost from the string
        if seventh not in ("N", "bb7"):
            ninth = seventh + "," + ninth if ninth != "N" else seventh

    # Append upper extensions in the canonical order: 9th, 11th, 13th
    exts = [t for t in (ninth, eleventh, thirteenth) if t != "N"]
    result = root_name + ":" + base
    if exts:
        result += "(" + ",".join(exts) + ")"

    # Slash bass only when it differs from the root
    if bass_name and bass_name != root_name:
        result += "/" + bass_name

    return result


def _weighted_sample(prob_dict: dict, rng: random.Random) -> str:
    """random.choices over a probability dict, using stored probabilities as weights."""
    keys    = list(prob_dict.keys())
    weights = list(prob_dict.values())
    return rng.choices(keys, weights=weights, k=1)[0]


# ─────────────────────────────────────────────────────────────────────────────
# Main generator class
# ─────────────────────────────────────────────────────────────────────────────

class ChordGenerator:
    """
    Hierarchical two-level HMM chord progression generator.

    Parameters
    ----------
    genre       : one of 'pop_rock', 'jazz', 'classical', 'folk'
    tonic_pc    : integer 0-11 (C=0 … B=11), or a note name string e.g. 'F#'
    num_chords  : length of the generated sequence
    temperature : float > 0; < 1 = more predictable, > 1 = more exploratory
    seed        : optional int for reproducibility
    dist_dir    : path to the distributions/ folder (default: alongside this file)
    bars_per_chord : seconds per chord in the timing stub (Phase 3 will replace)
    bpm         : BPM for bar-length calculation
    """

    def __init__(
        self,
        genre:         str            = "pop_rock",
        tonic_pc:      int | str      = 0,
        num_chords:    int            = 16,
        temperature:   float          = 1.0,
        seed:          Optional[int]  = None,
        dist_dir:      str            = _DEFAULT_DIST_DIR,
        bars_per_chord:int            = 1,
        bpm:           int            = 120,
    ):
        if genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {genre!r}")

        # Resolve tonic
        if isinstance(tonic_pc, str):
            note = tonic_pc[0].upper() + tonic_pc[1:]
            tonic_pc = NOTE_TO_PC.get(note)
            if tonic_pc is None:
                raise ValueError(f"Unrecognised tonic note: {tonic_pc!r}")

        self.genre          = genre
        self.tonic_pc       = int(tonic_pc) % 12
        self.num_chords     = num_chords
        self.temperature    = float(temperature)
        self.bars_per_chord = bars_per_chord
        self.bpm            = bpm
        self.rng            = random.Random(seed)

        self._load_distributions(dist_dir)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_distributions(self, dist_dir: str) -> None:
        def _load(name):
            path = os.path.join(dist_dir, f"{name}_{self.genre}.json")
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

        try:
            l1  = _load("level1_transitions")
            l2e = _load("level2_extensions")
            l2b = _load("level2_bass")
        except FileNotFoundError as e:
            raise FileNotFoundError(
                f"Distribution file not found: {e}. "
                f"Run extract_distributions.py first to populate {dist_dir}/"
            )

        # Use probabilities, not raw counts
        self._l1  = l1 ["probabilities"]
        self._l2e = l2e["probabilities"]
        self._l2b = l2b["probabilities"]

        # Pre-compute global state frequency for initial-state sampling
        # (sum of all outgoing counts as a proxy for visit frequency)
        l1_counts = l1["counts"]
        self._state_freq = {
            st: sum(targets.values()) for st, targets in l1_counts.items()
        }

    # ── Level 1: Markov walk ─────────────────────────────────────────────────

    def _tonic_state(self) -> str:
        """
        Return the most natural starting state for this genre.
        For pop/rock/folk: always the tonic major/minor (interval=0).
        For jazz/classical: sample from global visit frequencies —
        many jazz tunes open on a ii or IV chord.
        """
        if self.genre in ("pop_rock", "folk"):
            # Prefer tonic major; fall back to tonic minor if not in matrix
            for candidate in ("0_major", "0_minor"):
                if candidate in self._l1:
                    return candidate
        # Jazz / classical: frequency-weighted sample over all states
        if self._state_freq:
            states  = list(self._state_freq.keys())
            weights = [self._state_freq[s] for s in states]
            return self.rng.choices(states, weights=weights, k=1)[0]
        return "0_major"

    def _apply_temperature(self, prob_dict: dict) -> dict:
        """Re-weight probabilities with temperature scaling."""
        if self.temperature == 1.0:
            return prob_dict
        scaled = {k: v ** (1.0 / self.temperature) for k, v in prob_dict.items()}
        total  = sum(scaled.values())
        return {k: v / total for k, v in scaled.items()}

    def _level1_walk(self) -> list[str]:
        """
        Perform the Level 1 Markov random walk.
        Returns a list of num_chords state strings, e.g. ['0_major', '5_major', …].
        Dead ends trigger a jump back to the tonic state.
        """
        states = []
        current = self._tonic_state()

        for _ in range(self.num_chords):
            states.append(current)
            transitions = self._l1.get(current)

            if not transitions:
                # Dead-end: jump to tonic
                current = self._tonic_state()
                continue

            tempered    = self._apply_temperature(transitions)
            current     = _weighted_sample(tempered, self.rng)

        return states

    # ── Level 2: extension + bass sampling ───────────────────────────────────

    def _sample_extensions(self, state: str) -> tuple[str, str, str, str]:
        """
        Sample a (seventh, ninth, eleventh, thirteenth) tuple from the
        Level 2 extension distribution for this state.
        Fallback: all 'N' (plain triad, no extensions).
        """
        ext_dist = self._l2e.get(state)
        if not ext_dist:
            return ("N", "N", "N", "N")

        raw = _weighted_sample(ext_dist, self.rng)
        # Stored as "(b7,9,N,N)" — strip parens, split on comma
        tokens = raw.strip("()").split(",")
        while len(tokens) < 4:
            tokens.append("N")
        return tuple(tokens[:4])   # (seventh, ninth, eleventh, thirteenth)

    def _sample_bass(self, state: str) -> int:
        """
        Sample a root-relative bass semitone from the Level 2 bass distribution.
        Fallback: 0 (root position).
        """
        bass_dist = self._l2b.get(state)
        if not bass_dist:
            return 0
        return int(_weighted_sample(bass_dist, self.rng))

    # ── Step 4: Algorithmic Bassist (voice-leading smoother) ─────────────────

    def _smooth_sequence(
        self,
        chords: list[dict],
    ) -> list[dict]:
        """
        Post-generation voice-leading pass.

        For each chord, the sampled bass note is accepted unchanged if it
        creates ≤ neutral voice-leading from the previous bass note.
        If it creates a penalised leap (> 4th), the smoother searches the
        full dynamic tone pool (triad tones + all active extensions) for the
        nearest alternative.

        The first chord is never altered (no previous context).
        """
        smoothed = []
        prev_bass_abs = None

        for chord in chords:
            root_pc    = chord["root_pc"]
            sampled    = chord["bass_semi"]   # root-relative
            tone_pool  = chord["tone_pool"]

            if prev_bass_abs is None:
                # First chord: accept the empirical sample unconditionally
                best_semi = sampled
            else:
                best_semi = _smooth_bass(prev_bass_abs, root_pc, sampled, tone_pool)

            prev_bass_abs = (root_pc + best_semi) % 12
            smoothed.append({**chord, "bass_semi": best_semi,
                              "bass_abs_pc": prev_bass_abs})

        return smoothed

    # ── Step 5: Absolute pitch translation + Harte reconstruction ────────────

    def _translate(self, chords: list[dict]) -> list[dict]:
        """
        Convert every root/bass from relative integers to note names
        and reconstruct the Harte string.
        """
        translated = []
        seconds_per_bar = (60.0 / self.bpm) * 4   # 4/4 assumed; Phase 3 will refine
        bar_len         = seconds_per_bar * self.bars_per_chord

        for i, chord in enumerate(chords):
            root_pc   = chord["root_pc"]
            bass_pc   = chord["bass_abs_pc"]

            root_name = PC_TO_NOTE[root_pc]
            bass_name = PC_TO_NOTE[bass_pc]

            seventh    = chord["seventh"]
            ninth      = chord["ninth"]
            eleventh   = chord["eleventh"]
            thirteenth = chord["thirteenth"]
            triad      = chord["triad"]

            harte = _reconstruct_harte(
                root_name, triad, bass_name,
                seventh, ninth, eleventh, thirteenth
            )

            time_start = round(i       * bar_len, 4)
            time_end   = round((i + 1) * bar_len, 4)

            translated.append({
                "time_start": time_start,
                "time_end":   time_end,
                "root":       root_name,
                "triad":      triad,
                "bass":       bass_name,
                "seventh":    seventh,
                "ninth":      ninth,
                "eleventh":   eleventh,
                "thirteenth": thirteenth,
                "harte":      harte,
            })

        return translated

    # ── Public interface ──────────────────────────────────────────────────────

    def generate(self) -> list[dict]:
        """
        Run the full pipeline and return a list of chord dicts, each with keys:
          time_start, time_end, root, triad, bass, seventh, ninth,
          eleventh, thirteenth, harte
        """
        # Level 1: structural skeleton
        l1_states = self._level1_walk()

        # Level 2: extensions + raw bass
        raw_chords = []
        for state in l1_states:
            interval_str, triad = state.rsplit("_", 1)
            root_interval = int(interval_str)
            root_pc       = (root_interval + self.tonic_pc) % 12

            seventh, ninth, eleventh, thirteenth = self._sample_extensions(state)
            bass_semi = self._sample_bass(state)

            tone_pool = _build_tone_pool(triad, seventh, ninth, eleventh, thirteenth)

            raw_chords.append({
                "state":       state,
                "root_interval": root_interval,
                "root_pc":     root_pc,
                "triad":       triad,
                "seventh":     seventh,
                "ninth":       ninth,
                "eleventh":    eleventh,
                "thirteenth":  thirteenth,
                "bass_semi":   bass_semi,
                "tone_pool":   tone_pool,
                # placeholder abs_pc; smoother will set the real value
                "bass_abs_pc": (root_pc + bass_semi) % 12,
            })

        # Algorithmic Bassist: voice-leading smoother
        smoothed = self._smooth_sequence(raw_chords)

        # Absolute pitch translation + Harte reconstruction
        return self._translate(smoothed)

    def to_lab_rows(self, chords: Optional[list[dict]] = None) -> list[str]:
        """
        Format chords as 10-column tab-separated .lab rows.
        If chords is None, calls generate() internally.

        Format:
          time_start  time_end  root  triad  bass  7th  9th  11th  13th  harte
        """
        if chords is None:
            chords = self.generate()
        rows = []
        for c in chords:
            row = "\t".join([
                f"{c['time_start']:.4f}",
                f"{c['time_end']:.4f}",
                c["root"],
                c["triad"],
                c["bass"],
                c["seventh"],
                c["ninth"],
                c["eleventh"],
                c["thirteenth"],
                c["harte"],
            ])
            rows.append(row)
        return rows

    def write_lab(self, path: str, chords: Optional[list[dict]] = None) -> None:
        """Generate a sequence and write it to a .lab file."""
        rows = self.to_lab_rows(chords)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        print(f"Wrote {len(rows)} chords → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="ChordFormer Phase 2 Generator")
    p.add_argument("--genre",       default="pop_rock",
                   choices=VALID_GENRES)
    p.add_argument("--tonic",       default="C",
                   help="Tonic note name (C, F#, Bb …) or integer 0-11")
    p.add_argument("--num",         type=int, default=16,
                   help="Number of chords to generate")
    p.add_argument("--temperature", type=float, default=1.0,
                   help="Sampling temperature (< 1 = conservative, > 1 = exploratory)")
    p.add_argument("--bpm",         type=int, default=120)
    p.add_argument("--seed",        type=int, default=None)
    p.add_argument("--out",         default=None,
                   help="Output .lab file path (default: print to stdout)")
    p.add_argument("--dist-dir",    default=_DEFAULT_DIST_DIR,
                   help="Path to distributions/ folder")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # Resolve tonic: allow both note names and integers
    try:
        tonic = int(args.tonic)
    except ValueError:
        tonic = args.tonic

    gen = ChordGenerator(
        genre       = args.genre,
        tonic_pc    = tonic,
        num_chords  = args.num,
        temperature = args.temperature,
        bpm         = args.bpm,
        seed        = args.seed,
        dist_dir    = args.dist_dir,
    )

    chords = gen.generate()
    rows   = gen.to_lab_rows(chords)

    if args.out:
        gen.write_lab(args.out, chords)
    else:
        header = "\t".join([
            "time_start", "time_end", "root", "triad", "bass",
            "7th", "9th", "11th", "13th", "harte"
        ])
        print(header)
        for row in rows:
            print(row)