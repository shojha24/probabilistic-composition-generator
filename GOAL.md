# GOAL.md

## Ideal Architecture
The target architecture utilizes a key-relative, genre-conditioned hierarchical system that relies on the normalized dataset's empirical distributions to generate rich extensions and realistic inversions, outputting strictly in Harte syntax.

### 1. Two-Level, Genre-Conditioned Progression Generator [COMPLETED]
To prevent data sparsity and Frankenstein progressions, the generator is split into two stochastic levels, separated by four distinct genre umbrellas (Pop/Rock from `billboard`/`isophonics`, Jazz from `ireal-pro`/`weimar-jazz`, Classical from `when-in-rome`, and Folk from `nottingham`).

* **Level 1 (Structural Skeleton):** A **Key-Relative transition matrix** calculating movement via semitone distance from the tonic. This level models the movement of the Root interval and the Triad class (`maj`, `min`, `dim`, `aug`, `sus2`, `sus4`, `5`). 
  * *Note:* The 'N' (silence) state is intentionally skipped during transition extraction to prevent "silence gravity wells" from corrupting functional harmonic grammar.
* **Level 2 (Empirical Extension Sampler):** Conditioned on the Genre and the Level 1 structural skeleton, this layer independently samples the extensions (7th, 9th, 11th, 13th) based on exact probabilities learned from that specific corpus. 

### 2. Voice-Leading & Inversion Layer (The "Algorithmic Bassist") [COMPLETED]
Because slash chords and inversions were cleanly preserved in the normalization phase, the bass layer relies on empirical ground truth. 
* Uses the dataset's empirical distributions to sample root-relative bass notes.
* Applies a post-generation cost-function filter (penalizing bass leaps > a 4th, rewarding stepwise motion or common tones) across a **Dynamic Tone Pool** (evaluating the triad tones + any actively generated extensions like `#11`) to smooth out the generated basslines into realistic Harte slash-chords without destroying complex jazz voicings.

### 3. Rendering Interoperability & Structural Injection [ACTIVE]
* **The Structural Injector (Handling Silence):** Because the HMM generates continuous harmony, a pre-render Python step must inject explicit `N` states (silence/rests) into the `.lab` sequence for intros, outros, and occasional drum breaks, ensuring the downstream ACR model learns to predict the absence of a chord.
* **Build the Harte-to-JFugue Dictionary:** Create a systematic mapping function for the Java layer. Update the JFugue renderer to parse the bass note and append the correct inversion syntax (e.g., turning `C:maj/E` into the proper JFugue string `Cmaj^E`).
* **Complex Multi-Voice Overdubbing:** If a sampled 6D chord is too complex for a single JFugue preset (e.g., mutually exclusive dual-alterations), use JFugue's multi-voice syntax to overlay the isolated extension on a separate channel (e.g., rendering the base triad on Voice 0, and the specific `#11` pitch on Voice 1).
* **Fix Dynamic MIDI Jitter (HumanizedMidiRenderer.java):** Calculate the jitter dynamically based on the track's generated BPM rather than a flat `JITTER_TICKS = 15`. Cache the jitter offset applied to a NOTE_ON event and apply that exact offset to the corresponding NOTE_OFF event to preserve precise duration.
* **Defer Melody for Now:** Disable the disconnected melody generator. Focus entirely on ensuring the Bass, Drums, and Chord instrument layers render perfectly.

### 4. Factored Annotation Output (ChordFormer Standard) [COMPLETED]
The `.lab` annotation files represent chords as structured objects to directly feed downstream PyTorch ACR DataLoaders. The generator outputs files matching this exact 10-column format:
`time_start  time_end  root  triad  bass  7th  9th  11th  13th  harte_shorthand`

*(Note: The `bb7` class is theoretically important for fully diminished chords and is preserved in the output logs).*

### Project Phasing
* **[DONE] Phase 1: Transition & Probability Extraction:** Scripted the extraction of the key-relative Level 1 matrices and Level 2 extension/root-relative bass distributions from the normalized CSVs, partitioned into 4 genre umbrellas.
* **[DONE] Phase 2: The Generator Engine:** Built the Python module that generates the synthetic sequences, runs the Algorithmic Bassist, translates absolute pitches, and outputs 10-column `.lab` files.
* **[ACTIVE] Phase 3: The Synthesizer & Annotator:** Feed the strings to JFugue to render the WAV/MIDI, inject structural silence, and implement multi-voice rendering for complex chords.