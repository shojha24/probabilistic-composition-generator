import os
import json
from pathlib import Path

def main():
    EDA_DIR = Path(__file__).resolve().parent
    DATA_DIR = EDA_DIR.parent / "data_normalized"
    
    jams_files = list(DATA_DIR.rglob('*.jams'))
    modulating_files = []
    total_files = len(jams_files)
    
    print(f"Scanning {total_files} files for key modulations...")

    for jf in jams_files:
        try:
            with open(jf, 'r', encoding='utf-8') as f:
                jam_data = json.load(f)
                
            for annotation in jam_data.get('annotations', []):
                if annotation.get('namespace') == 'key_mode':
                    # Extract all key observations
                    keys = [obs.get('value') for obs in annotation.get('data', []) if obs.get('value')]
                    
                    # Remove sequential duplicates (e.g., C, C, G is 2 unique keys)
                    unique_sequential_keys = []
                    for k in keys:
                        if not unique_sequential_keys or k != unique_sequential_keys[-1]:
                            unique_sequential_keys.append(k)
                            
                    # If there's more than one distinct key shift, log it
                    if len(unique_sequential_keys) > 1:
                        rel_path = jf.relative_to(DATA_DIR)
                        modulating_files.append((str(rel_path), unique_sequential_keys))
                    break # Found the key annotation, move to next file
                    
        except Exception as e:
            continue

    print("-" * 50)
    print(f"Found {len(modulating_files)} files with multiple keys ({(len(modulating_files)/total_files)*100:.2f}% of dataset).")
    
    # Print the first 20 as examples
    for f, keys in modulating_files[:20]:
        print(f"{f}: {keys}")

if __name__ == '__main__':
    main()