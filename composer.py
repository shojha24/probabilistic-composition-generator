import json
import random
import os

# --- CONFIGURATION ---
NUM_FILES_TO_GENERATE = 1000
HMM_FILE = "chord_transition_matrix.json"
OUTPUT_TXT_FILE = "gen/generated_scores.txt"
LABELS_DIR = "gen/labels"

os.makedirs(LABELS_DIR, exist_ok=True)

# --- 1. NOTE MATH ---
NOTE_OFFSETS = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
    "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "Cb": 11
}

# --- 2. INSTRUMENT POOLS ---
CHORD_INSTRUMENTS = [0, 4, 16, 25, 29, 48, 88, 104]
BASS_INSTRUMENTS = [32, 33, 34, 38, 43, 110]
MELODY_INSTRUMENTS = [73, 76, 26, 30, 66, 11, 22]

# --- 3. RHYTHM PATTERNS ---
CHORD_RHYTHMS = [
    "Rw",                   # Pad
    "Rq Rq Rq Rq",          # Pop
    "Ri Ri Ri Ri Ri Ri Ri Ri", # Rock
    "Rh Rh",                # Half
]

BASS_RHYTHMS = [
    "ROOTq ROOTq ROOTq ROOTq",
    "ROOTi ROOTi ROOTi ROOTi ROOTi ROOTi ROOTi ROOTi",
    "ROOTw",
    "ROOTq. ROOTi ROOTq ROOTq",
]

DRUM_KITS = {
    "Standard": "42q 42q 36q 38q",
    "Rock":     "36q 38q 36q 38q",
    "Busy":     "42i 42i 42i 42i 36q 38q",
    "World":    "64q 63q 67q 67q",
    "Minimal":  "36w",
}

def load_hmm():
    print(f"Loading HMM from {HMM_FILE}...")
    with open(HMM_FILE, 'r') as f:
        return json.load(f)

def get_next_chord(current_chord, hmm, temperature=1.5):
    if current_chord not in hmm:
        return random.choice(list(hmm.keys()))
    
    transitions = hmm[current_chord]
    candidates = list(transitions.keys())
    original_weights = list(transitions.values())
    
    flattened_weights = [w ** (1.0 / temperature) for w in original_weights]
    total_weight = sum(flattened_weights)
    final_weights = [w / total_weight for w in flattened_weights]
    
    return random.choices(candidates, weights=final_weights, k=1)[0]

def generate_advanced_melody(root_note, quality, duration_beats=4):
    """
    Generates a melody that IS ACTUALLY TUNED to the chord.
    """
    # 1. Scale Definition
    if "min" in quality: intervals = [0, 2, 3, 5, 7, 8, 10]
    elif "dim" in quality: intervals = [0, 3, 6, 9]
    elif "aug" in quality: intervals = [0, 4, 8, 10]
    else: intervals = [0, 2, 4, 5, 7, 9, 11] # Major

    # 2. Convert Root Name to MIDI (Base 60 = Middle C)
    # This prevents the "Simplistic One Note" bug
    base_pitch = 60 + NOTE_OFFSETS.get(root_note, 0)
    
    melody_string = ""
    current_scale_idx = 4 
    
    rhythm_type = random.choice(["quarters", "eighths", "mixed"])
    if rhythm_type == "quarters": steps, note_len = 4, "q"
    elif rhythm_type == "eighths": steps, note_len = 8, "i"
    else: steps, note_len = 8, "i"

    for _ in range(steps):
        rest_prob = 0.1 if rhythm_type == "quarters" else 0.25
        
        if random.random() < rest_prob:
            melody_string += f"R{note_len} "
        else:
            # Contour Logic
            move_options = [-2, -1, 0, 1, 2]
            if current_scale_idx > 10: weights = [0.4, 0.3, 0.2, 0.1, 0.0]
            elif current_scale_idx < 2:  weights = [0.0, 0.1, 0.2, 0.3, 0.4]
            else: weights = [0.15, 0.25, 0.2, 0.25, 0.15]
            
            step_size = random.choices(move_options, weights=weights, k=1)[0]
            if random.random() < 0.10: step_size = random.choice([-4, -3, 3, 4]) # Leap

            current_scale_idx += step_size
            
            # --- THE FIX: Calculate Actual MIDI Pitch ---
            try:
                # Wrap index to scale length
                safe_idx = current_scale_idx % len(intervals)
                
                # Calculate Octave Shift (Floor division)
                octave_shift = current_scale_idx // len(intervals)
                
                # MIDI Math: Base + Interval + (Octave * 12)
                interval_semitones = intervals[safe_idx]
                final_midi = base_pitch + interval_semitones + (octave_shift * 12)
                
                # Append raw MIDI number (no brackets!)
                melody_string += f"{final_midi}{note_len} " 
            except:
                melody_string += f"{base_pitch}{note_len} "

    return melody_string

