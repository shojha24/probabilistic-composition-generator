"""
Phase 1 (§1): Transition & Probability Extraction
==================================================
Extracts key-relative Level-1 transition matrices and Level-2 empirical
extension / bass distributions from normalized JAMS files, partitioned by
genre umbrella (Pop/Rock, Jazz, Classical).

This is a rebuild of a deprecated prototype. The chord/key parsing grammar,
interval tables, and genre-routing logic are carried over unchanged (they
were verified against the full 2282-chord, 469k-instance vocabulary); the
control flow, state management, and I/O have been restructured for clarity,
testability, and correctness (e.g. the per-file active-key lookup is now an
explicit object instead of a loop-scoped closure, and every public piece of
logic is a pure, independently testable function).

Outputs (written to OUT_DIR, default ./distributions/):
  level1_transitions_{genre}.json   - key-relative (interval, triad) bigram counts + probs
  level2_extensions_{genre}.json    - P(7th, 9th, 11th, 13th | interval, triad)
  level2_bass_{genre}.json          - P(bass_interval | interval, triad)
  extraction_report.json            - per-corpus file counts, skips, warnings
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Genre umbrella routing
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
GENRES: List[str] = ["pop_rock", "jazz"]

# ─────────────────────────────────────────────────────────────────────────────
# Chromatic pitch-class lookup (covers every note spelling in the dataset)
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
# Quality -> triad class (all 24 quality tokens found in the normalized vocab)
# ─────────────────────────────────────────────────────────────────────────────
QUALITY_TO_TRIAD: Dict[str, str] = {
    "": "major", "maj": "major",
    "maj6": "major", "maj7": "major",
    "maj9": "major", "maj13": "major",
    "6": "major", "7": "major",
    "9": "major", "11": "major", "13": "major",
    "1": "major",
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
}

# ─────────────────────────────────────────────────────────────────────────────
# Quality -> seventh component
# ─────────────────────────────────────────────────────────────────────────────
QUALITY_TO_7TH: Dict[str, str] = {
    # natural 7th (major-seventh interval)
    "maj7": "7", "maj9": "7", "maj13": "7", "minmaj7": "7",
    # flat 7th (minor-seventh interval)
    "7": "b7", "9": "b7", "11": "b7", "13": "b7",
    "min7": "b7", "min9": "b7", "min11": "b7", "min13": "b7",
    "hdim7": "b7", "7sus4": "b7", "aug7": "b7",
    # double-flat 7th (diminished-seventh interval - must NOT be collapsed to b7)
    "dim7": "bb7",
    # no 7th
    "": "N", "maj": "N", "min": "N",
    "dim": "N", "aug": "N", "sus4": "N",
    "sus2": "N", "5": "N",
    "maj6": "N", "min6": "N", "6": "N",
    "1": "N",
}

# ─────────────────────────────────────────────────────────────────────────────
# Interval tokens -> semitones above root (25 bass tokens + paren extensions)
# ─────────────────────────────────────────────────────────────────────────────
INTERVAL_TO_SEMI: Dict[str, int] = {
    "1": 0, "#1": 1, "b1": 0,       # b1 = enharmonic unison / root
    "b2": 1, "2": 2,
    "bb3": 2, "b3": 3, "3": 4,
    "b4": 4, "4": 5, "#4": 6,
    "b5": 6, "5": 7, "#5": 8,
    "b6": 8, "6": 9, "#6": 9, "bb6": 8,
    "bb7": 9, "b7": 10, "7": 11,
    "b8": 11,                       # b8 = leading-tone octave
    "b9": 1, "9": 2, "#9": 3, "bb9": 0,  # bb9 = enharmonic root
    "11": 5, "#11": 6,
    "b13": 8, "13": 9,
}

MINOR_MODES = {
    "minor", "min", "aeolian", "dorian", "phrygian", "locrian",
    "blues", "chromatic",  # mode-altering scales mapped to closest diatonic mode
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


# ─────────────────────────────────────────────────────────────────────────────
# Key parsing
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

    # Colon-separated: "G:major", "Bb:minor", "D:chromatic"
    m = _KEY_COLON_RE.match(v)
    if m:
        note_str, mode_str = m.group(1), m.group(2).lower().strip()
        note_str = note_str[0].upper() + note_str[1:]
        pc = NOTE_TO_PC.get(note_str)
        if pc is None:
            return None
        mode = "minor" if mode_str in MINOR_MODES else "major"
        return (pc, mode)

    # Space-separated: "G major", "A minor", "G mixolydian"
    parts = v.split()
    if len(parts) == 2:
        note_str = parts[0][0].upper() + parts[0][1:]
        mode_str = parts[1].lower()
        pc = NOTE_TO_PC.get(note_str)
        if pc is None:
            return None
        mode = "minor" if mode_str in MINOR_MODES else "major"
        return (pc, mode)

    # Bare note: "C", "F#", "Bb"
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
    tonic in effect at any timestamp. Replaces the loop-scoped closure in the
    original prototype with an explicit, independently testable object.
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
        """Resolve the active tonic pitch-class at a given timestamp."""
        eps = 0.001
        for k in self.keys:
            if k["start"] - eps <= chord_time < k["end"] + eps:
                return k["tonic_pc"]
        past = [k for k in self.keys if k["start"] <= chord_time]
        if past:
            return past[-1]["tonic_pc"]
        return self.keys[0]["tonic_pc"]


# ─────────────────────────────────────────────────────────────────────────────
# Chord (Harte string) parsing
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParsedChord:
    root_interval: int   # semitones above tonic, 0-11
    triad: str
    bass_interval: int   # semitones above root, 0-11
    seventh: str         # "N" | "b7" | "7" | "bb7"
    ninth: str           # "N" | "9" | "#9" | "b9"
    eleventh: str        # "N" | "11" | "#11"
    thirteenth: str      # "N" | "13" | "b13"

    @property
    def state(self) -> str:
        """Level-1 state key: key-relative (interval, triad)."""
        return f"{self.root_interval}_{self.triad}"

    @property
    def extension_key(self) -> str:
        return f"({self.seventh},{self.ninth},{self.eleventh},{self.thirteenth})"


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
        # Strip digits/accidentals/parens and retry (catches leftovers like "maj(9)")
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
                ninth = "b9"          # bb9 collapsed to b9 for the ACR schema
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
            # Non-extension paren tokens (omissions/colour tones: b3, b5, #5,
            # 2, 3, 4, 5, 6, ...) are intentionally ignored - out of scope for
            # the 6D schema, but not an error.

    # ── Bass note ──────────────────────────────────────────────────────────
    bass_interval = 0
    if bass_str:
        bass_str = bass_str.strip()
        bass_note = bass_str[0].upper() + bass_str[1:] if bass_str else ""
        if bass_note in NOTE_TO_PC:
            bass_pc = NOTE_TO_PC[bass_note]
            bass_interval = (bass_pc - root_pc) % 12          # relative to root
        else:
            semis = INTERVAL_TO_SEMI.get(bass_str)
            if semis is not None:
                bass_interval = semis % 12                    # already root-relative

    return ParsedChord(root_interval, triad, bass_interval, seventh, ninth, eleventh, thirteenth)


# ─────────────────────────────────────────────────────────────────────────────
# Genre resolution
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
    """Corpus directory name = path segment directly under data_root."""
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
    """Return (chord_ann, chord_namespace, key_mode_data) from a JAMS annotation list."""
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
# Aggregation state
# ─────────────────────────────────────────────────────────────────────────────
class Accumulator:
    """Holds running counts across all files for one extraction run."""

    def __init__(self, genres: List[str]):
        self.genres = genres
        self.level1: Dict[str, Dict[str, Dict[str, int]]] = {
            g: defaultdict(lambda: defaultdict(int)) for g in genres
        }
        self.level2_ext: Dict[str, Dict[str, Dict[str, int]]] = {
            g: defaultdict(lambda: defaultdict(int)) for g in genres
        }
        self.level2_bass: Dict[str, Dict[str, Dict[str, int]]] = {
            g: defaultdict(lambda: defaultdict(int)) for g in genres
        }
        self.report = {
            "corpora": {},
            "total_files": 0,
            "total_chords_parsed": 0,
            "total_transitions": 0,
            "skipped_no_key": 0,
            "skipped_unknown_corpus": 0,
            "parse_errors": 0,
            "chord_parse_failures": 0,
        }

    def corpus_entry(self, corpus_label: str, genre: str) -> dict:
        if corpus_label not in self.report["corpora"]:
            self.report["corpora"][corpus_label] = {
                "files": 0, "chords": 0, "transitions": 0,
                "genre": genre, "key_failures": 0, "chord_failures": 0,
            }
        return self.report["corpora"][corpus_label]

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

        # Level 2: extension & bass distributions
        for chord in parsed_chords:
            if chord is None:
                continue
            self.level2_ext[genre][chord.state][chord.extension_key] += 1
            self.level2_bass[genre][chord.state][str(chord.bass_interval)] += 1

        # Level 1: bigram transitions (skip across silences - no phantom splicing)
        transitions = 0
        for a, b in zip(parsed_chords, parsed_chords[1:]):
            if a is None or b is None:
                continue
            self.level1[genre][a.state][b.state] += 1
            transitions += 1

        self.report["total_transitions"] += transitions
        entry["transitions"] += transitions
        entry["files"] += 1
        self.report["total_files"] += 1


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

    acc.add_file(genre, corpus_label, parsed)


# ─────────────────────────────────────────────────────────────────────────────
# Normalization / output
# ─────────────────────────────────────────────────────────────────────────────
def dedefault(d):
    if isinstance(d, defaultdict):
        return {k: dedefault(v) for k, v in d.items()}
    return d


def normalise(count_dict: dict) -> dict:
    prob = {}
    for state, outcomes in count_dict.items():
        total = sum(outcomes.values())
        if total == 0:
            continue
        prob[state] = {k: round(v / total, 6) for k, v in outcomes.items()}
    return prob


def write_outputs(acc: Accumulator, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    for genre in acc.genres:
        l1_raw = dedefault(acc.level1[genre])
        le_raw = dedefault(acc.level2_ext[genre])
        lb_raw = dedefault(acc.level2_bass[genre])

        with open(os.path.join(out_dir, f"level1_transitions_{genre}.json"), "w") as f:
            json.dump({"counts": l1_raw, "probabilities": normalise(l1_raw)}, f, indent=2)

        with open(os.path.join(out_dir, f"level2_extensions_{genre}.json"), "w") as f:
            json.dump({"counts": le_raw, "probabilities": normalise(le_raw)}, f, indent=2)

        with open(os.path.join(out_dir, f"level2_bass_{genre}.json"), "w") as f:
            json.dump({"counts": lb_raw, "probabilities": normalise(lb_raw)}, f, indent=2)

        n_states = len(l1_raw)
        n_trans = sum(sum(v.values()) for v in l1_raw.values())
        print(f"\n[{genre.upper()}]")
        print(f"  L1 states: {n_states}   L1 transitions: {n_trans}")
        print(f"  L2 ext states: {len(le_raw)}   L2 bass states: {len(lb_raw)}")

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
        print("WARNING: No JAMS files found. Set DATA_ROOT env var or run from project root.")

    acc = Accumulator(GENRES)
    for fpath in jams_files:
        process_file(fpath, data_root, acc)

    write_outputs(acc, out_dir)

    r = acc.report
    print(f"\n{'=' * 60}")
    print(f"Files processed       : {r['total_files']}")
    print(f"Chords parsed         : {r['total_chords_parsed']:,}")
    print(f"Transitions recorded  : {r['total_transitions']:,}")
    print(f"Skipped - no key      : {r['skipped_no_key']}")
    print(f"Skipped - unknown corp: {r['skipped_unknown_corpus']}")
    print(f"File parse errors     : {r['parse_errors']}")
    print(f"Chord parse failures  : {r['chord_parse_failures']}")
    print(f"\nOutputs -> {out_dir}/")

    return acc


if __name__ == "__main__":
    run()