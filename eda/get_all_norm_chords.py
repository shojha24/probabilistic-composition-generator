from pathlib import Path
from collections import Counter, defaultdict
import json
import csv

def main():
    ROOT_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT_DIR / "data_normalized"

    jams_files = list(DATA_DIR.rglob('*.jams'))
    
    # Nested dictionary: counts[chord_string][dataset_name] = count
    counts = defaultdict(Counter)
    
    # Keep track of every unique dataset partition we encounter
    all_datasets = set()

    for i, jf in enumerate(jams_files):
        # Extract the dataset name based on the folder structure
        try:
            dataset_name = jf.relative_to(DATA_DIR).parts[0]
        except ValueError:
            dataset_name = "unknown"
            
        all_datasets.add(dataset_name)

        # FAST I/O: Load as a raw Python dictionary using the native C-optimized JSON parser
        try:
            with open(jf, 'r', encoding='utf-8') as f:
                jam_data = json.load(f)
        except Exception as e:
            print(f"Skipping {jf.name} due to read error: {e}")
            continue

        # Traverse the raw dictionary
        for annotation in jam_data.get('annotations', []):
            if annotation.get('namespace') in ('chord', 'chord_harte'):
                for obs in annotation.get('data', []):
                    val = obs.get('value')
                    if val:
                        counts[val][dataset_name] += 1
                        
        # Progress tracker
        if (i + 1) % 100 == 0:
            print(f"Processed {i + 1} files...")

    # Calculate total counts to sort the output from most to least frequent
    sorted_chords = sorted(counts.keys(), key=lambda c: sum(counts[c].values()), reverse=True)
    
    # Alphabetize the dataset columns for consistent formatting
    sorted_datasets = sorted(list(all_datasets))

    EDA_DIR = Path(__file__).resolve().parent
    out_path = EDA_DIR / 'chord_dataset_normalized_counts.csv'
    
    # Write results to CSV
    with open(out_path, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        # Write the header row
        header = ['Chord', 'Total_Count'] + sorted_datasets
        writer.writerow(header)
        
        # Write the data rows
        for chord in sorted_chords:
            total = sum(counts[chord].values())
            row = [chord, total]
            
            # Append the count for each dataset (default to 0 if it doesn't exist)
            for ds in sorted_datasets:
                row.append(counts[chord].get(ds, 0))
                
            writer.writerow(row)

    print(f'Wrote {len(sorted_chords)} unique chords to {out_path.name}')

if __name__ == '__main__':
    main()