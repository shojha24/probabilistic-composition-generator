from pathlib import Path
from collections import defaultdict
import jams
import csv

def main():
    ROOT_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT_DIR / "data"

    jams_files = list(DATA_DIR.rglob('*.jams'))
    
    # Track the unique files that contain each chord
    # chord_files[chord_string][dataset_name] = set(file_names)
    chord_files = defaultdict(lambda: defaultdict(set))
    all_datasets = set()

    for i, jf in enumerate(jams_files):
        try:
            dataset_name = jf.relative_to(DATA_DIR).parts[0]
        except ValueError:
            dataset_name = "unknown"
            
        all_datasets.add(dataset_name)

        try:
            jam = jams.load(str(jf), validate=False)
        except Exception:
            continue

        for annotation in jam.annotations:
            if annotation.namespace in ('chord', 'chord_harte'):
                for observation in annotation.data:
                    if observation.value:
                        # Add the specific filename to the set for this chord/dataset
                        chord_files[observation.value][dataset_name].add(jf.name)
                        
        if (i + 1) % 100 == 0:
            print(f"Processed {i + 1} files...")

    # Sort chords by the TOTAL number of unique files they appear in across all datasets
    sorted_chords = sorted(
        chord_files.keys(), 
        key=lambda c: sum(len(files) for files in chord_files[c].values()), 
        reverse=True
    )
    
    sorted_datasets = sorted(list(all_datasets))
    out_path = ROOT_DIR / 'chord_song_counts.csv'
    
    with open(out_path, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # Header now reflects that we are counting Songs, not total occurrences
        writer.writerow(['Chord', 'Total_Songs_Affected'] + [f"{ds}_Songs" for ds in sorted_datasets])
        
        for chord in sorted_chords:
            total_songs = sum(len(files) for files in chord_files[chord].values())
            row = [chord, total_songs]
            
            for ds in sorted_datasets:
                # Append the size of the set (number of unique files)
                row.append(len(chord_files[chord].get(ds, set())))
                
            writer.writerow(row)

    print(f'Wrote {len(sorted_chords)} unique chords to {out_path.name}')

if __name__ == '__main__':
    main()