def get_bass_note(root, quality):
    # Bass math: Root + 12 semitones down (Octave 3 approx)
    # We return MIDI number directly for safety
    base = 36 + NOTE_OFFSETS.get(root, 0) # 36 is C2
    return f"{base}"

def generate_song_and_labels(hmm, song_id):
    # Select Instruments
    my_chord_inst = random.choice(CHORD_INSTRUMENTS)
    my_bass_inst = random.choice(BASS_INSTRUMENTS)
    my_drum_kit_name = random.choice(list(DRUM_KITS.keys()))
    my_melody_inst = random.choice(MELODY_INSTRUMENTS)
    
    # Select Rhythms
    my_chord_rhythm = random.choice(CHORD_RHYTHMS)
    my_bass_rhythm = random.choice(BASS_RHYTHMS)
    my_drum_pattern = DRUM_KITS[my_drum_kit_name]
    
    bpm = random.randint(60, 160)
    
    num_measures = 40 
    beats_per_measure = 4
    seconds_per_beat = 60.0 / bpm
    seconds_per_measure = beats_per_measure * seconds_per_beat
    
    # Init Layers
    v0 = f"V0 I{my_chord_inst} "
    v1 = f"V1 I{my_bass_inst} "
    v9 = "V9 "
    v2 = f"V2 I{my_melody_inst} "
    
    current_chord = "C:maj"
    current_time = 0.0
    label_entries = []
    
    for _ in range(num_measures):
        current_chord = get_next_chord(current_chord, hmm)
        
        # LABEL
        start_t = current_time
        end_t = current_time + seconds_per_measure
        label_entries.append(f"{start_t:.4f}\t{end_t:.4f}\t{current_chord}")
        current_time += seconds_per_measure
        
        # HANDLE N
        if current_chord == 'N':
            v0 += "Rw "
            v1 += "Rw "
            v2 += "Rw "
            v9 += my_drum_pattern + " "
        else:
            if ":" in current_chord: root, quality = current_chord.split(":")
            else: root, quality = current_chord, "maj"
                
            jf_qual = quality.split("(")[0]
            if jf_qual == "hdim7": jf_qual = "min7b5"
            
            chord_token = f"{root}{jf_qual}"
            bass_note = get_bass_note(root, quality)
            
            v0 += my_chord_rhythm.replace("R", chord_token) + " "
            v1 += my_bass_rhythm.replace("ROOT", bass_note) + " "
            v9 += my_drum_pattern + " "
            
            # Now uses the TUNED generator
            v2 += generate_advanced_melody(root, quality, 4) + " "

    label_filename = os.path.join(LABELS_DIR, f"START_SONG_{song_id}.lab")
    with open(label_filename, 'w') as f:
        for entry in label_entries:
            f.write(entry + "\n")
            
    return f"{v0}\n{v1}\n{v9}\n{v2}"

if __name__ == "__main__":
    hmm = load_hmm()
    print(f"Generating {NUM_FILES_TO_GENERATE} diverse, tuned songs...")
    
    try:
        with open(OUTPUT_TXT_FILE, 'w') as f:
            for i in range(NUM_FILES_TO_GENERATE):
                if i % 100 == 0: print(f"Processing song {i}...")
                try:
                    score = generate_song_and_labels(hmm, i)
                    f.write(f"START_SONG_{i}\n")
                    f.write(score + "\n")
                    f.write("END_SONG\n")
                    if i % 10 == 0: f.flush() 
                except Exception as e:
                    print(f"CRITICAL ERROR at Song {i}: {e}")
                    continue
                    
        print(f"Done! Scores in {OUTPUT_TXT_FILE}, Labels in {LABELS_DIR}/")
        
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")