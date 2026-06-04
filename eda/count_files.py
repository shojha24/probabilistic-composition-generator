import os
from pathlib import Path
from collections import defaultdict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data_normalized"


if not DATA_DIR.exists():
    print(f"Directory {DATA_DIR} not found")
else:
    counts = defaultdict(int)
    
    for subfolder in DATA_DIR.iterdir():
        if subfolder.is_dir():
            jams_count = len(list(subfolder.rglob("*.jams")))
            counts[subfolder.name] = jams_count
    
    print("JAMS files per subfolder:")
    print("-" * 40)
    for subfolder, count in sorted(counts.items()):
        print(f"{subfolder}: {count}")
    print("-" * 40)
    print(f"Total: {sum(counts.values())} files")
