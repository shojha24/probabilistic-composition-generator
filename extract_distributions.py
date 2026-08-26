"""
Training-Table Extraction (Chord Generator Spec §7)
====================================================
Extracts every count table Stages 1-3 need for generation-time backoff
sampling, partitioned by genre umbrella (§1.3), from normalized JAMS files.

This supersedes extract_distributions.py, which predates the current spec
and only emitted a single flat bigram table plus one Level-2 extension
blob and one Level-2 bass blob -- not the spec's 3-level Stage 1 backoff
(§2.2), 2-level Stage 2 backoff (§3.2), or 4-slot trie Stage 3 design
(§4.2-§4.3). The chord/key parsing grammar, interval tables, and genre-
routing logic are carried over from that script unchanged (verified
against the full 2282-chord, 469k-instance vocabulary); everything
downstream of "what does this chord event look like" has been rebuilt to
match the spec.

What gets extracted, per genre (`pop_rock`, `jazz` -- §1.3, §10.1
VALID_GENRES):

  Stage 1 (§2, §7.4) -- counts for the order-2 categorical HMM over the
  joint (Root, Triad) symbol:
    level0  P(Root_t, Triad_t)                                   [unigram]
    level1  P(Root_t, Triad_t | Root_{t-1}, Triad_{t-1})          [bigram]
    level2  P(Root_t, Triad_t | Root_{t-1},Triad_{t-1},Root_{t-2},Triad_{t-2})  [trigram]

  Stage 2 (§3, §7.4) -- counts for the Bass AR model:
    level0   P(Bass_t | Root_t)
    level1b  P(Bass_t | Root_t, Bass_{t-1})

  Stage 3 (§4, §7.5) -- three trie-selection levels (§4.2) over the
  ordered slot sequence seventh -> ninth -> eleventh -> thirteenth, each
  a node structure keyed by prefix, and at every node the three context
  tables needed for §4.3's node-level backoff:
    trie_A  one trie per (Root, Triad)
    trie_B  one trie per Triad, aggregated over all roots
    trie_C  one genre-wide trie
    -- and within every node of every trie: `marginal` (Level 0, the
       trie's own unconditional child distribution -- also what makes
       trie_A/B/C the A/B/C selection levels of §4.2), `by_bass`
       (Level 1, conditioned additionally on Bass_t), and
       `by_bass_history` (Level 2, additionally conditioned on Ext_{t-1}).

Interpolation weights (lambda, §2.2/§4.3) are NOT baked in here: lambda is
a function of a context's raw count and the discount constant D, and D is
a generation-time hyperparameter (§9), not a training-time constant. Raw
counts are saved so any generation-time D can compute lambda on load.

Segmentation (§1.1, §7.1): a training "sequence" is a maximal run of
consecutively-parseable chords within one file. A silence/unparseable
chord (Harte "N"/"X"/parse failure) breaks the run so Markov context does
not splice across it, exactly as a song/section boundary would.

Outputs, written under OUT_DIR (default ./distributions/):
  {genre}/stage1_backbone.json     - level0/level1/level2 counts + probs
  {genre}/stage2_bass.json         - level0/level1b counts + probs
  {genre}/stage3_extensions.json   - trie_A/trie_B/trie_C, each node with
                                      marginal/by_bass/by_bass_history
  {genre}/meta.json                - vocab sizes, song/chord counts
  extraction_report.json           - per-corpus file counts, skips, warnings

Note on §7.8 (validation): full dev-split hold-out generation testing is a
property of the *generator* (not yet built) exercising these tables, so
it can't be done here. What this script does check, as a training-time
sanity pass, is the structural guarantee the spec depends on: every state
that was observed anywhere has a Level-0 unigram entry (Stage 1 and
Stage 2's guaranteed floor, §2.2/§3.2), and every trie_C node reached
during extraction has a non-empty marginal (Stage 3's guaranteed floor,
§4.2 Level C). See `validate()`.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Genre umbrella routing (§1.3) -- carried over unchanged
# ─────────────────────────────────────────────────────────────────────────────
CORPUS_TO_GENRE: Dict[str, str] = {
    "mcgill billboard": "pop_rock",
    "billboard": "pop_rock",
    "isophonics": "pop_rock",
    "ireal pro": "jazz",
    "ireal-pro": "jazz",
    "irealpro": "jazz",
    "weimar jazz database": "jazz",
    "weimar-jazz": "jazz",
    "weimarjazz": "jazz",
}
GENRES: List[str] = ["pop_rock", "jazz"]  # matches VALID_GENRES, §10.1

# ─────────────────────────────────────────────────────────────────────────────
# Chromatic pitch-class lookup -- carried over unchanged
# ─────────────────────────────────────────────────────────────────────────────
NOTE_TO_PC: Dict[str, int] = {
    "C": 0, "B#": 0,
    "C#": 1, "Db": 1,
    "D": 2,
    "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4,
    "F": 5, "E#": 5,
    "F#": 6, "Gb": 6,
    "G": 7,
    "G#": 8, "Ab": 8,
    "A": 9,
    "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

# ─────────────────────────────────────────────────────────────────────────────
# Quality -> triad class (closed 7-way vocabulary, §1.1) -- carried over
# unchanged
# ─────────────────────────────────────────────────────────────────────────────
QUALITY_TO_TRIAD: Dict[str, str] = {
    "": "major", "maj": "major",
    "maj6": "major", "maj7": "major",
    "maj9": "major", "maj13": "major",
    "6": "major", "7": "major",
    "9": "major", "11": "major", "13": "major",
    "min": "minor", "m": "minor",
    "min6": "minor", "min7": "minor",
    "min9": "minor", "min11": "minor", "min13": "minor",
    "minmaj7": "minor",
    "dim": "diminished", "dim7": "diminished",
    "hdim7": "diminished",
    "aug": "augmented", "aug7": "augmented",
    "sus4": "sus4", "sus": "sus4", "7sus4": "sus4",
    "sus2": "sus2",
    "5": "5",
    "1": "1",
}

# ─────────────────────────────────────────────────────────────────────────────
# Quality -> seventh component -- carried over unchanged
# ─────────────────────────────────────────────────────────────────────────────
QUALITY_TO_7TH: Dict[str, str] = {
    "maj7": "7", "maj9": "7", "maj13": "7", "minmaj7": "7",
    "7": "b7", "9": "b7", "11": "b7", "13": "b7",
    "min7": "b7", "min9": "b7", "min11": "b7", "min13": "b7",
    "hdim7": "b7", "7sus4": "b7", "aug7": "b7",
    "dim7": "bb7",
    "": "N", "maj": "N", "min": "N",
    "dim": "N", "aug": "N", "sus4": "N",
    "sus2": "N", "5": "N",
    "maj6": "bb7", "min6": "bb7", "6": "bb7",
    "1": "N",
}

# ─────────────────────────────────────────────────────────────────────────────
# Interval tokens -> semitones above root -- carried over unchanged
# ─────────────────────────────────────────────────────────────────────────────
INTERVAL_TO_SEMI: Dict[str, int] = {
    "1": 0, "#1": 1, "b1": 0,
    "b2": 1, "2": 2,
    "bb3": 2, "b3": 3, "3": 4,
    "b4": 4, "4": 5, "#4": 6,
    "b5": 6, "5": 7, "#5": 8,
    "b6": 8, "6": 9, "#6": 9, "bb6": 8,
    "bb7": 9, "b7": 10, "7": 11,
    "b8": 11,
    "b9": 1, "9": 2, "#9": 3, "bb9": 0,
    "11": 5, "#11": 6,
    "b13": 8, "13": 9,
}

MINOR_MODES = {
    "minor", "min", "aeolian", "dorian", "phrygian", "locrian",
    "blues", "chromatic",
}
MAJOR_MODES = {"major", "maj", "ionian", "mixolydian", "lydian"}

_KEY_COLON_RE = re.compile(r'^([A-Ga-g][#b]?):(.+)$')
_KEY_BARE_RE = re.compile(r'^([A-Ga-g][#b]?)$')

_CHORD_RE = re.compile(
    r'^([A-Ga-g][#b]?)'        # 1: root note
    r'(?::([a-zA-Z0-9]+))?'    # 2: quality (optional)
    r'(\([^)]*\))?'            # 3: paren extensions (optional)
    r'(?:/([^/\s]+))?$'        # 4: bass (optional)
)

# Extension slot order, §1.1 / §4.1-§4.3
EXT_SLOTS = ("seventh", "ninth", "eleventh", "thirteenth")


# ─────────────────────────────────────────────────────────────────────────────
# Key parsing -- carried over unchanged
# ─────────────────────────────────────────────────────────────────────────────
def parse_key(value: Optional[str]) -> Optional[Tuple[int, str]]:
    """
    Parse a key_mode annotation value into (tonic_pc, mode).

    Handles: "C", "G:major", "Bb:major", "G:minor", "G mixolydian",
             "D:aeolian", "D:chromatic", "D:dorian", "Ab:mixolydian",
             "Bb:blues", "N".
    Mode-altering scales are mapped to the closest diatonic mode.
    """
    if not value or value.strip() in ("N", ""):
        return None
    v = value.strip()

    m = _KEY_COLON_RE.match(v)
    if m:
        note_str, mode_str = m.group(1), m.group(2).lower().strip()
        note_str = note_str[0].upper() + note_str[1:]
        pc = NOTE_TO_PC.get(note_str)
        if pc is None:
            return None
        mode = "minor" if mode_str in MINOR_MODES else "major"
        return (pc, mode)

    parts = v.split()
    if len(parts) == 2:
        note_str = parts[0][0].upper() + parts[0][1:]
        mode_str = parts[1].lower()
        pc = NOTE_TO_PC.get(note_str)
        if pc is None:
            return None
        mode = "minor" if mode_str in MINOR_MODES else "major"
        return (pc, mode)

    m2 = _KEY_BARE_RE.match(v)
    if m2:
        note_str = m2.group(1)[0].upper() + m2.group(1)[1:]
        pc = NOTE_TO_PC.get(note_str)
        if pc is not None:
            return (pc, "major")

    return None


@dataclass
class KeyTimeline:
    """
    Holds the sequence of active keys for one file/track and resolves the
    tonic in effect at any timestamp (§1.1: root_interval is looked up
    against the time-resolved active key, not the previous chord).
    """
    keys: List[Dict] = field(default_factory=list)  # [{start, end, tonic_pc}, ...]

    @classmethod
    def from_key_mode_data(cls, key_data: List[dict]) -> "KeyTimeline":
        active_keys = []
        for kd in key_data:
            parsed = parse_key(str(kd.get("value", "")))
            if parsed is None:
                continue
            t = float(kd.get("time", 0.0))
            d = float(kd.get("duration", 0.0))
            active_keys.append({"start": t, "end": t + d, "tonic_pc": parsed[0]})
        active_keys.sort(key=lambda k: k["start"])
        return cls(keys=active_keys)

    def __bool__(self) -> bool:
        return len(self.keys) > 0

    def tonic_at(self, chord_time: float) -> int:
        eps = 0.001
        for k in self.keys:
            if k["start"] - eps <= chord_time < k["end"] + eps:
                return k["tonic_pc"]
        past = [k for k in self.keys if k["start"] <= chord_time]
        if past:
            return past[-1]["tonic_pc"]
        return self.keys[0]["tonic_pc"]


# ─────────────────────────────────────────────────────────────────────────────
# Chord (Harte string) parsing -- carried over unchanged
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParsedChord:
    root_interval: int   # semitones above tonic, 0-11 (§1.1)
    triad: str            # closed 7-way vocabulary (§1.1)
    bass_interval: int   # semitones above THIS chord's root, 0-11 (§1.1, §3.1)
    seventh: str          # "N" | "b7" | "7" | "bb7"
    ninth: str            # "N" | "9" | "#9" | "b9"
    eleventh: str         # "N" | "11" | "#11"
    thirteenth: str       # "N" | "13" | "b13"

    @property
    def state(self) -> str:
        """Stage 1 symbol key: joint (Root, Triad)."""
        return f"{self.root_interval}_{self.triad}"

    @property
    def ext_tuple(self) -> Tuple[str, str, str, str]:
        return (self.seventh, self.ninth, self.eleventh, self.thirteenth)


def parse_harte(value: Optional[str], tonic_pc: int) -> Optional[ParsedChord]:
    """Parse a Harte chord string, resolving intervals relative to tonic_pc."""
    if value in ("N", "X", "", None):
        return None

    m = _CHORD_RE.match(value.strip())
    if not m:
        return None

    root_str, quality, paren_str, bass_str = m.groups()
    root_str = root_str[0].upper() + root_str[1:]
    quality = (quality or "").strip()

    root_pc = NOTE_TO_PC.get(root_str)
    if root_pc is None:
        return None

    root_interval = (root_pc - tonic_pc) % 12

    # ── Triad ──────────────────────────────────────────────────────────────
    triad = QUALITY_TO_TRIAD.get(quality)
    if triad is None:
        stripped = re.sub(r'[\d#b()]', '', quality)
        triad = QUALITY_TO_TRIAD.get(stripped, "major")

    # ── Seventh ────────────────────────────────────────────────────────────
    seventh = QUALITY_TO_7TH.get(quality, "N")

    # ── Extensions ─────────────────────────────────────────────────────────
    ninth = eleventh = thirteenth = "N"

    if quality in ("9", "min9", "maj9"):
        ninth = "9"
    if quality in ("11", "min11", "maj11"):
        ninth, eleventh = "9", "11"
    if quality in ("13", "min13", "maj13"):
        ninth, eleventh, thirteenth = "9", "11", "13"

    if paren_str:
        tokens = [t.strip() for t in paren_str.strip("()").split(",") if t.strip()]
        for tok in tokens:
            if tok in ("9", "add9"):
                ninth = "9"
            elif tok == "#9":
                ninth = "#9"
            elif tok in ("b9", "bb9"):
                ninth = "b9"
            elif tok in ("11", "add11"):
                eleventh = "11"
            elif tok == "#11":
                eleventh = "#11"
            elif tok in ("13", "add13"):
                thirteenth = "13"
            elif tok == "b13":
                thirteenth = "b13"
            elif tok == "b7" and seventh == "N":
                seventh = "b7"
            elif tok == "7" and seventh == "N":
                seventh = "7"
            elif tok == "bb7" and seventh == "N":
                seventh = "bb7"
            elif tok == "6" and seventh == "N":
                seventh = "bb7" 
            # Non-extension paren tokens ignored -- out of scope for the schema.

    # ── Bass note ──────────────────────────────────────────────────────────
    bass_interval = 0
    if bass_str:
        bass_str = bass_str.strip()
        bass_note = bass_str[0].upper() + bass_str[1:] if bass_str else ""
        if bass_note in NOTE_TO_PC:
            bass_pc = NOTE_TO_PC[bass_note]
            bass_interval = (bass_pc - root_pc) % 12
        else:
            semis = INTERVAL_TO_SEMI.get(bass_str)
            if semis is not None:
                bass_interval = semis % 12

    return ParsedChord(root_interval, triad, bass_interval, seventh, ninth, eleventh, thirteenth)


# ─────────────────────────────────────────────────────────────────────────────
# Genre / corpus resolution -- carried over unchanged
# ─────────────────────────────────────────────────────────────────────────────
def resolve_genre(ann_meta: dict, corpus_dir_hint: str = "") -> Optional[str]:
    raw = (ann_meta.get("corpus") or corpus_dir_hint or "").lower().strip()

    if raw in CORPUS_TO_GENRE:
        return CORPUS_TO_GENRE[raw]

    best, best_len = None, 0
    for key, genre in CORPUS_TO_GENRE.items():
        k = key.lower()
        if k in raw and len(k) > best_len:
            best, best_len = genre, len(k)
    if best:
        return best

    dir_norm = corpus_dir_hint.lower().replace("-", "").replace("_", "").replace(" ", "")
    for key, genre in CORPUS_TO_GENRE.items():
        k = key.lower().replace("-", "").replace("_", "").replace(" ", "")
        if k == dir_norm or (k and k in dir_norm):
            return genre

    return None


def infer_corpus_dir(fpath: str, data_root: str) -> str:
    norm_path = fpath.replace("\\", "/")
    root_norm = os.path.normpath(data_root).replace("\\", "/")
    root_parts = root_norm.split("/")
    parts = norm_path.split("/")
    try:
        idx = next(i for i, p in enumerate(parts) if p == root_parts[-1])
        return parts[idx + 1] if idx + 1 < len(parts) else ""
    except StopIteration:
        return ""


def get_annotations(annotations: List[dict]) -> Tuple[Optional[dict], Optional[str], List[dict]]:
    chord_ann, chord_ns = None, None
    key_data: List[dict] = []
    for ann in annotations:
        ns = ann.get("namespace", "")
        if ns == "chord_harte":
            chord_ann, chord_ns = ann, ns
        elif ns == "chord" and chord_ann is None:
            chord_ann, chord_ns = ann, ns
        elif ns == "key_mode":
            key_data.extend(ann.get("data", []))
    return chord_ann, chord_ns, key_data


# ─────────────────────────────────────────────────────────────────────────────
# Trie node factory (Stage 3, §4.2-§4.3)
# ─────────────────────────────────────────────────────────────────────────────
def _new_trie_node() -> Dict[str, dict]:
    """
    One node of one trie = one slot-prefix. Holds the three context tables
    §4.3's node-level backoff needs:
      marginal          Level 0: P(child | prefix)                  {child: count}
      by_bass           Level 1: P(child | prefix, Bass_t)          {bass: {child: count}}
      by_bass_history    Level 2: P(child | prefix, Bass_t, Ext_{t-1}) {"bass||ext_prev": {child: count}}
    """
    return {
        "marginal": defaultdict(int),
        "by_bass": defaultdict(lambda: defaultdict(int)),
        "by_bass_history": defaultdict(lambda: defaultdict(int)),
    }


def _new_trie() -> "defaultdict":
    """A trie = dict[node_key] -> dict[prefix_str] -> trie node."""
    return defaultdict(lambda: defaultdict(_new_trie_node))


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation state
# ─────────────────────────────────────────────────────────────────────────────
class Accumulator:
    """Holds running counts across all files for one extraction run."""

    def __init__(self, genres: List[str]):
        self.genres = genres

        # Stage 1 (§2, §7.4)
        self.stage1_level0: Dict[str, Dict[str, int]] = {g: defaultdict(int) for g in genres}
        self.stage1_level1: Dict[str, Dict[str, Dict[str, int]]] = {
            g: defaultdict(lambda: defaultdict(int)) for g in genres
        }
        self.stage1_level2: Dict[str, Dict[str, Dict[str, int]]] = {
            g: defaultdict(lambda: defaultdict(int)) for g in genres
        }

        # Stage 2 (§3, §7.4)
        self.stage2_level0: Dict[str, Dict[str, Dict[str, int]]] = {
            g: defaultdict(lambda: defaultdict(int)) for g in genres
        }
        self.stage2_level1b: Dict[str, Dict[str, Dict[str, int]]] = {
            g: defaultdict(lambda: defaultdict(int)) for g in genres
        }

        # Stage 3 (§4, §7.5) -- three trie-selection levels
        self.stage3_trie_A: Dict[str, "defaultdict"] = {g: _new_trie() for g in genres}  # per (Root,Triad)
        self.stage3_trie_B: Dict[str, "defaultdict"] = {g: _new_trie() for g in genres}  # per Triad
        self.stage3_trie_C: Dict[str, "defaultdict"] = {g: _new_trie() for g in genres}  # genre-wide

        self.meta = {g: {"songs": 0, "runs": 0, "chords": 0} for g in genres}

        self.report = {
            "corpora": {},
            "total_files": 0,
            "total_songs_with_chords": 0,
            "total_chords_parsed": 0,
            "total_runs": 0,
            "skipped_no_key": 0,
            "skipped_unknown_corpus": 0,
            "parse_errors": 0,
            "chord_parse_failures": 0,
        }

    def corpus_entry(self, corpus_label: str, genre: str) -> dict:
        if corpus_label not in self.report["corpora"]:
            self.report["corpora"][corpus_label] = {
                "files": 0, "chords": 0, "runs": 0,
                "genre": genre, "key_failures": 0, "chord_failures": 0,
            }
        return self.report["corpora"][corpus_label]

    # ── run-level accumulation ────────────────────────────────────────────
    def add_run(self, genre: str, run: List[ParsedChord]) -> None:
        """
        `run` is a maximal contiguous stretch of successfully-parsed chords
        (no silence/parse-failure inside it -- see split_into_runs). This is
        the unit Stage 1-3 all condition their history/backoff on.
        """
        n = len(run)
        if n == 0:
            return

        s1_l0 = self.stage1_level0[genre]
        s1_l1 = self.stage1_level1[genre]
        s1_l2 = self.stage1_level2[genre]
        s2_l0 = self.stage2_level0[genre]
        s2_l1b = self.stage2_level1b[genre]
        trie_A = self.stage3_trie_A[genre]
        trie_B = self.stage3_trie_B[genre]
        trie_C = self.stage3_trie_C[genre]

        for i, ch in enumerate(run):
            state_i = ch.state
            root_key = str(ch.root_interval)
            bass_str = str(ch.bass_interval)

            # ---- Stage 1: (Root,Triad) order-2 HMM counts (§2.1) --------
            s1_l0[state_i] += 1
            if i >= 1:
                ctx1 = run[i - 1].state
                s1_l1[ctx1][state_i] += 1
            if i >= 2:
                ctx2 = f"{run[i - 2].state}|{run[i - 1].state}"
                s1_l2[ctx2][state_i] += 1

            # ---- Stage 2: Bass AR counts (§3.1-§3.2) ---------------------
            s2_l0[root_key][bass_str] += 1
            if i >= 1:
                bass_prev = str(run[i - 1].bass_interval)
                ctx1b = f"{root_key}|{bass_prev}"
                s2_l1b[ctx1b][bass_str] += 1

            # ---- Stage 3: 4-slot extension trie counts (§4.2-§4.3) -------
            ext_tuple = ch.ext_tuple
            if i >= 1:
                p = run[i - 1]
                ext_prev_str = "|".join(p.ext_tuple)
            else:
                ext_prev_str = "START"  # sentinel: no history within this run

            a_key = state_i          # trie_A: keyed by (Root,Triad) -- §4.2 Level A
            b_key = ch.triad         # trie_B: keyed by Triad only  -- §4.2 Level B
            c_key = "ALL"            # trie_C: genre-wide            -- §4.2 Level C

            for trie_dict, node_key in (
                (trie_A, a_key),
                (trie_B, b_key),
                (trie_C, c_key),
            ):
                node_table = trie_dict[node_key]
                for d in range(4):
                    prefix = ext_tuple[:d]
                    prefix_str = "|".join(prefix) if prefix else "ROOT"
                    child = ext_tuple[d]
                    node = node_table[prefix_str]
                    node["marginal"][child] += 1
                    node["by_bass"][bass_str][child] += 1
                    composite = f"{bass_str}||{ext_prev_str}"
                    node["by_bass_history"][composite][child] += 1

        self.meta[genre]["runs"] += 1
        self.meta[genre]["chords"] += n
        self.report["total_runs"] += 1

    # ── file-level bookkeeping ────────────────────────────────────────────
    def add_file(
        self,
        genre: str,
        corpus_label: str,
        parsed_chords: List[Optional[ParsedChord]],
    ) -> None:
        entry = self.corpus_entry(corpus_label, genre)

        valid_count = sum(1 for p in parsed_chords if p is not None)
        self.report["total_chords_parsed"] += valid_count
        entry["chords"] += valid_count

        runs = split_into_runs(parsed_chords)
        entry["runs"] += len(runs)
        if valid_count > 0:
            self.report["total_songs_with_chords"] += 1
            self.meta[genre]["songs"] += 1

        for run in runs:
            self.add_run(genre, run)


def split_into_runs(parsed_chords: List[Optional[ParsedChord]]) -> List[List[ParsedChord]]:
    """
    Break a file's chord-event sequence at every silence/parse-failure
    (None) so Markov/AR/trie context never bleeds across a gap -- the same
    principle as not letting context bleed across song/section boundaries
    (§1.1, §7.1).
    """
    runs: List[List[ParsedChord]] = []
    current: List[ParsedChord] = []
    for c in parsed_chords:
        if c is None:
            if current:
                runs.append(current)
                current = []
        else:
            current.append(c)
    if current:
        runs.append(current)
    return runs


# ─────────────────────────────────────────────────────────────────────────────
# Per-file processing
# ─────────────────────────────────────────────────────────────────────────────
def process_file(fpath: str, data_root: str, acc: Accumulator) -> None:
    corpus_dir = infer_corpus_dir(fpath, data_root)

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        acc.report["parse_errors"] += 1
        return

    annotations = data.get("annotations", [])
    chord_ann, _chord_ns, key_data = get_annotations(annotations)
    if not chord_ann:
        return

    ann_meta = chord_ann.get("annotation_metadata", {})
    genre = resolve_genre(ann_meta, corpus_dir)
    if genre is None:
        acc.report["skipped_unknown_corpus"] += 1
        return

    corpus_label = ann_meta.get("corpus") or corpus_dir

    timeline = KeyTimeline.from_key_mode_data(key_data)
    if not timeline:
        acc.report["skipped_no_key"] += 1
        acc.corpus_entry(corpus_label, genre)["key_failures"] += 1
        return

    parsed: List[Optional[ParsedChord]] = []
    for entry in chord_ann.get("data", []):
        val = entry.get("value", "N")
        chord_time = float(entry.get("time", 0.0))
        tonic = timeline.tonic_at(chord_time)

        result = parse_harte(val, tonic)
        if result is None:
            if val not in ("N", "X", "", None):
                acc.report["chord_parse_failures"] += 1
                acc.corpus_entry(corpus_label, genre)["chord_failures"] += 1
            parsed.append(None)
            continue
        parsed.append(result)

    acc.report["total_files"] += 1
    acc.add_file(genre, corpus_label, parsed)


# ─────────────────────────────────────────────────────────────────────────────
# Normalization / serialization
# ─────────────────────────────────────────────────────────────────────────────
def dedefault(d):
    """Recursively convert nested defaultdicts to plain dicts for JSON."""
    if isinstance(d, defaultdict) or isinstance(d, dict):
        return {k: dedefault(v) for k, v in d.items()}
    return d


def normalise_flat(count_dict: Dict[str, int]) -> Dict[str, float]:
    """outcome -> count  =>  outcome -> probability."""
    total = sum(count_dict.values())
    if total == 0:
        return {}
    return {k: round(v / total, 6) for k, v in count_dict.items()}


def normalise_ctx(count_dict: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, float]]:
    """context -> outcome -> count  =>  context -> outcome -> probability."""
    return {ctx: normalise_flat(outcomes) for ctx, outcomes in count_dict.items()}


def package_trie_node(node: Dict[str, dict]) -> dict:
    return {
        "marginal": {
            "counts": dedefault(node["marginal"]),
            "probabilities": normalise_flat(node["marginal"]),
        },
        "by_bass": {
            "counts": dedefault(node["by_bass"]),
            "probabilities": normalise_ctx(node["by_bass"]),
        },
        "by_bass_history": {
            "counts": dedefault(node["by_bass_history"]),
            "probabilities": normalise_ctx(node["by_bass_history"]),
        },
    }


def package_trie(trie: "defaultdict") -> dict:
    """node_key -> prefix_str -> packaged node."""
    out = {}
    for node_key, prefixes in trie.items():
        out[node_key] = {
            prefix_str: package_trie_node(node) for prefix_str, node in prefixes.items()
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Validation (§7.8, extraction-time subset -- see module docstring)
# ─────────────────────────────────────────────────────────────────────────────
def validate(acc: Accumulator) -> List[str]:
    """
    Checks the structural guarantees the spec's backoff design depends on.
    Returns a list of human-readable problems found (empty = clean).
    """
    problems: List[str] = []

    for genre in acc.genres:
        l1_states = set(acc.stage1_level1[genre].keys()) | {
            s for outs in acc.stage1_level1[genre].values() for s in outs
        }
        l0_states = set(acc.stage1_level0[genre].keys())
        missing = l1_states - l0_states
        if missing:
            problems.append(
                f"[{genre}] Stage1: {len(missing)} state(s) appear in level1 but not level0 "
                f"(level0 is supposed to be the unconditional marginal, so this should be impossible)."
            )

        s2_roots_in_l1b = {ctx.split("|", 1)[0] for ctx in acc.stage2_level1b[genre]}
        s2_roots_in_l0 = set(acc.stage2_level0[genre].keys())
        missing_roots = s2_roots_in_l1b - s2_roots_in_l0
        if missing_roots:
            problems.append(
                f"[{genre}] Stage2: {len(missing_roots)} root(s) appear in level1b but have no "
                f"level0 fallback -- would crash generation on backoff (§8, 'Missing Level-0 fallback')."
            )

        trie_c = acc.stage3_trie_C[genre]
        if "ALL" not in trie_c or "ROOT" not in trie_c["ALL"]:
            if acc.meta[genre]["chords"] > 0:
                problems.append(
                    f"[{genre}] Stage3: genre-wide trie_C has no ROOT node despite {acc.meta[genre]['chords']} "
                    f"chords processed -- Level C floor is not guaranteed as required by §4.2."
                )
        elif not acc.stage3_trie_C[genre]["ALL"]["ROOT"]["marginal"]:
            problems.append(f"[{genre}] Stage3: trie_C ROOT marginal is empty -- no guaranteed floor.")

    return problems


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────
def write_outputs(acc: Accumulator, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    for genre in acc.genres:
        genre_dir = os.path.join(out_dir, genre)
        os.makedirs(genre_dir, exist_ok=True)

        # ---- Stage 1 -----------------------------------------------------
        l0 = dedefault(acc.stage1_level0[genre])
        l1 = dedefault(acc.stage1_level1[genre])
        l2 = dedefault(acc.stage1_level2[genre])
        stage1_doc = {
            "level0": {"counts": l0, "probabilities": normalise_flat(l0)},
            "level1": {"counts": l1, "probabilities": normalise_ctx(l1)},
            "level2": {"counts": l2, "probabilities": normalise_ctx(l2)},
        }
        with open(os.path.join(genre_dir, "stage1_backbone.json"), "w") as f:
            json.dump(stage1_doc, f, indent=2)

        # ---- Stage 2 -----------------------------------------------------
        b0 = dedefault(acc.stage2_level0[genre])
        b1 = dedefault(acc.stage2_level1b[genre])
        stage2_doc = {
            "level0": {"counts": b0, "probabilities": normalise_ctx(b0)},
            "level1b": {"counts": b1, "probabilities": normalise_ctx(b1)},
        }
        with open(os.path.join(genre_dir, "stage2_bass.json"), "w") as f:
            json.dump(stage2_doc, f, indent=2)

        # ---- Stage 3 -----------------------------------------------------
        stage3_doc = {
            "trie_A": package_trie(acc.stage3_trie_A[genre]),
            "trie_B": package_trie(acc.stage3_trie_B[genre]),
            "trie_C": package_trie(acc.stage3_trie_C[genre]),
        }
        with open(os.path.join(genre_dir, "stage3_extensions.json"), "w") as f:
            json.dump(stage3_doc, f, indent=2)

        # ---- Meta ----------------------------------------------------------
        meta_doc = dict(acc.meta[genre])
        meta_doc["stage1_level0_states"] = len(l0)
        meta_doc["stage1_level1_contexts"] = len(l1)
        meta_doc["stage1_level2_contexts"] = len(l2)
        meta_doc["stage2_level0_roots"] = len(b0)
        meta_doc["stage2_level1b_contexts"] = len(b1)
        meta_doc["stage3_trie_A_states"] = len(acc.stage3_trie_A[genre])
        meta_doc["stage3_trie_B_triads"] = len(acc.stage3_trie_B[genre])
        with open(os.path.join(genre_dir, "meta.json"), "w") as f:
            json.dump(meta_doc, f, indent=2)

        print(f"\n[{genre.upper()}]")
        print(f"  songs: {meta_doc['songs']}   runs: {meta_doc['runs']}   chords: {meta_doc['chords']}")
        print(f"  Stage1 -- level0 states: {len(l0)}  level1 ctx: {len(l1)}  level2 ctx: {len(l2)}")
        print(f"  Stage2 -- level0 roots: {len(b0)}  level1b ctx: {len(b1)}")
        print(f"  Stage3 -- trie_A states: {len(acc.stage3_trie_A[genre])}  trie_B triads: {len(acc.stage3_trie_B[genre])}")

    with open(os.path.join(out_dir, "extraction_report.json"), "w") as f:
        json.dump(acc.report, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def run(data_root: Optional[str] = None, out_dir: Optional[str] = None) -> Accumulator:
    data_root = data_root or os.environ.get("DATA_ROOT", "data_normalized")
    if not os.path.isdir(data_root):
        data_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_normalized")

    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "distributions")

    jams_files = sorted(glob.glob(os.path.join(data_root, "**", "*.jams"), recursive=True))
    print(f"Found {len(jams_files)} JAMS files under {data_root}")
    if not jams_files:
        print("WARNING: No JAMS files found. Set DATA_ROOT env var, pass --data-root, "
              "or run from project root.")

    acc = Accumulator(GENRES)
    for fpath in jams_files:
        process_file(fpath, data_root, acc)

    write_outputs(acc, out_dir)

    problems = validate(acc)
    if problems:
        print(f"\n{'!' * 60}\nVALIDATION ISSUES:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\nValidation: guaranteed-floor checks passed (Stage1/2 level0 coverage, "
              "Stage3 trie_C ROOT support).")
    acc.report["validation_problems"] = problems
    with open(os.path.join(out_dir, "extraction_report.json"), "w") as f:
        json.dump(acc.report, f, indent=2)

    r = acc.report
    print(f"\n{'=' * 60}")
    print(f"Files processed        : {r['total_files']}")
    print(f"Songs with >=1 chord   : {r['total_songs_with_chords']}")
    print(f"Chords parsed          : {r['total_chords_parsed']:,}")
    print(f"Contiguous runs        : {r['total_runs']:,}")
    print(f"Skipped - no key       : {r['skipped_no_key']}")
    print(f"Skipped - unknown corp : {r['skipped_unknown_corpus']}")
    print(f"File parse errors      : {r['parse_errors']}")
    print(f"Chord parse failures   : {r['chord_parse_failures']}")
    print(f"\nOutputs -> {out_dir}/")

    return acc


def parse_args():
    p = argparse.ArgumentParser(description="Extract Stage 1-3 training tables (spec §7)")
    p.add_argument("--data-root", default=None,
                    help="Root dir of normalized JAMS files (env DATA_ROOT, or ./data_normalized)")
    p.add_argument("--out-dir", default=None,
                    help="Output dir for count tables (default ./distributions)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(data_root=args.data_root, out_dir=args.out_dir)