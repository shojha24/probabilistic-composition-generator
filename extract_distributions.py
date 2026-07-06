"""
Phase 1: Transition & Probability Extraction
=============================================
Extracts key-relative Level-1 transition matrices and Level-2 empirical
extension / bass distributions from normalized JAMS files, partitioned by
genre umbrella (Pop/Rock, Jazz, Classical).

Verified against the full 2282-chord, 469k-instance vocabulary.

Outputs (written to ./distributions/):
  level1_transitions_{genre}.json   – key-relative (interval, triad) bigram counts + probs
  level2_extensions_{genre}.json    – P(7th, 9th, 11th, 13th | interval, triad)
  level2_bass_{genre}.json          – P(bass_interval | interval, triad)
  extraction_report.json            – per-corpus file counts, skips, warnings
"""

import os, re, json, glob
from collections import defaultdict

# ─────────────────────────────────────────────────────────────────────────────
# Genre umbrella routing
# ─────────────────────────────────────────────────────────────────────────────
CORPUS_TO_GENRE = {
    "mcgill billboard": "pop_rock",
    "billboard":        "pop_rock",
    "isophonics":       "pop_rock",
    "ireal pro":        "jazz",
    "ireal-pro":        "jazz",
    "irealpro":         "jazz",
    "weimar jazz database": "jazz",
    "weimar-jazz":      "jazz",
    "weimarjazz":       "jazz",
}
GENRES = ["pop_rock", "jazz"]

# ─────────────────────────────────────────────────────────────────────────────
# Chromatic pitch-class lookup  (covers every note in the dataset)
# ─────────────────────────────────────────────────────────────────────────────
NOTE_TO_PC = {
    "C":  0, "B#": 0,
    "C#": 1, "Db": 1,
    "D":  2,
    "D#": 3, "Eb": 3,
    "E":  4, "Fb": 4,
    "F":  5, "E#": 5,
    "F#": 6, "Gb": 6,
    "G":  7,
    "G#": 8, "Ab": 8,
    "A":  9,
    "A#":10, "Bb":10,
    "B": 11, "Cb":11,
}

# ─────────────────────────────────────────────────────────────────────────────
# Quality → triad class
# Covers all 24 quality tokens found in the normalised vocab.
# ─────────────────────────────────────────────────────────────────────────────
QUALITY_TO_TRIAD = {
    # plain major
    "":       "major",   "maj":    "major",
    "maj6":   "major",   "maj7":   "major",
    "maj9":   "major",   "maj13":  "major",
    "6":      "major",   "7":      "major",
    "9":      "major",   "11":     "major",   "13":     "major",
    "1":      "major",
    # minor
    "min":    "minor",   "m":      "minor",
    "min6":   "minor",   "min7":   "minor",
    "min9":   "minor",   "min11":  "minor",   "min13":  "minor",
    "minmaj7":"minor",
    # diminished
    "dim":    "diminished", "dim7":  "diminished",
    "hdim7":  "diminished",
    # augmented
    "aug":    "augmented",  "aug7":  "augmented",
    # suspended
    "sus4":   "sus4",   "sus":    "sus4",   "7sus4":  "sus4",
    "sus2":   "sus2",
    # power / 5th
    "5":      "5",
}

# ─────────────────────────────────────────────────────────────────────────────
# Quality → 7th component
# ─────────────────────────────────────────────────────────────────────────────
QUALITY_TO_7TH = {
    # natural 7th (major 7th interval)
    "maj7":   "7",    "maj9":   "7",    "maj13":  "7",   "minmaj7": "7",
    # flat 7th (minor 7th interval)
    "7":      "b7",   "9":      "b7",   "11":     "b7",  "13":      "b7",
    "min7":   "b7",   "min9":   "b7",   "min11":  "b7",  "min13":   "b7",
    "hdim7":  "b7",   "7sus4":  "b7",   "aug7":   "b7",
    # double-flat 7th (diminished 7th interval — must NOT be collapsed)
    "dim7":   "bb7",
    # no 7th
    "":       "N",    "maj":    "N",    "min":    "N",
    "dim":    "N",    "aug":    "N",    "sus4":   "N",
    "sus2":   "N",    "5":      "N",
    "maj6":   "N",    "min6":   "N",    "6":      "N",
    "1":      "N",
}

