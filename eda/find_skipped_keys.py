import os
import json
import glob
import re
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Replicate the exact parsing logic from your extraction script
# ─────────────────────────────────────────────────────────────────────────────
NOTE_TO_PC = {
    "C":  0, "B#": 0, "C#": 1, "Db": 1, "D":  2, "D#": 3, "Eb": 3,
    "E":  4, "Fb": 4, "F":  5, "E#": 5, "F#": 6, "Gb": 6, "G":  7,
    "G#": 8, "Ab": 8, "A":  9, "A#":10, "Bb":10, "B": 11, "Cb":11,
}

MINOR_MODES = {"minor", "min", "aeolian", "dorian", "phrygian", "locrian", "blues", "chromatic"}

def parse_key(value):
    if not value or value.strip() in ("N", ""): return None
    v = value.strip()

    m = re.match(r'^([A-Ga-g][#b]?):(.+)$', v)
    if m:
        note_str, mode_str = m.group(1), m.group(2).lower().strip()
        pc = NOTE_TO_PC.get(note_str[0].upper() + note_str[1:])
        return (pc, "minor") if mode_str in MINOR_MODES else (pc, "major") if pc is not None else None

    parts = v.split()
    if len(parts) == 2:
        pc = NOTE_TO_PC.get(parts[0][0].upper() + parts[0][1:])
        return (pc, "minor" if parts[1].lower() in MINOR_MODES else "major") if pc is not None else None

    m2 = re.match(r'^([A-Ga-g][#b]?)$', v)
    if m2:
        pc = NOTE_TO_PC.get(m2.group(1)[0].upper() + m2.group(1)[1:])
        return (pc, "major") if pc is not None else None

    return None

# ─────────────────────────────────────────────────────────────────────────────
# Main Scanner
# ─────────────────────────────────────────────────────────────────────────────
def main():
    # Adjust this path if needed
    ROOT_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT_DIR / "data_normalized"
    OUTPUT_FILE = ROOT_DIR / "skipped_27_files.txt"

    jams_files = sorted(glob.glob(os.path.join(DATA_DIR, "**", "*.jams"), recursive=True))
    print(f"Scanning {len(jams_files)} files for the missing 27 keys...")

    skipped_files = []

    for fpath in jams_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        annotations = data.get("annotations", [])
        
        # Extract key data
        key_data = []
        for ann in annotations:
            if ann.get("namespace", "") == "key_mode":
                key_data.extend(ann.get("data", []))

        # Check if the key passes the parser
        key_result = None
        for kd in key_data:
            key_result = parse_key(str(kd.get("value", "")))
            if key_result:
                break

        # If it fails, log the relative path
        if key_result is None:
            # Get just the dataset/jams/filename part for easy reading
            rel_path = os.path.relpath(fpath, DATA_DIR)
            
            # Try to grab what the raw value actually was (to see WHY it failed)
            raw_val = "NO_KEY_ANNOTATION"
            if key_data:
                raw_val = str(key_data[0].get("value", ""))
                
            skipped_files.append(f"{rel_path}  |  Raw Value: '{raw_val}'")

    # Save to file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(skipped_files))

    print(f"\nFound {len(skipped_files)} skipped files.")
    print(f"Saved list to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()