from pathlib import Path
from collections import defaultdict, Counter
import jams

def main() -> None:
    ROOT_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT_DIR / "data_normalized"
    
    # Store: stats[dataset_name][key_string] = count
    stats = defaultdict(Counter)

    jams_files = list(DATA_DIR.rglob("*.jams"))
    print(f"Scanning {len(jams_files)} files for keys...")

    for i, jf in enumerate(jams_files):
        # Determine dataset name
        try:
            dataset_name = jf.relative_to(DATA_DIR).parts[0]
        except ValueError:
            dataset_name = "unknown"

        # Load JAMS
        try:
            jam = jams.load(str(jf), validate=False)
        except Exception:
            continue

        # Extract keys
        found_key = False
        for annotation in jam.annotations:
            if annotation.namespace == "key_mode":
                for obs in annotation.data:
                    # JAMS key_mode values are usually a dict or object
                    # We format as "Root:Mode" (e.g., "C:major")
                    val = obs.value
                    if isinstance(val, dict):
                        root = val.get('tonic', 'Unknown')
                        mode = val.get('mode', 'Unknown')
                        key_str = f"{root}:{mode}"
                    else:
                        key_str = str(val)
                    
                    stats[dataset_name][key_str] += 1
                    found_key = True
        
        if not found_key:
            stats[dataset_name]["MISSING"] += 1

        if (i + 1) % 500 == 0:
            print(f"Processed {i + 1} files...")

    # Print results formatted by dataset
    print("\n--- DATASET KEY DISTRIBUTION ---")
    for dataset in sorted(stats.keys()):
        print(f"\nDataset: {dataset}")
        dataset_counts = stats[dataset].most_common()
        for key, count in dataset_counts:
            print(f"  {key:<15} : {count}")

if __name__ == "__main__":
    main()