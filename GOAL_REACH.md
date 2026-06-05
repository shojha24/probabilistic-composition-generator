# GOAL.md

## Ideal Architecture
The target architecture utilizes a key-relative, genre-conditioned hierarchical system that relies on the normalized dataset's empirical distributions to generate rich extensions and realistic inversions, outputting strictly in Harte syntax and multi-voice, multi-timbral MIDI.

### 1. Two-Level, Genre-Conditioned Progression Generator [COMPLETED]
To prevent data sparsity and Frankenstein progressions, the generator is split into two stochastic levels, separated by four distinct genre umbrellas (Pop/Rock, Jazz, Classical, and Folk).

* **Level 1 (Structural Skeleton):** A **Key-Relative transition matrix** calculating movement via semitone distance from the tonic. Models the movement of the Root interval and Triad class. The 'N' (silence) state is intentionally skipped to prevent corrupting functional harmonic grammar.
* **Level 2 (Empirical Extension Sampler):** Independently samples the extensions (7th, 9th, 11th, 13th) based on exact probabilities learned from the specific genre corpus. 

### 2. Voice-Leading & Inversion Layer (The "Algorithmic Bassist") [COMPLETED]
Because slash chords and inversions were cleanly preserved in the normalization phase, the bass layer relies on empirical ground truth. 
* Uses the dataset's empirical distributions to sample root-relative bass notes.
* Applies a post-generation cost-function filter across a **Dynamic Tone Pool** (evaluating triad tones + any actively generated extensions) to smooth out generated basslines into realistic Harte slash-chords without destroying complex jazz voicings.

### 3. Rendering Interoperability & Acoustic Interference ("Fat Python / Thin Java") [ACTIVE]
To enforce a strict separation of concerns, all musical logic, structural sequencing, acoustic interference, and instrument assignments are handled in Python. Java acts solely as a "thin" synthesizer.

* **The Ensemble Randomizer (Timbral Diversity):** For every generated track, Python randomly assigns unique JFugue instrument patches to the distinct musical roles to prevent the ACR model from overfitting to a single timbre. 
  * *Example Assignment:* Voice 0 (Base Chords) = Synth Pad; Voice 1 (Bass) = Fretless Bass; Voice 2 (Extensions) = Electric Piano; Voice 3 (Arpeggiator) = Flute.
* **Complex Multi-Voice Overdubbing (Python):** Python explicitly constructs isolated MIDI channels using the randomized instruments to bypass Java preset limitations (e.g., rendering the base triad on Voice 0, and mutually exclusive `#11`/`13` extensions on Voice 2).
* **The Structural Injector (Python):** A pre-render step injects explicit `N` states into the `.lab` sequence and `R` (Rests) into the JFugue string for intros, outros, and drop-outs, ensuring the ACR model learns the null hypothesis (silence).
* **Acoustic Interference - The 80/20 Arpeggiator (Python):** Replaces traditional melody. A dedicated synthesizer voice plays 16th/32nd notes to create acoustic masking. Uses an 80/20 split: 80% of notes are drawn from the active chord's Dynamic Tone Pool, and 20% are random chromatic Non-Chord Tones (NCTs). NCTs are strictly placed on weak off-beats to simulate human dissonance without invalidating ground-truth labels.
* **Transient Interference - Drum Sequencer (Python):** A basic rhythmic loop (Kick/Snare/Hi-hat) is assigned to MIDI Channel 9 to introduce broadband noise, forcing the ACR model to differentiate between transient percussion and chordal attacks.
* **Dynamic MIDI Jitter (Java):** The Java layer reads the pre-compiled Staccato string. It intercepts MIDI `NOTE_ON` events to apply BPM-scaled Gaussian jitter, caching and applying the exact same offset to the corresponding `NOTE_OFF` events to preserve precise duration.

### 4. Factored Annotation Output (ChordFormer Standard) [COMPLETED]
The `.lab` annotation files represent chords as structured objects to directly feed downstream PyTorch ACR DataLoaders. The generator outputs files matching this exact 10-column format:
`time_start  time_end  root  triad  bass  7th  9th  11th  13th  harte_shorthand`

*(Note: The `bb7` class is theoretically important for fully diminished chords and is preserved in the output logs).*

### Project Phasing
* **[DONE] Phase 1: Transition & Probability Extraction**
* **[DONE] Phase 2: The Generator Engine**
* **[ACTIVE] Phase 3: The Synthesizer & Annotator:** Upgrade Python to output complete multi-voice, multi-instrument JFugue Staccato strings (including randomized patches, drums, 80/20 arpeggiator, and structural rests). Upgrade Java to act as a thin renderer that reads the strings, applies BPM-scaled jitter, and exports to WAV/MIDI.