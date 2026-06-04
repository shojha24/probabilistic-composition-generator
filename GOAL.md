# GOAL.md

## Ideal Architecture
The target architecture utilizes a key-relative, genre-conditioned hierarchical system that relies on the normalized dataset's empirical distributions to generate rich extensions and realistic inversions, outputting strictly in Harte syntax.

### 1. Two-Level, Genre-Conditioned Progression Generator
To prevent data sparsity and Frankenstein progressions, the generator is split into two stochastic levels, separated by genre umbrellas (e.g., Pop/Rock from `billboard`/`isophonics`, Jazz from `ireal-pro`/`weimar-jazz`, Classical from `when-in-rome`).

* **Level 1 (Structural Skeleton):** A **Key-Relative transition matrix** calculating movement via semitone distance from the tonic. This level models the movement of the Root interval and the Triad class (`maj`, `min`, `dim`, `aug`, `sus2`, `sus4`, `5`). 
  * *Example:* If the generated key is C Major, an HMM transition from `0_maj` to `+7_maj` mathematically outputs the Harte strings `C:maj` -> `G:maj`. This perfectly maps functional movement while retaining Harte output.
* **Level 2 (Empirical Extension Sampler):** Conditioned on the Genre and the Level 1 structural skeleton, this layer independently samples the extensions (7th, 9th, 11th, 13th) based on exact probabilities learned from that specific corpus. 
  * *Example:* If Level 1 generates a dominant chord in the Jazz HMM, Level 2 looks at the Jazz distribution for dominant chords and has a high probability of sampling a `#11` or `b9`. 

### 2. Voice-Leading & Inversion Layer (The "Algorithmic Bassist")
Because slash chords and inversions were cleanly preserved in the normalization phase, the bass layer no longer requires guesswork. 
* Use the dataset's empirical distributions to sample bass notes (e.g., picking `/3` or `/5`).
* Apply a post-generation cost-function filter to the sequence (penalizing bass leaps > a 4th, rewarding stepwise motion or common tones) to smooth out the generated basslines into realistic Harte slash-chords.

### 3. Rendering Interoperability
* **Build the Harte-to-JFugue Dictionary:** Create a systematic mapping function for the Java layer. Update the JFugue renderer to parse the bass note and append the correct inversion syntax (e.g., turning `C:maj/E` into the proper JFugue string `Cmaj^E`).
* **Complex Multi-Voice Overdubbing:** If a sampled 6D chord is too complex for a single JFugue preset (e.g., mutually exclusive dual-alterations), use JFugue's multi-voice syntax to overlay the isolated extension on a separate channel (e.g., rendering the base triad on Voice 0, and the specific `#11` pitch on Voice 1).
* **Fix Dynamic MIDI Jitter (HumanizedMidiRenderer.java):** Calculate the jitter dynamically based on the track's generated BPM rather than a flat `JITTER_TICKS = 15`. Cache the jitter offset applied to a NOTE_ON event and apply that exact offset to the corresponding NOTE_OFF event to preserve precise duration.
* **Defer Melody for Now:** Disable the disconnected melody generator. Focus entirely on ensuring the Bass, Drums, and Chord instrument layers render perfectly.

### 4. Factored Annotation Output (ChordFormer Standard)
The `.lab` annotation files must represent chords as structured objects to directly feed downstream PyTorch ACR DataLoaders. The generator must output files matching this exact 10-column format:
`time_start  time_end  root  triad  bass  7th  9th  11th  13th  harte_shorthand`

*(Note: The `bb7` class is theoretically important for fully diminished chords and must NOT be collapsed to `b7` in the output logs).*

### Project Phasing
* **Phase 1: Transition & Probability Extraction:** Script the extraction of the key-relative Level 1 matrices and Level 2 extension/bass distributions from the normalized CSVs, partitioned by genre.
* **Phase 2: The Generator Engine:** Build the Python module that generates the synthetic sequences (Tonic/Key selection -> Genre Selection -> Sequence Generation -> Harte String Output).
* **Phase 3: The Synthesizer & Annotator:** Feed the strings to JFugue to render the WAV/MIDI, and simultaneously write the 10-column `.lab` files.