# ─────────────────────────────────────────────────────────────────────────────
# Interval tokens → semitones above root
# Covers all 25 bass tokens + every paren extension token in the dataset.
# ─────────────────────────────────────────────────────────────────────────────
INTERVAL_TO_SEMI = {
    "1": 0,   "#1": 1,  "b1":  0,   # b1 = enharmonic unison / root
    "b2": 1,  "2":  2,
    "bb3":2,  "b3": 3,  "3":   4,
    "b4": 4,  "4":  5,  "#4":  6,
    "b5": 6,  "5":  7,  "#5":  8,
    "b6": 8,  "6":  9,  "#6":  9,   "bb6": 8,
    "bb7":9,  "b7": 10, "7":   11,
    "b8": 11,           # b8 = leading-tone octave, treat as 11
    "b9": 1,  "9":  2,  "#9":  3,   "bb9": 0,   # bb9 = enharmonic root
    "11": 5,  "#11": 6,
    "b13":8,  "13": 9,
}

# ─────────────────────────────────────────────────────────────────────────────
# Helper: parse key annotation value
# Handles: "C", "G:major", "Bb:major", "G:minor", "G mixolydian", "D:aeolian",
#          "D:chromatic", "D:dorian", "Ab:mixolydian", "Bb:blues", "N"
# Mode-altering scales are mapped to closest diatonic mode.
# Returns (tonic_pc, mode) or None.
# ─────────────────────────────────────────────────────────────────────────────
MINOR_MODES = {"minor", "min", "aeolian", "dorian", "phrygian", "locrian",
               "blues", "chromatic"}   # treat chromatic/blues as minor for interval math
MAJOR_MODES = {"major", "maj", "ionian", "mixolydian", "lydian"}

def parse_key(value):
    if not value or value.strip() in ("N", ""):
        return None
    v = value.strip()

    # Colon-separated: "G:major", "Bb:minor", "D:chromatic"
    m = re.match(r'^([A-Ga-g][#b]?):(.+)$', v)
    if m:
        note_str, mode_str = m.group(1), m.group(2).lower().strip()
        note_str = note_str[0].upper() + note_str[1:]
        pc = NOTE_TO_PC.get(note_str)
        if pc is None:
            return None
        if mode_str in MINOR_MODES:
            return (pc, "minor")
        else:
            return (pc, "major")

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
    m2 = re.match(r'^([A-Ga-g][#b]?)$', v)
    if m2:
        note_str = m2.group(1)[0].upper() + m2.group(1)[1:]
        pc = NOTE_TO_PC.get(note_str)
        if pc is not None:
            return (pc, "major")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Helper: parse a single Harte chord string
# Returns (root_pc, triad, bass_interval_from_tonic, seventh, ninth, eleventh, thirteenth)
# or None for "N" / "X" / unparseable.
# ─────────────────────────────────────────────────────────────────────────────
_CHORD_RE = re.compile(
    r'^([A-Ga-g][#b]?)'       # 1: root note
    r'(?::([a-zA-Z0-9]+))?'   # 2: quality (optional)
    r'(\([^)]*\))?'            # 3: paren extensions (optional)
    r'(?:/([^/\s]+))?$'        # 4: bass (optional)
)

