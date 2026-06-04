# CONTEXT.md

## AAM via Large-Vocabulary HMM Chord Progressions: Feasibility & Pitfalls

### Is it implementable?
Yes — the core pipeline is sound and the two codebases are architecturally compatible. The probabilistic generator already handles the hardest part (data-driven chord progressions) and the ASG codebase shows exactly what the downstream Java/JFugue rendering layer needs to look like. The massive hurdle of raw data standardization has been successfully solved via the `normalize_datasets.py` pipeline.

### What the pipeline gets right
1. **The 6D Normalized Vocabulary:** The dataset now perfectly mirrors the target ACR architecture (ChordFormer). Complex classical enharmonics have been mapped to 12-TET, explicit omissions resolved, and massive jazz interval clusters elegantly collapsed into base shorthands with extensions. 
2. **The "5" Triad:** Power chords are structurally preserved, preventing pop/rock data from being hallucinated as major or minor triads.
3. **The Rendering Logic:** The temperature parameter in `get_next_chord()` gives a useful knob for controlling realistic vs. diverse output. `HumanizedMidiRenderer.java` provides crucial Gaussian jitter for attack-detection realism. The `build_master_hmm.py` data ingestion pipeline is robust and bypasses strict JAMS JSON schema traps.

### The Solved Pitfalls (Previous Iterations)
* **The Key-Agnostic Problem:** *Solved.* Previously, the HMM tracked absolute chords (e.g., C to G), diluting transition probabilities. Because every song now has a verified key annotation, transitions can be calculated in a **key-relative semitone space** (e.g., tracking a transition from `0_maj` to `+7_maj`). This mathematically preserves function across all keys without ever needing Roman Numeral strings.
* **The Messy Extension Problem:** *Solved.* The dataset now reliably factors into the `(root, triad, bass, 7th, 9th, 11th, 13th)` format required for training.

### The Remaining Challenge: Stylistic Coherence
Because the datasets range from classical to folk to hard bop, a single "master" HMM would generate harmonically incoherent Frankenstein progressions. The pipeline must remain partitioned by **genre umbrellas** (e.g., Pop/Rock, Jazz, Classical) during transition matrix calculation to preserve the unique syntactic grammar of each musical style.

### Summary for Implementation
The annotation layer is cleanly factored and the normalization is complete. The immediate code tasks are: 
1. Build the key-relative, genre-conditioned HMM transition matrices.
2. Build the empirical extension and bass samplers.
3. Finalize the Harte-to-JFugue rendering pipeline using the multi-voice hack for edge cases.
4. Restructure the label output from flat strings to the 10-column structured tuples.