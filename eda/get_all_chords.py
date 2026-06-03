from pathlib import Path
from collections import Counter, defaultdict
import jams
import csv

def main():
    ROOT_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT_DIR / "data"

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

        # Use jams to load the file, bypassing strict schema validation
        try:
            jam = jams.load(str(jf), validate=False)
        except Exception as e:
            print(f"Skipping {jf.name} due to load error: {e}")
            continue

        # Iterate through annotations and filter for chord namespaces
        for annotation in jam.annotations:
            if annotation.namespace in ('chord', 'chord_harte'):
                # Extract the chord string directly from the observation object
                for observation in annotation.data:
                    if observation.value:
                        counts[observation.value][dataset_name] += 1
                        
        # Progress tracker
        if (i + 1) % 100 == 0:
            print(f"Processed {i + 1} files...")

    # Calculate total counts to sort the output from most to least frequent
    sorted_chords = sorted(counts.keys(), key=lambda c: sum(counts[c].values()), reverse=True)
    
    # Alphabetize the dataset columns for consistent formatting
    sorted_datasets = sorted(list(all_datasets))

    out_path = ROOT_DIR / 'chord_dataset_counts.csv'
    
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