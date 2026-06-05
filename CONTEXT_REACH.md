# CONTEXT.md

## AAM via Large-Vocabulary HMM Chord Progressions: Feasibility & Pitfalls

### Current State of the Pipeline
The core Python Machine Learning pipeline (Phases 1 & 2) is complete and verified. The immediate focus shifts to expanding Python's responsibilities to handle multitrack logic (instrument randomization, arpeggiator noise, drums), and configuring Java as a "thin" audio renderer (Phase 3).

### What the pipeline gets right
1. **The 6D Normalized Vocabulary:** Achieved a 0% failure rate parsing 461,000+ chords into standard Harte syntax, perfectly mirroring ChordFormer's target ACR architecture.
2. **Timbral Diversity & Masking:** The pipeline explicitly avoids the "pristine piano" trap by implementing an Ensemble Randomizer. It assigns generated bass, chord pads, extensions, and arpeggios to diverse MIDI instruments, simulating realistic, multi-timbral bands.
3. **Dynamic Voice-Leading:** The generator creates smooth inversions while utilizing a dynamic tone pool to protect valid extensions (e.g., `#11` in the bass) from being overwritten.
4. **The Rendering Logic:** `HumanizedMidiRenderer.java` provides crucial Gaussian jitter for attack-detection realism.

### The Solved Pitfalls (Previous Iterations)
* **The Key-Agnostic Problem:** *Solved.* Transitions are calculated in a **key-relative semitone space**.
* **The Bass Inversion Problem:** *Solved.* Bass is tracked relative to the *Root*, ensuring inversions are learned universally regardless of the key.
* **The Silence Gravity Well:** *Solved.* The `N` (silence/no-chord) state was removed from the HMM transition matrices. Silence is instead handled structurally by a post-generation Python injector.
* **The Acoustic Cheat Sheet:** *Solved.* To prevent the ACR model from simply transcribing the highest, loudest synth line, the 80/20 Arpeggiator injects probabilistic, rapid Non-Chord Tones (NCTs) on weak beats to provide "safe dissonance."

### The Remaining Challenge: The "Fat Python / Thin Java" Bridge
Previously, JFugue (Java) was expected to parse complex Harte strings and calculate voicings and instrumentations. This created a bottleneck where ML logic was bleeding into the audio rendering layer. The new architecture dictates that Python acts as the "Fat Logic Layer"—calculating exact multi-voice MIDI strings, instrument patches, drums, and arpeggios—while Java acts purely as a "Thin Rendering Layer," accepting pre-computed text files and applying timing humanization.

### Summary for Implementation
The immediate code tasks for Phase 3 are:
1. **Python - The Ensemble Randomizer:** Build a function to assign unique JFugue instrument patches (`I[Fretless_Bass]`, `I[Electric_Piano]`, etc.) to distinct Voice channels for each generated song.
2. **Python - Structural Injector:** Pad the `.lab` sequences with `N` states and the JFugue strings with `R` (Rests) for intros/outros/drop-outs.
3. **Python - The 80/20 Arpeggiator & Drum Sequencer:** Generate probabilistic 16th-note melody strings (using the Dynamic Tone Pool + 20% chromatic dirt) and basic Channel 9 drum loops to create acoustic masking.
4. **Python - Multi-Voice String Builder:** Translate the 10-column `.lab` tuples into raw, multi-voice JFugue Staccato syntax strings mapped to the randomized instruments.
5. **Java - Thin Renderer:** Refactor the Java layer to read the massive text string outputted by Python, parse it, apply BPM-scaled MIDI jitter in `HumanizedMidiRenderer.java`, and export the audio files.