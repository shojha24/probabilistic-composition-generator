# CONTEXT.md

## AAM via Large-Vocabulary HMM Chord Progressions: Feasibility & Pitfalls

### Current State of the Pipeline
The core Python Machine Learning pipeline (Phases 1 & 2) is complete and verified. The probabilistic generator has successfully replaced heuristic guesswork with empirical, genre-conditioned statistical grammar. The immediate focus shifts strictly to the Java/JFugue rendering layer (Phase 3).

### What the pipeline gets right
1. **The 6D Normalized Vocabulary:** The dataset perfectly mirrors the target ACR architecture (ChordFormer). Phase 1 achieved a 0% failure rate parsing 461,000+ chords into standard Harte syntax.
2. **The "5" Triad:** Power chords are structurally preserved, preventing pop/rock data from being hallucinated as major or minor triads.
3. **Dynamic Voice-Leading:** The generator successfully creates smooth inversions (e.g., stepping `F:maj` to `C:maj/E`) while utilizing a dynamic tone pool to protect valid extensions (e.g., `#11` in the bass) from being overwritten.
4. **The Rendering Logic:** The temperature parameter in `get_next_chord()` gives a useful knob for controlling realistic vs. diverse output. `HumanizedMidiRenderer.java` provides crucial Gaussian jitter for attack-detection realism.

### The Solved Pitfalls (Previous Iterations)
* **The Key-Agnostic Problem:** *Solved.* Transitions are calculated in a **key-relative semitone space** (e.g., tracking a transition from `0_maj` to `+7_maj`). This mathematically preserves function across all keys.
* **The Bass Inversion Problem:** *Solved.* Bass is tracked relative to the *Root* (not the Tonic), ensuring inversions are learned universally regardless of the key.
* **The Silence Gravity Well:** *Solved.* The `N` (silence/no-chord) state was removed from the HMM transition matrices to prevent it from corrupting functional harmonic grammar. Silence is instead handled structurally during the final rendering phase.

### The Remaining Challenge: Audio Rendering (Java Interoperability)
The generator now outputs mathematically perfect, highly complex Harte strings (e.g., `C:7(b9,11)/b9`). The legacy JFugue codebase was designed for simple AAM triads. The remaining challenge is upgrading the Java renderer to parse these complex 10-column `.lab` tuples and render them into audio without dropping notes or crashing.

### Summary for Implementation
The Python generation layer is locked. The immediate code tasks for Phase 3 are: 
1. Build the "Structural Injector" in Python to pad the sequences with `N` states for intros/outros.
2. Update the Java layer to parse the `.lab` files instead of raw Harte strings.
3. Implement the **Harte-to-JFugue Multi-Voice Hack** in Java to render mutually exclusive or unsupported extensions (`9`, `11`, `13`) across multiple isolated MIDI voices.
4. Refactor the MIDI Jitter logic to be dynamically scaled by BPM.