def parse_harte(value, tonic_pc):
    """
    Parse Harte string, resolve intervals relative to tonic_pc.
    Returns (root_interval, triad, bass_interval, seventh, ninth, eleventh, thirteenth)
    or None.
    """
    if value in ("N", "X", "", None):
        return None

    m = _CHORD_RE.match(value.strip())
    if not m:
        return None

    root_str, quality, paren_str, bass_str = m.groups()
    root_str = root_str[0].upper() + root_str[1:]
    quality  = (quality or "").strip()

    root_pc = NOTE_TO_PC.get(root_str)
    if root_pc is None:
        return None

    root_interval = (root_pc - tonic_pc) % 12

    # ── Triad ──────────────────────────────────────────────────────────────
    triad = QUALITY_TO_TRIAD.get(quality)
    if triad is None:
        # Strip digits/accidentals and retry (catches things like "maj(9)" leftovers)
        stripped = re.sub(r'[\d#b()]', '', quality)
        triad = QUALITY_TO_TRIAD.get(stripped, "major")

    # ── Seventh ────────────────────────────────────────────────────────────
    seventh = QUALITY_TO_7TH.get(quality, "N")

    # ── Extensions from paren string ──────────────────────────────────────
    ninth = eleventh = thirteenth = "N"

    # Also capture extensions baked into the quality name
    if quality in ("9", "min9", "maj9"):
        ninth = "9"
    if quality in ("11", "min11", "maj11"):
        ninth, eleventh = "9", "11"
    if quality in ("13", "min13", "maj13"):
        ninth, eleventh, thirteenth = "9", "11", "13"

    if paren_str:
        tokens = [t.strip() for t in paren_str.strip("()").split(",") if t.strip()]
        for tok in tokens:
            if   tok in ("9",  "add9"):  ninth       = "9"
            elif tok == "#9":            ninth       = "#9"
            elif tok in ("b9", "bb9"):   ninth       = "b9"   # bb9 ≈ b9 for ACR
            elif tok in ("11","add11"):  eleventh    = "11"
            elif tok == "#11":           eleventh    = "#11"
            elif tok in ("13","add13"):  thirteenth  = "13"
            elif tok == "b13":           thirteenth  = "b13"
            # Paren-embedded 7th override (e.g. sus4(b7,9) bakes b7 into paren)
            elif tok == "b7" and seventh == "N":
                seventh = "b7"
            elif tok == "7" and seventh == "N":
                seventh = "7"
            elif tok == "bb7" and seventh == "N":
                seventh = "bb7"
            # Non-extension paren tokens (omissions, colour tones) — ignore for
            # the 6D schema but don't error: b3, b5, #5, 2, 3, 4, 5, 6, etc.

    # ── Bass note ──────────────────────────────────────────────────────────
    if bass_str:
        bass_str = bass_str.strip()
        bass_note = bass_str[0].upper() + bass_str[1:] if bass_str else ""
        
        if bass_note in NOTE_TO_PC:
            bass_pc = NOTE_TO_PC[bass_note]
            bass_interval = (bass_pc - root_pc) % 12  # <--- CHANGED: Relative to Root
        else:
            semis = INTERVAL_TO_SEMI.get(bass_str)
            if semis is not None:
                bass_interval = semis % 12            # <--- CHANGED: Semis is already relative to root
            else:
                bass_interval = 0                     # Root position
    else:
        bass_interval = 0

    return (root_interval, triad, bass_interval, seventh, ninth, eleventh, thirteenth)


# ─────────────────────────────────────────────────────────────────────────────
# Corpus genre resolver
# ─────────────────────────────────────────────────────────────────────────────
def resolve_genre(ann_meta, corpus_dir_hint=""):
    raw = (ann_meta.get("corpus") or corpus_dir_hint or "").lower().strip()
    # Try full string
    if raw in CORPUS_TO_GENRE:
        return CORPUS_TO_GENRE[raw]
    # Try substring matches (longest match wins)
    best = None
    best_len = 0
    for key, genre in CORPUS_TO_GENRE.items():
        k = key.lower()
        if k in raw and len(k) > best_len:
            best, best_len = genre, len(k)
    if best:
        return best
    # Try directory name
    dir_norm = corpus_dir_hint.lower().replace("-","").replace("_","").replace(" ","")
    for key, genre in CORPUS_TO_GENRE.items():
        k = key.lower().replace("-","").replace("_","").replace(" ","")
        if k == dir_norm or k in dir_norm:
            return genre
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Accumulator structures
# ─────────────────────────────────────────────────────────────────────────────
level1     = {g: defaultdict(lambda: defaultdict(int)) for g in GENRES}
level2_ext = {g: defaultdict(lambda: defaultdict(int)) for g in GENRES}
level2_bass= {g: defaultdict(lambda: defaultdict(int)) for g in GENRES}

