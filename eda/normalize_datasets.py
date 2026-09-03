import json
import re
from pathlib import Path

# 1. ENHARMONIC FIXES: Map all bizarre classical spellings back to the standard 12-pitch classes
ENHARMONIC_MAP = {
    "Cb": "B", "Fb": "E", "E#": "F", "B#": "C",
    "C##": "D", "F##": "G", "G##": "A", "A##": "B",
    "D##": "E", "Ebb": "D", "Abb": "G", "Bbb": "A",
    "Dbb": "C", "Gbb": "F", "Cbb": "Bb", "Fbb": "Eb",
    "B##": "C#", "C###": "Eb", "Abbb": "F", "Ebbb": "Db"
}

def normalize_chord(chord):
    if chord is None or chord == 'N' or chord == 'X' or not isinstance(chord, str):
        return chord

    match = re.match(r'^([A-G][#b]*):?([^\(/]*)(?:\(([^)]+)\))?(?:/(.+))?$', chord)
    
    if not match:
        return chord 

    root = match.group(1)
    shorthand = match.group(2)
    extensions = match.group(3)
    bass = match.group(4)

    # RULE 1: ENHARMONIC EQUIVALENTS
    if root in ENHARMONIC_MAP:
        root = ENHARMONIC_MAP[root]

    ext_list = [x.strip() for x in extensions.split(',')] if extensions else []

    # RULE 2: OMITTED ROOTS (*1)
    ext_list = [x for x in ext_list if x != '*1']

    # RULE 3: EXPLICIT OMISSIONS (*3, *5)
    ext_list = [x.replace('*', '') for x in ext_list]

    # RULE 4: INTERVAL CLUSTERS -> SHORTHAND MAPPING
    if not shorthand:
        is_minor = 'b3' in ext_list or 'bb3' in ext_list
        is_major = '3' in ext_list
        is_dim5 = 'b5' in ext_list
        is_aug5 = '#5' in ext_list
        is_dom7 = 'b7' in ext_list
        is_maj7 = '7' in ext_list
        is_dim7 = 'bb7' in ext_list
        is_sus4 = '4' in ext_list and not (is_major or is_minor)
        is_sus2 = '2' in ext_list and not (is_major or is_minor)

        if is_minor and is_dim5 and is_dim7: shorthand = 'dim7'
        elif is_minor and is_dim5 and is_dom7: shorthand = 'hdim7'
        elif is_minor and is_dim5: shorthand = 'dim'
        elif is_major and is_aug5: shorthand = 'aug'
        elif is_minor and is_dom7: shorthand = 'min7'
        elif is_minor: shorthand = 'min'
        elif is_major and is_maj7: shorthand = 'maj7'
        elif is_major and is_dom7: shorthand = '7'
        elif is_sus4: shorthand = 'sus4'
        elif is_sus2: shorthand = 'sus2'
        elif '5' in ext_list and not (is_major or is_minor): shorthand = '5'
        else: shorthand = 'maj'

        # RULE 5: MAJOR FLAT-5 ELEGANCE
        if is_major and is_dim5:
            if '#11' not in ext_list:
                ext_list.append('#11')

        foundations = {'1', '2', '3', 'b3', 'bb3', '4', '5', 'b5', '#5', '7', 'b7', 'bb7'}
        ext_list = [x for x in ext_list if x not in foundations]

    else:
        major_types = ['maj', '7', 'maj7', '9', '11', '13']
        if shorthand in major_types and 'b5' in ext_list:
            ext_list.remove('b5')
            if '#11' not in ext_list:
                ext_list.append('#11')

    final_ext = f"({','.join(ext_list)})" if ext_list else ""
    final_bass = f"/{bass}" if bass else ""
    
    if not shorthand: shorthand = 'maj'
        
    return f"{root}:{shorthand}{final_ext}{final_bass}"

def main():
    ROOT_DIR = Path(__file__).resolve().parent.parent
    EDA_DIR = Path(__file__).resolve().parent
    
    DATA_DIR = ROOT_DIR / "data"
    OUTPUT_DIR = ROOT_DIR / "data_normalized"
    ERROR_LOG = EDA_DIR / "corrupt_files_log.txt" # Saves log to the eda folder

    jams_files = list(DATA_DIR.rglob('*.jams'))
    
    files_processed = 0
    files_modified = 0
    chords_fixed = 0
    timestamps_fixed = 0
    
    corrupt_files = []

    print(f"Found {len(jams_files)} JAMS files. Starting JSON-level normalization with timestamp clamping...")

    for jf in jams_files:
        try:
            with open(jf, 'r', encoding='utf-8') as f:
                jam_data = json.load(f)
        except Exception as e:
            print(f"Skipping {jf.name} due to JSON read error: {e}")
            continue

        file_was_modified = False

        for annotation in jam_data.get('annotations', []):
            
            # 1. SANITIZE BAD TIMESTAMPS
            for obs in annotation.get('data', []):
                if isinstance(obs.get('duration'), (int, float)) and obs['duration'] < 0.0:
                    obs['duration'] = 0.0
                    file_was_modified = True
                    timestamps_fixed += 1
                if isinstance(obs.get('time'), (int, float)) and obs['time'] < 0.0:
                    obs['time'] = 0.0
                    file_was_modified = True
                    timestamps_fixed += 1

            # 2. NORMALIZE CHORDS
            if annotation.get('namespace') in ('chord', 'chord_harte'):
                for obs in annotation.get('data', []):
                    original_chord = obs.get('value')
                    
                    if original_chord and isinstance(original_chord, str):
                        fixed_chord = normalize_chord(original_chord)
                        
                        if fixed_chord != original_chord:
                            obs['value'] = fixed_chord
                            file_was_modified = True
                            chords_fixed += 1
                            
        rel_path = jf.relative_to(DATA_DIR)
        out_file = OUTPUT_DIR / rel_path
        out_file.parent.mkdir(parents=True, exist_ok=True)

        # THE FIX: Safely dump the JSON without redundant JAMS library validation
        try:
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(jam_data, f, indent=2)

            files_processed += 1
            if file_was_modified:
                files_modified += 1
                
        except Exception as e:
            print(f"\n[!] FILE WRITE ERROR in file: {rel_path}")
            print(f"    Error details: {e}\n")
            corrupt_files.append(str(rel_path))
            continue 

        if files_processed % 500 == 0 and files_processed > 0:
            print(f"Processed {files_processed} files... ({chords_fixed} chords, {timestamps_fixed} timestamps fixed)")

    print("\n--- NORMALIZATION COMPLETE ---")
    print(f"Total valid files processed and saved to '{OUTPUT_DIR.name}/': {files_processed}")
    print(f"Files containing fixed annotations/timestamps: {files_modified}")
    print(f"Total individual chord observations rewritten: {chords_fixed}")
    print(f"Total negative timestamps clamped to 0.0: {timestamps_fixed}")
    
    if corrupt_files:
        ERROR_LOG.write_text("\n".join(corrupt_files), encoding="utf-8")
        print(f"\n[WARNING] Found {len(corrupt_files)} files that could not be saved.")
        print(f"Saved corrupted file log to: {ERROR_LOG.name}")

if __name__ == '__main__':
    main()