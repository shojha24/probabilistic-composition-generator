import os
import json
import glob
import numpy as np
import jams
from tqdm import tqdm

# --- CONFIGURATION ---
# Update these paths to where your .jams files actually live
DATA_DIRS = {
    "billboard": "data/billboard/jams",
    "rock": "data/rock-corpus/jams-converted",
    "weimar": "data/weimar-jazz/jams-converted"
}

OUTPUT_FILE = "chord_transition_matrix.json"

# 4.0 Hz = 0.25s per step. 
# This is fine-grained enough to catch fast jazz changes.
SAMPLING_RATE_HZ = 4.0  

def simplify_harte(chord_label):
    """
    Normalizes Harte strings for the HMM.
    1. 'N' -> 'N'
    2. 'C:maj/5' -> 'C:maj' (Strips bass to prevent combinatorial explosion)
    """
    if chord_label == 'N':
        return 'N'
    
    # Split by forward slash to remove bass note (e.g. F:maj/5 -> F:maj)
    # The Java Renderer will probabilistically add these back later.
    base_chord = chord_label.split('/')[0]
    
    return base_chord

def extract_sequence_from_jams(jams_path):
    """
    Parses a JAMS file and extracts a quantized chord sequence.
    Includes error handling for unknown namespaces (like 'timesig').
    """
    try:
        # validate=False speeds up loading significantly
        jam = jams.load(jams_path, validate=False) 
    except Exception:
        return []

    ann = None
    
    # --- SAFER MANUAL SEARCH ---
    # Instead of jam.annotations.search(), we loop manually.
    # This bypasses the schema check that crashes on 'timesig'.
    
    # Priority 1: Explicit Harte (Rock/Jazz Corpus)
    for a in jam.annotations:
        if a.namespace == 'chord_harte':
            ann = a
            break
    
    # Priority 2: Generic Chord (McGill/Billboard) - only if priority 1 wasn't found
    if not ann:
        for a in jam.annotations:
            if a.namespace == 'chord':
                ann = a
                break
            
    if not ann:
        return []

    # --- QUANTIZATION (Time-Grid) ---
    try:
        if jam.file_metadata.duration:
            duration = jam.file_metadata.duration
        elif ann.data:
            duration = ann.data[-1].time + ann.data[-1].duration
        else:
            return []

        time_points = np.arange(0, duration, 1/SAMPLING_RATE_HZ)

        # JAMS helper to sample values at specific times
        labels = ann.to_samples(time_points)
        
        sequence = []
        for label in labels:
            if label:
                clean_label = simplify_harte(label[0])
                sequence.append(clean_label)
            else:
                sequence.append("N")
                
        return sequence

    except Exception:
        return [] # Skip broken files

def build_transition_matrix(sequences):
    """
    Counts transitions and normalizes to probabilities.
    """
    print("Computing Transitions...")
    transitions = {}
    
    # 1. Count Transitions
    for seq in tqdm(sequences, desc="Counting"):
        for i in range(len(seq) - 1):
            curr = seq[i]
            next_c = seq[i+1]
            
            if curr not in transitions: transitions[curr] = {}
            if next_c not in transitions[curr]: transitions[curr][next_c] = 0
            
            transitions[curr][next_c] += 1
            
    # 2. Normalize & Smooth
    hmm_json = {}
    
    # Filter: If a chord appears fewer than X times as a source, it's noise.
    MIN_ROW_COUNT = 5 
    
    for start_node, targets in transitions.items():
        total_count = sum(targets.values())
        
        if total_count < MIN_ROW_COUNT:
            continue
            
        hmm_json[start_node] = {}
        
        for target_node, count in targets.items():
            prob = count / total_count
            
            # Pruning: Remove ultra-rare 0.1% transitions to keep JSON small
            # (unless you specifically want 'long tail' noise)
            if prob > 0.001: 
                hmm_json[start_node][target_node] = round(prob, 5)

    return hmm_json

if __name__ == "__main__":
    all_sequences = []
    
    # 1. SCANNING
    print("--- Phase 1: Ingesting Data ---")
    for dataset_name, folder_path in DATA_DIRS.items():
        print(f"Scanning {dataset_name} at: {folder_path}")
        
        # Recursive glob to find all .jams files
        files = glob.glob(os.path.join(folder_path, "**", "*.jams"), recursive=True)
        print(f"Found {len(files)} files.")
        
        for f in tqdm(files, desc=f"Parsing {dataset_name}"):
            seq = extract_sequence_from_jams(f)
            # Only keep files that are long enough to be useful (> 2.5 seconds)
            if len(seq) > 10: 
                all_sequences.append(seq)

    print(f"\nTotal Songs Processed: {len(all_sequences)}")
    
    # 2. BUILDING
    print("\n--- Phase 2: Building HMM ---")
    hmm = build_transition_matrix(all_sequences)
    
    # 3. SAVING
    print(f"\n--- Phase 3: Saving to {OUTPUT_FILE} ---")
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(hmm, f, indent=2)
        
    print(f"Success! HMM contains {len(hmm)} unique chords.")
    print("Next Step: Run 'composer.py' to generate JFugue scores.")