report = {
    "corpora": {},
    "total_files": 0,
    "total_chords_parsed": 0,
    "total_transitions": 0,
    "skipped_no_key": 0,
    "skipped_unknown_corpus": 0,
    "parse_errors": 0,
    "chord_parse_failures": 0,
}


# ─────────────────────────────────────────────────────────────────────────────
# File discovery
# ─────────────────────────────────────────────────────────────────────────────
DATA_ROOT = os.environ.get("DATA_ROOT", "data_normalized")
if not os.path.isdir(DATA_ROOT):
    DATA_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_normalized")

jams_files = sorted(glob.glob(os.path.join(DATA_ROOT, "**", "*.jams"), recursive=True))
print(f"Found {len(jams_files)} JAMS files under {DATA_ROOT}")
if not jams_files:
    print("WARNING: No JAMS files found. Set DATA_ROOT env var or run from project root.")


def get_annotations(annotations):
    """Return (chord_ann, chord_namespace, key_data_list)."""
    chord_ann, chord_ns = None, None
    key_data = []
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
# Main processing loop
# ─────────────────────────────────────────────────────────────────────────────
for i, fpath in enumerate(jams_files):
    # Infer corpus from directory name (segment after data_normalized/)
    norm_path = fpath.replace("\\", "/")
    parts = norm_path.split("/")
    try:
        idx = next(i for i, p in enumerate(parts) if "data_normalized" in p)
        corpus_dir = parts[idx + 1] if idx + 1 < len(parts) else ""
    except StopIteration:
        corpus_dir = ""

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        report["parse_errors"] += 1
        continue

    annotations = data.get("annotations", [])
    chord_ann, chord_ns, key_data = get_annotations(annotations)
    if not chord_ann:
        continue

    # ── Genre ──────────────────────────────────────────────────────────────
    ann_meta = chord_ann.get("annotation_metadata", {})
    genre = resolve_genre(ann_meta, corpus_dir)
    if genre is None:
        report["skipped_unknown_corpus"] += 1
        continue

    corpus_label = ann_meta.get("corpus") or corpus_dir
    if corpus_label not in report["corpora"]:
        report["corpora"][corpus_label] = {
            "files": 0, "chords": 0, "transitions": 0,
            "genre": genre, "key_failures": 0, "chord_failures": 0
        }

    # ── Key ────────────────────────────────────────────────────────────────
    active_keys = []
    for kd in key_data:
        val = str(kd.get("value", ""))
        kr = parse_key(val)
        if kr:
            t = float(kd.get("time", 0.0))
            d = float(kd.get("duration", 0.0))
            active_keys.append({
                "start": t,
                "end": t + d,
                "tonic_pc": kr[0]
            })

    if not active_keys:
        report["skipped_no_key"] += 1
        report["corpora"][corpus_label]["key_failures"] += 1
        continue

    # Sort keys by start time to ensure chronological sequence
    active_keys.sort(key=lambda x: x["start"])

    def get_active_tonic(chord_time):
        """Finds the active key for a given timestamp."""
        for k in active_keys:
            # Use a tiny epsilon (0.001) to forgive floating point inaccuracies in JAMS
            if k["start"] - 0.001 <= chord_time < k["end"] + 0.001:
                return k["tonic_pc"]
        
        # Fallback: If there is a tiny gap between annotations, use the most recent key
        past_keys = [k for k in active_keys if k["start"] <= chord_time]
        if past_keys:
            return past_keys[-1]["tonic_pc"]
        
        # Absolute fallback: use the first key in the song
        return active_keys[0]["tonic_pc"]

    # ── Parse chords ───────────────────────────────────────────────────────
    parsed = []
    for entry in chord_ann.get("data", []):
        val = entry.get("value", "N")
        chord_time = float(entry.get("time", 0.0))
        
        # Dynamically resolve the key for this specific chord's timestamp
        current_tonic = get_active_tonic(chord_time)

        result = parse_harte(val, current_tonic)
        
        if result is None:
            if val not in ("N", "X", "", None):
                report["chord_parse_failures"] += 1
                report["corpora"][corpus_label]["chord_failures"] += 1
            parsed.append(None) # Phantom splicing fix
            continue
            
        parsed.append(result)

    # Count only the actual parsed chords, ignoring the Nones
    valid_chords_count = sum(1 for p in parsed if p is not None)
    report["total_chords_parsed"] += valid_chords_count
    report["corpora"][corpus_label]["chords"] += valid_chords_count

    # ── Level 2: extension & bass distributions ────────────────────────────
    for item in parsed:
        if item is None:
            continue # FIX: Prevent unpacking error on silences
            
        root_iv, triad, bass_iv, seventh, ninth, eleventh, thirteenth = item
        state   = f"{root_iv}_{triad}"
        ext_key = f"({seventh},{ninth},{eleventh},{thirteenth})"
        level2_ext [genre][state][ext_key]    += 1
        level2_bass[genre][state][str(bass_iv)] += 1

    # ── Level 1: bigram transitions ────────────────────────────────────────
    valid_transitions = 0
    for i in range(len(parsed) - 1):
        if parsed[i] is None or parsed[i+1] is None:
            continue # FIX: Breaks the phantom splice!
            
        from_st = f"{parsed[i][0]}_{parsed[i][1]}"
        to_st   = f"{parsed[i+1][0]}_{parsed[i+1][1]}"
        level1[genre][from_st][to_st] += 1
        valid_transitions += 1

    report["total_transitions"] += valid_transitions
    report["corpora"][corpus_label]["transitions"] += valid_transitions
    report["corpora"][corpus_label]["files"] += 1
    report["total_files"] += 1

