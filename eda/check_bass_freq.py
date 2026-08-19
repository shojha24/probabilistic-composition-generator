import json
import os
from pathlib import Path

def calculate_slash_chord_frequency(genre_dir):
    EDA_DIR = Path(__file__).resolve().parent
    DIST_DIR = EDA_DIR.parent / "distributions"
    genre_dir = DIST_DIR / genre_dir
    stage2_path = os.path.join(genre_dir, "stage2_bass.json")
    
    if not os.path.exists(stage2_path):
        return None

    with open(stage2_path, "r", encoding="utf-8") as f:
        stage2_data = json.load(f)

    total_chords = 0
    total_slash_chords = 0

    # level0 counts map: root_interval -> {bass_interval -> count}
    level0_counts = stage2_data.get("level0", {}).get("counts", {})

    for root, bass_dict in level0_counts.items():
        for bass_interval, count in bass_dict.items():
            total_chords += count
            if bass_interval != "0":
                total_slash_chords += count

    if total_chords == 0:
        return 0.0

    return (total_slash_chords / total_chords) * 100


def main():  
    for genre in ["pop_rock", "jazz"]:
        genre_dir = os.path.join(genre)
        percentage = calculate_slash_chord_frequency(genre_dir)
        
        if percentage is not None:
            print(f"--- {genre.upper()} ---")
            print(f"Total Slash Chords / Inversions: {percentage:.2f}%")
        else:
            print(f"Could not find stage2_bass.json for {genre}")


if __name__ == "__main__":
    main()