import pandas as pd
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
    # Handle NaN or explicit 'No Chord' labels
    if pd.isna(chord) or chord == 'N' or chord == 'X':
        return chord

    # Regex to mathematically deconstruct Harte syntax: Root:(shorthand)(extensions)/bass
    # E.g., C:min7(*1)/5 -> Group 1: C, Group 2: min7, Group 3: *1, Group 4: 5
    match = re.match(r'^([A-G][#b]*):?([^\(/]*)(?:\(([^)]+)\))?(?:/(.+))?$', chord)
    
    if not match:
        return chord # Return original if unparseable

    root = match.group(1)
    shorthand = match.group(2)
    extensions = match.group(3)
    bass = match.group(4)

    # RULE 1: ENHARMONIC EQUIVALENTS
    if root in ENHARMONIC_MAP:
        root = ENHARMONIC_MAP[root]

    # Parse extensions into a workable list
    ext_list = [x.strip() for x in extensions.split(',')] if extensions else []

    # RULE 2: OMITTED ROOTS (*1)
    # Simply strip *1 from the extensions. The root note remains as the structural anchor.
    ext_list = [x for x in ext_list if x != '*1']

    # RULE 3: EXPLICIT OMISSIONS (*3, *5)
    # Strip the '*' so the parser treats the interval as being played normally
    ext_list = [x.replace('*', '') for x in ext_list]

    # RULE 4: INTERVAL CLUSTERS -> SHORTHAND MAPPING
    # If there is no shorthand (e.g. C:(3,5,b7,b9)), we deduce the functional triad/7th
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

        # Deduce the foundational shorthand
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
        elif '5' in ext_list and not (is_major or is_minor): shorthand = '5' # Power chord
        else: shorthand = 'maj' # Default fallback for bare roots

        # RULE 5: MAJOR FLAT-5 ELEGANCE (Altered Dominants)
        # Convert (3, b5) structures into standard major/dominant chords with a #11 extension
        if is_major and is_dim5:
            if '#11' not in ext_list:
                ext_list.append('#11')

        # Clean up the extensions list by removing the core intervals we just absorbed into the shorthand
        foundations = {'1', '2', '3', 'b3', 'bb3', '4', '5', 'b5', '#5', '7', 'b7', 'bb7'}
        ext_list = [x for x in ext_list if x not in foundations]

    else:
        # If the chord already has a shorthand (e.g. C:maj), just check for the clumsy b5 extension
        major_types = ['maj', '7', 'maj7', '9', '11', '13']
        if shorthand in major_types and 'b5' in ext_list:
            ext_list.remove('b5')
            if '#11' not in ext_list:
                ext_list.append('#11')

    # Reconstruct the cleaned Harte string
    final_ext = f"({','.join(ext_list)})" if ext_list else ""
    final_bass = f"/{bass}" if bass else ""
    
    # Ensure standard major triad formatting
    if not shorthand: shorthand = 'maj'
        
    return f"{root}:{shorthand}{final_ext}{final_bass}"

def main():
    # Setup paths
    ROOT_DIR = Path(__file__).resolve().parent.parent
    input_csv = ROOT_DIR / 'chord_dataset_counts.csv'
    output_csv = ROOT_DIR / 'chord_dataset_counts_fixed.csv'

    print(f"Loading {input_csv.name}...")
    df = pd.read_csv(input_csv)

    print("Applying structural normalization rules...")
    # Apply the normalization logic to create the new column
    df['Fixed_Chord'] = df['Chord'].apply(normalize_chord)

    # Reorder columns so 'Fixed_Chord' sits right next to the original 'Chord' column
    cols = list(df.columns)
    cols.insert(1, cols.pop(cols.index('Fixed_Chord')))
    df = df[cols]

    # Save to a new CSV to prevent overwriting the original raw data
    df.to_csv(output_csv, index=False)
    print(f"Success! Processed {len(df)} unique chords.")
    print(f"Saved normalized dataset to: {output_csv.name}")

if __name__ == '__main__':
    main()