# ─────────────────────────────────────────────────────────────────────────────
# Normalise counts → probabilities
# ─────────────────────────────────────────────────────────────────────────────
def dedefault(d):
    if isinstance(d, defaultdict):
        return {k: dedefault(v) for k, v in d.items()}
    return d

def normalise(count_dict):
    prob = {}
    for state, outcomes in count_dict.items():
        total = sum(outcomes.values())
        if total == 0:
            continue
        prob[state] = {k: round(v / total, 6) for k, v in outcomes.items()}
    return prob


# ─────────────────────────────────────────────────────────────────────────────
# Write outputs
# ─────────────────────────────────────────────────────────────────────────────
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "distributions")
os.makedirs(OUT_DIR, exist_ok=True)

for genre in GENRES:
    l1_raw  = dedefault(level1     [genre])
    le_raw  = dedefault(level2_ext [genre])
    lb_raw  = dedefault(level2_bass[genre])

    with open(os.path.join(OUT_DIR, f"level1_transitions_{genre}.json"), "w") as f:
        json.dump({"counts": l1_raw, "probabilities": normalise(l1_raw)}, f, indent=2)

    with open(os.path.join(OUT_DIR, f"level2_extensions_{genre}.json"), "w") as f:
        json.dump({"counts": le_raw, "probabilities": normalise(le_raw)}, f, indent=2)

    with open(os.path.join(OUT_DIR, f"level2_bass_{genre}.json"), "w") as f:
        json.dump({"counts": lb_raw, "probabilities": normalise(lb_raw)}, f, indent=2)

    n_states = len(l1_raw)
    n_trans  = sum(sum(v.values()) for v in l1_raw.values())
    print(f"\n[{genre.upper()}]")
    print(f"  L1 states: {n_states}   L1 transitions: {n_trans}")
    print(f"  L2 ext states: {len(le_raw)}   L2 bass states: {len(lb_raw)}")

with open(os.path.join(OUT_DIR, "extraction_report.json"), "w") as f:
    json.dump(report, f, indent=2)

print(f"\n{'='*60}")
print(f"Files processed       : {report['total_files']}")
print(f"Chords parsed         : {report['total_chords_parsed']:,}")
print(f"Transitions recorded  : {report['total_transitions']:,}")
print(f"Skipped – no key      : {report['skipped_no_key']}")
print(f"Skipped – unknown corp: {report['skipped_unknown_corpus']}")
print(f"File parse errors     : {report['parse_errors']}")
print(f"Chord parse failures  : {report['chord_parse_failures']}")
print(f"\nOutputs → {OUT_DIR}/")
