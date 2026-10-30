# Ground-Truth Specification: Probabilistic Composition Generator

**Section 3 Reference Document for ISMIR 2026 Preprint**  
**Repository Audit & Ground-Truth Extraction**  
**Target File:** `context_vault/FACTS_GENERATOR.md`  
**Date of Audit:** October 30, 2026  
**Auditor:** Staff Research Engineer & Technical Auditor  

---

## 1. High-Level Pipeline & Entrypoints

### 1.1 Dataset Generation Entrypoints & CLI Execution Flow

The production pipeline for generating the 5,000-song synthetic Automatic Chord Recognition (ACR) training corpus executes across three sequential, deterministic CLI entrypoints (`CORPUS_GEN_REFERENCE.md:L49-L144`):

```
+-------------------------------------------------------------------------------------------------------------------------+
| STEP 1: Quota-Aware Symbolic Harmony Generation                                                                         |
| CLI: target_corpus_gen.py                                                                                               |
| Arguments: --target-events 500000 --total-songs 5000 --pop-rock-ratio 0.65 --bimodal-natural-ratio 0.5                  |
|            --triad-target-scale 3.0 --sustained-inversion-rate 0.10 --slice-inversions --debug --seed 15001             |
| Outputs: gen/acr-target-5k/pop-rock-labels/song_{0..3249}.json & gen/acr-target-5k/jazz-labels/song_{0..1749}.json      |
+-------------------------------------------------------------------------------------------------------------------------+
                                                               |
                                                               v
+-------------------------------------------------------------------------------------------------------------------------+
| STEP 2: Canonical Multi-Role JFugue Score Assembly                                                                      |
| CLI: render.py                                                                                                          |
| Arguments: --in-dir ./gen/acr-target-5k/pop-rock-labels --in-dir ./gen/acr-target-5k/jazz-labels                        |
|            --out ./gen/acr-canonical-5k-all-roles.txt --seed 25001 --mode mixed --arpeggio-percent 30                  |
|            --pad-percent 70 --percussion-percent 100 --melody-condition naturalistic --melody-percent 100               |
|            --melody-profile mixed --melody-decoder sequence_beam --stage score                                          |
| Outputs: gen/acr-canonical-5k-all-roles.txt (V0 Chords/Arps, V1 Bass, V2 Melody, V9 Percussion)                         |
+-------------------------------------------------------------------------------------------------------------------------+
                                                               |
                                                               v
+-------------------------------------------------------------------------------------------------------------------------+
| STEP 3: Multi-SoundFont Audio Synthesis & Acoustic Condition Projection                                                 |
| CLI: tools/project_condition_corpus.py                                                                                  |
| Arguments: --canonical ./gen/acr-canonical-5k-all-roles.txt --out-dir ./gen/acr-conditions-5k                            |
|            --conditions naturalistic --seed 25001 --melody-percent 70 --percussion-percent 70 --stage audio             |
|            --soundfont-dir ./soundfonts --gain-jitter-db 3.0 --reverb --detune-cents 25.0                               |
|            --production-fx --power-chord-distortion                                                                     |
| Process:   1. Compiles MIDI via HumanizedMidiRenderer.java (headless JFugue -> MIDI with timing/velocity humanization) |
|            2. Renders isolated FLAC stems per role using FluidSynth CLI and rotated GM SoundFonts                       |
|            3. Mixes stems with stem dropout quotas, low-end DSP, power-chord overdrive, convolution reverb, and loudnorm|
| Outputs: gen/acr-conditions-5k/naturalistic/audio/song_{0..4999}/mix.flac                                               |
+-------------------------------------------------------------------------------------------------------------------------+
                                                               |
                                                               v
+-------------------------------------------------------------------------------------------------------------------------+
| STEP 4: Ground-Truth Time-Aligned Annotation Export                                                                     |
| CLI: convert_synth_labels.py (ChordFormer repository) / MirEval Annotation Importer                                     |
| Input: gen/acr-target-5k/{pop-rock-labels,jazz-labels}/song_{i}.json                                                    |
| Format: Tab/space-delimited .lab format: <time_start> <time_end> <harte_chord_label>                                    |
+-------------------------------------------------------------------------------------------------------------------------+
```

### 1.2 Execution Step Breakdown
1. **Random Seed Derivation & Initialization:**  
   The pipeline takes a global integer seed $S$ (`--seed 15001` in Step 1, `25001` in Steps 2 & 3). Genre streams derive unaliased sub-seeds:
   $$\text{genre\_seed} = S + \text{genre\_index} \times 1{,}000{,}003 \quad (\text{target\_corpus\_gen.py:L1361-L1365})$$
   where $\text{genre\_index} = 0$ for `pop_rock` and $1$ for `jazz`.
   Subsequent processes (song tonics, BPM sampling, inversion slicing, stem gain jitter, detuning) derive independent pseudo-random streams via cryptographic/hashing primitives:
   - Tonic/BPM RNG: $\text{property\_seed} = \text{genre\_seed} + 7{,}919$ (`target_corpus_gen.py:L1379-L1384`).
   - Slicing RNG: $\text{slicing\_seed} = \text{genre\_seed} + 19{,}841 + \text{song\_index}$ (`target_corpus_gen.py:L1503-L1507`).
   - Java MIDI Renderer seed: 64-bit FNV-1a hash over `"$ROOT_SEED:humanization:$ordinal"` (`HumanizedMidiRenderer.java:L143-L154`).
   - SoundFont selection & Audio FX seeds: 64-bit FNV-1a hash over `"$ROOT_SEED:$domain:$ordinal"` (`sound_design.py:L74-L84`).

2. **Symbolic Harmony Generation:**  
   `target_corpus_gen.py` samples chord sequences of length $N = \lfloor \frac{E_{\text{genre}}}{S_{\text{genre}}} \rfloor$. Chords are generated through a 3-stage backoff model (order-2 categorical HMM for root/triad, autoregressive bass Markov chain, and a 4-slot prefix trie for 7th, 9th, 11th, and 13th extensions). A scheduling quota manager monitors feature deficits and forces overdue rare triads and extensions along attested transition edges.

3. **Sub-Measure Slicing & Sustained Inversions:**  
   `bass_module.py:slice_chord_events` slices whole- and half-note events into sub-measure time-aligned chord records matching authentic bass motion (walking lines, root-5th groove, scalar passing tones, and chromatic approach notes).

4. **Multi-Role Symbolic Arrangement:**  
   `render.py` maps the abstract chord sequence into four parallel JFugue musical tracks:
   - **Track `V0`:** Chords / Comping (30% Arpeggio patterns, 70% Sustained pads/voicings) via one of six instrument-specific voicer policies (`voicing/voicers/`).
   - **Track `V1`:** Bass line generated by `bass_module.py:BassModule` constrained to standard 4-string bass tessitura ($E_1$ to $G_3$, MIDI 28 to 55).
   - **Track `V2`:** Monophonic melodic lead generated by `melody_module.py:generate_melody` via bounded sequence beam search or chord-centered random walk.
   - **Track `V9`:** Synchronized GM percussion groove on MIDI channel 10 generated by `percussion_module.py:PercussionModule`.

5. **MIDI Synthesis & Sim-to-Real Audio Rendering:**  
   JFugue scores are compiled to standard Type-1 MIDI files via `HumanizedMidiRenderer.java`, applying micro-timing jitter, per-channel pocket bias, guitar strum stagger, piano damper pedal (CC64), and velocity humanization. `audio_render.py` synthesizes isolated role stems (`.flac`) via `FluidSynth` with rotating multi-SoundFont banks, followed by FFmpeg mix-bus DSP (saturation, split-band reverb, EQ tilt, dynamic bus compression, and LUFS loudness normalization).

6. **Ground-Truth Label Export:**  
   Each song's final sub-measure chord boundaries ($t_{\text{start}}, t_{\text{end}}$ in seconds) and canonical Harte notation labels are serialized to JSON and converted to `.lab` ground-truth files for model evaluation.

### 1.3 Global Dataset Parameters

| Parameter | Code Variable / Source Location | Exact Value / Formula / Distribution |
| :--- | :--- | :--- |
| **Total Song Count** | `target_corpus_gen.py:L575-L579`, `--total-songs` | $5{,}000$ songs total |
| **Pop/Rock vs. Jazz Ratio** | `target_corpus_gen.py:L53,L1581-L1585`, `--pop-rock-ratio` | $0.65$ pop/rock ($3{,}250$ songs), $0.35$ jazz ($1{,}750$ songs) |
| **Total Target Chord Events** | `target_corpus_gen.py:L52,L1561-L1565`, `--target-events` | $500{,}000$ total events ($325{,}000$ pop/rock, $175{,}000$ jazz) |
| **Average Song Length (Events)** | `target_corpus_gen.py:L1237` | $100$ chord events per song ($\frac{500{,}000}{5{,}000}$) |
| **Song Duration (Seconds)** | `target_corpus_gen.py:L912,L1003` | Dependent on BPM and duration tokens; mean $\approx 180 - 240\text{ s}$ |
| **Tempo (BPM) Bounds** | `target_corpus_gen.py:L1278,L1599`, `--random-bpm-range` | $U_{\mathbb{Z}}(60, 180)\text{ BPM}$ when `--random-bpm` or `--debug` is active |
| **Default Fixed BPM** | `target_corpus_gen.py:L1268,L1591`, `--bpm` | $120\text{ BPM}$ ($0.5\text{ s}$ per quarter-note beat) |
| **Meter / Time Signature** | `percussion_module.py:L21,L96`, `instruments.py:L162` | Standard $4/4$ meter; 16th-note subdivision grid ($4\text{ slots/beat}$) |
| **Key / Tonality Distribution** | `target_corpus_gen.py:L1385-L1389`, `--random-tonic` | $U_{\mathbb{Z}}(0, 11)$ uniform over all 12 pitch classes when `--random-tonic` or `--debug` is set |
| **Initial Song State Bias** | `chord_gen.py:L832-L846`, `_first_state()` | Prefers tonic `0_major` or `0_minor` if attested; else frequency-weighted draw over Stage 1 Level-0 unigrams |

---

## 2. Chord Vocabulary & Structural Representation

### 2.1 Pitch Classes & Root Spelling Conventions

All pitch classes are internally indexed as integer semitones modulo 12 relative to the global piece tonic $k \in \{0, 1, \dots, 11\}$ (`chord_gen.py:L60-L68`):

$$\text{Pitch Class (PC)} = (\text{root\_interval} + \text{tonic\_pc}) \pmod{12}$$

Spelling mappings defined in `PC_TO_NOTE` and `NOTE_TO_PC` (`chord_gen.py:L60-L68`):
- **Output canonical pitch-class names (`PC_TO_NOTE`):**  
  `{0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F", 6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}`
- **Parser recognition dictionary (`NOTE_TO_PC`):**  
  Accepts naturals, sharps, flats, double-sharps/flats, and enharmonics:
  `{"C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4, "F": 5, "E#": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "Cb": 11}`.

### 2.2 Triad Quality Vocabulary

The generator implements a closed vocabulary of 8 distinct triad types (`extract_distributions.py:L120-L137`, `voicing/types.py:L18-L27`):

| Triad Identifier | Code Name | Semitone Intervals Above Root $(3\text{rd}, 5\text{th})$ | Harte Quality Equivalent | Description |
| :--- | :--- | :--- | :--- | :--- |
| `major` | `"major"` | $(+4, +7)$ | `maj` | Major triad |
| `minor` | `"minor"` | $(+3, +7)$ | `min` | Minor triad |
| `diminished` | `"diminished"` | $(+3, +6)$ | `dim` | Diminished triad (flat 5) |
| `augmented` | `"augmented"` | $(+4, +8)$ | `aug` | Augmented triad (sharp 5) |
| `sus4` | `"sus4"` | $(+5, +7)$ | `sus4` | Suspended 4th triad |
| `sus2` | `"sus2"` | $(+2, +7)$ | `sus2` | Suspended 2nd triad |
| `5` | `"5"` | $(\text{None}, +7)$ | `5` | Power chord / bare fifth |
| `1` | `"1"` | $(\text{None}, \text{None})$ | `1` | Unison / octave fundamental |

> [!NOTE]
> While early design documents referenced a 7-way triad vocabulary, `extract_distributions.py:L136` and `chord_gen.py:L78-L80` explicitly added an 8th triad token, `"1"` (bare fundamental), derived from Billboard unisons. The generator handles `"1"` as a first-class triad type.

### 2.3 Seventh, Extension & Alteration Vocabulary

Four ordered slots model upper harmony (`chord_gen.py:L159`, `extract_distributions.py:L142-L170`, `voicing/types.py:L30-L35`):
$$\mathbf{E} = (e_{\text{seventh}}, e_{\text{ninth}}, e_{\text{eleventh}}, e_{\text{thirteenth}})$$

1. **Seventh Slot ($e_{\text{seventh}}$):**
   - `"N"`: Absent / no seventh ($0\text{ semitones}$).
   - `"b7"`: Minor 7th ($+10\text{ semitones}$, dominant/minor quality).
   - `"7"`: Major 7th ($+11\text{ semitones}$, major quality).
   - `"bb7"`: Diminished 7th or added 6th ($+9\text{ semitones}$).
     > [!IMPORTANT]
     > `extract_distributions.py:L83-L87, L151` and `chord_gen.py:L83-L87` intentionally map both diminished-7th chords and 6th chords (`maj6`, `min6`, `6`) to `"bb7"`. When paired with a `major` triad, `("major", "bb7")` resolves to Harte shorthand `maj6`; paired with `minor`, it resolves to `min6`; paired with `diminished`, it resolves to `dim7`.

2. **Ninth Slot ($e_{\text{ninth}}$):**
   - `"N"`: Absent.
   - `"9"`: Natural 9th / major 2nd ($+2\text{ semitones}$ mod 12).
   - `"b9"`: Minor 9th / flat 9 ($+1\text{ semitone}$ mod 12).
   - `"#9"`: Augmented 9th / sharp 9 ($+3\text{ semitones}$ mod 12).

3. **Eleventh Slot ($e_{\text{eleventh}}$):**
   - `"N"`: Absent.
   - `"11"`: Perfect 11th / natural 4th ($+5\text{ semitones}$ mod 12).
   - `"#11"`: Augmented 11th / sharp 4 ($+6\text{ semitones}$ mod 12).

4. **Thirteenth Slot ($e_{\text{thirteenth}}$):**
   - `"N"`: Absent.
   - `"13"`: Major 13th / natural 6th ($+9\text{ semitones}$ mod 12).
   - `"b13"`: Minor 13th / flat 6 ($+8\text{ semitones}$ mod 12).

### 2.4 Degree Collision-Merge Rule

When compound extensions land on the same pitch class as a suspended or altered triad tone, `voicing/types.py:L179-L201` applies deterministic collision merges to prevent duplicate pitches in the symbolic degree representation:

| Collision Condition | Lower Role Collapsed | Higher Role Retained | Absorbed Semitone (Mod 12) |
| :--- | :--- | :--- | :--- |
| `seventh == "bb7"` and `thirteenth == "13"` | `7th` | `13th` (`merged_from=("7th",)`) | $+9\text{ semitones}$ |
| `triad == "sus4"` and `eleventh == "11"` | `3rd` | `11th` (`merged_from=("3rd",)`) | $+5\text{ semitones}$ |
| `triad == "sus2"` and `ninth == "9"` | `3rd` | `9th` (`merged_from=("3rd",)`) | $+2\text{ semitones}$ |
| `triad == "diminished"` and `eleventh == "#11"` | `5th` | `11th` (`merged_from=("5th",)`) | $+6\text{ semitones}$ |
| `triad == "augmented"` and `thirteenth == "b13"` | `5th` | `13th` (`merged_from=("5th",)`) | $+8\text{ semitones}$ |

### 2.5 Inversions & Bass Note Representation

The bass pitch class is represented as an offset interval $\Delta_{\text{bass}} \in \{0, 1, \dots, 11\}$ above the chord root (`chord_gen.py:L1145`):
$$\text{Bass PC} = (\text{Root PC} + \Delta_{\text{bass}}) \pmod{12}$$
- $\Delta_{\text{bass}} = 0$: Root position (no slash notation).
- $\Delta_{\text{bass}} \neq 0$: Slash inversion (`Root:Quality/BassDegree`).
- **Sustained Inversions (`target_corpus_gen.py:L1009-L1022`):**  
  Active with probability $p = 0.10$ on whole (`w`) and half (`h`) note chords:
  $$P(\text{Bass} = \text{3rd} \mid \text{inversion}) = 0.60, \quad P(\text{Bass} = \text{5th} \mid \text{inversion}) = 0.40$$

### 2.6 No-Chord (`N`) Handling

Silence / No-Chord events are injected as a post-generation filter (`chord_gen.py:L653-L716`, `target_corpus_gen.py:L1487-L1492`):
- **Omission Rate:** $1.0\%$ of total events (`--no-chord-rate 0.01`).
- **Quota Safeguard (`is_no_chord_eligible`, `chord_gen.py:L638-L646`):**  
  An event is eligible for `N` replacement if and only if:
  $$\text{event} \in \{\text{major}, \text{minor}\} \ \land \ \Delta_{\text{bass}} = 0 \ \land \ e_{\text{seventh}} = e_{\text{ninth}} = e_{\text{eleventh}} = e_{\text{thirteenth}} = \text{"N"}$$
  This guarantees that rare triads (`sus2`, `sus4`, `aug`, `dim`) and altered extensions are never replaced by `N`, maintaining quotas without deficit.
- **Data Contract:** Replaced events emit `harte = "N"`, `is_no_chord = True`, `root = None`, `triad = None`, and render as explicit MIDI rests (`R<duration>`) across all stems (`render.py:L913`, `bass_module.py:L406`, `percussion_module.py:L229`).

### 2.7 Harte / `mir_eval` Formatting Rules

Formatting is executed by `reconstruct_harte()` (`chord_gen.py:L615-L636`):
1. **Base Quality Shorthand Table (`QUALITY_SHORTHAND`):**
   - `("major", "N"): "maj"`, `("major", "b7"): "7"`, `("major", "7"): "maj7"`, `("major", "bb7"): "maj6"`
   - `("minor", "N"): "min"`, `("minor", "b7"): "min7"`, `("minor", "7"): "minmaj7"`, `("minor", "bb7"): "min6"`
   - `("diminished", "N"): "dim"`, `("diminished", "b7"): "hdim7"`, `("diminished", "bb7"): "dim7"`
   - `("augmented", "N"): "aug"`, `("augmented", "b7"): "aug7"`
   - `("sus4", "N"): "sus4"`, `("sus4", "b7"): "7sus4"`
   - `("sus2", "N"): "sus2"`
   - `("5", "N"): "5"`, `("1", "N"): "1"`
2. **Extensions:** Formatted in comma-separated parentheses: `"(<ext1>,<ext2>,...)"` for non-`"N"` values among ninth, eleventh, and thirteenth (e.g., `C:7(9,#11)`).
3. **Bass Note:** Appended via forward slash `"/" + bass_name` if $\text{Bass} \neq \text{Root}$ (e.g., `C:maj/E` or `C:maj/3`).
4. **Sub-measure Slicing Notation (`bass_module.py:L169-L170, L222-L226`):**
   Bass passing lines emit interval-degree slash labels: `INTERVAL_TO_DEGREE = {0: "1", 1: "b2", 2: "2", 3: "b3", 4: "3", 5: "4", 6: "#4", 7: "5", 8: "b6", 9: "6", 10: "b7", 11: "7"}`.

---

## 3. Probabilistic Progression Sampling & Rare-Chord Upweighting

### 3.1 Three-Stage Generative Formulation

The generative distribution factorizes each chord event $C_t$ into three stages:
$$P(C_t \mid C_{t-1}, C_{t-2}) = P(S_t \mid S_{t-1}, S_{t-2}) \cdot P(\text{Bass}_t \mid \text{Root}_t, \text{Bass}_{t-1}) \cdot P(\mathbf{E}_t \mid S_t, \text{Bass}_t, \mathbf{E}_{t-1})$$
where $S_t = (\text{Root}_t, \text{Triad}_t)$ represents the harmonic backbone state.

#### Stage 1: Order-2 Harmonic Backbone HMM with Interpolated Backoff
Stage 1 models the joint state $S_t \in \{0, \dots, 11\} \times \{\text{triads}\}$ (`chord_gen.py:L272-L317`). It combines three backoff levels using Witten-Bell confidence weights:
$$P_1(S_t) = \lambda_1(n_1) \cdot P_{\text{L1}}(S_t \mid S_{t-1}) + (1 - \lambda_1(n_1)) \cdot P_{\text{L0}}(S_t)$$
$$P_{\text{final}}(S_t) = \lambda_2(n_2) \cdot P_{\text{L2}}(S_t \mid S_{t-1}, S_{t-2}) + (1 - \lambda_2(n_2)) \cdot P_1(S_t)$$
where:
$$\lambda(n, D) = \frac{n}{n + D} \quad \text{with shared discount constant } D = 25.0 \quad (\text{chord\_gen.py:L200, L229-L233})$$
- $n_1 = \sum_{s} C_{\text{L1}}(S_{t-1}, s)$ and $n_2 = \sum_{s} C_{\text{L2}}(S_{t-2}, S_{t-1}, s)$.
- **Laplace Smoothing:** $P(s) = \frac{C(s) + k}{\sum C + k \cdot |V|}$ with $k = 0.0$ default (`chord_gen.py:L201, L212-L226`).
- **Level-1 Softmax Tempering:**
  $$P_{\text{L1}}(S_t \mid S_{t-1}) \propto \left[ P_{\text{raw}}(S_t \mid S_{t-1}) \right]^{1 / \tau_1}$$
  with $\tau_{1, \text{pop\_rock}} = 2.2$ and $\tau_{1, \text{jazz}} = 2.0$ (`chord_gen.py:L185, L236-L244`).
- **$\epsilon$-Floor Marginal Mixing:** Prevents zero probability in unobserved contexts by blending $\epsilon = 0.02$ ($2.0\%$) unigram marginal floor:
  $$P_{\text{floored}}(S_t) = (1 - \epsilon) P_{\text{final}}(S_t) + \epsilon P_{\text{L0}}(S_t) \quad (\text{chord\_gen.py:L204, L253-L265})$$
- **Self-Transition Repeat Suppression:** Exact repeats ($S_t = S_{t-1}$) are discounted by $\delta = 0.20$ (`chord_gen.py:L190, L319-L336`):
  $$P(S_t = S_{t-1}) \leftarrow \delta \cdot P(S_t = S_{t-1})$$
  with the removed probability mass $(1 - \delta) P(S_t = S_{t-1})$ redistributed proportionally across all $s \neq S_{t-1}$.

#### Stage 2: Autoregressive Bass Markov Chain
Stage 2 samples $\Delta_{\text{bass}} \in \{0, \dots, 11\}$ (`chord_gen.py:L379-L401`):
$$P(\text{Bass}_t \mid \text{Root}_t, \text{Bass}_{t-1}) = \lambda_1(n_1) \cdot P_{\text{L1b}}(\text{Bass}_t \mid \text{Root}_t, \text{Bass}_{t-1}) + (1 - \lambda_1(n_1)) \cdot P_{\text{L0}}(\text{Bass}_t \mid \text{Root}_t)$$
where $\lambda_1 = \frac{n_1}{n_1 + 25.0}$.

#### Stage 3: Four-Slot Trie Walk
Stage 3 samples $\mathbf{E}_t = (e_{\text{7th}}, e_{\text{9th}}, e_{\text{11th}}, e_{\text{13th}})$ (`chord_gen.py:L449-L569`):
1. **Trie Selection Cutover:**
   $$\text{Selected Trie} = \begin{cases} 
   \text{Trie A } (\text{per } S_t) & \text{if } N(\text{Trie A}) \ge 200 \ (\text{min\_count\_a}) \\ 
   \text{Trie B } (\text{per } \text{Triad}_t) & \text{if } N(\text{Trie B}) \ge 500 \ (\text{min\_count\_b}) \\ 
   \text{Trie C } (\text{genre-wide `ALL`}) & \text{otherwise} 
   \end{cases}$$
   Low-count Triad B tries ($0 < N < 500$) interpolate with Trie C via weight $\lambda = \frac{N}{N + D}$ (`chord_gen.py:L427-L446`).
2. **Node-Level Backoff:**
   At each depth $d \in \{1, 2, 3, 4\}$, node child selection backoffs:
   $$P(e_d) = \lambda_2 P_{\text{L2}}(e_d \mid \text{Bass}_t, \mathbf{E}_{t-1}) + (1 - \lambda_2) \left[ \lambda_1 P_{\text{L1}}(e_d \mid \text{Bass}_t) + (1 - \lambda_1) P_{\text{L0}}(e_d) \right]$$

### 3.2 Bimodal Mixture & Rare-Chord Quota Scheduling

To prevent representational collapse onto common diatonic triads while safeguarding natural transition distributions, the 5,000-song corpus is partitioned using a bimodal architecture (`target_corpus_gen.py:L1402-L1486`):
- **Bimodal Ratio (`--bimodal-natural-ratio 0.5`):**  
  - **Cohort 1 ("natural", 50% = 2,500 songs):** Generated unconstrained from the empirical distributions. Events produced naturally count toward corpus quotas.
  - **Cohort 2 ("targeted", 50% = 2,500 songs):** Governed by `GenerationQuota`, which enforces deterministic scheduling of rare triads, rare extensions, and dense extension stacks.

#### Scheduling Due-Position Formula
For any target class $k$ with remaining quota $Q_k$ and current count $c_k$, the target becomes *due* at position (`target_corpus_gen.py:L405-L408`):
$$\text{pos}_{\text{due}}(k) = \left\lfloor \frac{(c_k + 1) \cdot T + (Q_k + B) - 1}{Q_k + B} \right\rfloor$$
where $T$ is the total event budget, and $B$ is the schedule buffer ($B = 50$ for triads/extensions, $B = 5000$ for dense chords).
- **Target Spacing Constraint:** Consecutive targeted chords must be separated by at least $\Delta_{\text{pos}} \ge 2$ events (`min_target_spacing = 2`, relaxed to $1$ if target density $> 35\%$).
- **Attested Edge Constraint (`target_corpus_gen.py:L653-L743`):** Target states are *never* injected randomly. The generator searches 1-step and 2-step (bridged) successors observed in training data:
  $$\text{Weight}(s_{\text{target}}) = \frac{P_{\text{dist1}}(s_{\text{target}}) \cdot C_{\text{local}}(s_{\text{target}})}{1 + \text{RootCount}(s_{\text{target}})}$$
  This guarantees that rare chords are only sounded within grammatically valid harmonic contexts.

### 3.3 Empirical vs. Targeted Probability Distributions

The table below contrasts empirical frequencies (extracted from 1,114 Billboard songs and 2,477 iRealPro charts) against the upweighted quotas enforced by `--triad-target-scale 3.0` (`target_corpus_gen.py:L62-L103`):

| Harmonic Class | Feature Token | Empirical Pop/Rock % ($N=139{,}078$) | Targeted Pop/Rock Quota (per 175k events) | Empirical Jazz % ($N=161{,}022$) | Targeted Jazz Quota (per 75k events) | Upweight Factor vs. Empirical Baseline |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Major Triad** | `major` | $70.41\%$ | Diatonic backbone | $62.81\%$ | Diatonic backbone | $1.0\times$ (Natural baseline) |
| **Minor Triad** | `minor` | $21.98\%$ | Diatonic backbone | $28.95\%$ | Diatonic backbone | $1.0\times$ (Natural baseline) |
| **Suspended 4th** | `sus4` | $2.82\%$ | $6{,}000 \times 3.0 = 18{,}000$ | $2.43\%$ | $2{,}500 \times 3.0 = 7{,}500$ | $\mathbf{3.65\times - 4.11\times}$ |
| **Suspended 2nd** | `sus2` | $0.24\%$ | $7{,}000 \times 3.0 = 21{,}000$ | $0.14\%$ | $1{,}200 \times 3.0 = 3{,}600$ | $\mathbf{34.2\times - 50.1\times}$ |
| **Diminished** | `diminished` | $0.53\%$ | $2{,}500 \times 3.0 = 7{,}500$ | $4.13\%$ | $3{,}500 \times 3.0 = 10{,}500$ | $\mathbf{3.38\times - 8.11\times}$ |
| **Augmented** | `augmented` | $0.23\%$ | $1{,}200 \times 3.0 = 3{,}600$ | $0.94\%$ | $1{,}200 \times 3.0 = 3{,}600$ | $\mathbf{5.11\times - 8.84\times}$ |
| **Power Chord** | `5` | $2.12\%$ | $4{,}500 \times 3.0 = 13{,}500$ | $0.60\%$ | $800 \times 3.0 = 2{,}400$ | $\mathbf{3.65\times - 5.33\times}$ |
| **Unison** | `1` | $1.67\%$ | $3{,}000 \times 3.0 = 9{,}000$ | $0.00\%$ | $0$ | $\mathbf{3.09\times}$ (Pop only) |
| **Extension: $\flat 7$** | `b7` | $20.44\%$ | $90{,}000$ ($51.4\%$) | $59.60\%$ | $40{,}000$ ($53.3\%$) | $0.89\times - 2.51\times$ |
| **Extension: $\flat 9$** | `b9` | $0.07\%$ | $700$ | $1.21\%$ | $2{,}000$ | $\mathbf{2.20\times - 7.15\times}$ |
| **Extension: $\sharp 9$** | `#9` | $0.03\%$ | $4{,}000$ | $0.85\%$ | $1{,}500$ | $\mathbf{2.35\times - 95.8\times}$ |
| **Extension: $11$** | `11` | $0.89\%$ | $2{,}500$ | $1.42\%$ | $2{,}000$ | $\mathbf{1.60\times - 1.88\times}$ |
| **Extension: $\sharp 11$**| `#11` | $0.02\%$ | $1{,}000$ | $1.15\%$ | $1{,}500$ | $\mathbf{1.74\times - 36.1\times}$ |
| **Extension: $13$** | `13` | $0.31\%$ | $1{,}200$ | $1.28\%$ | $1{,}500$ | $\mathbf{1.56\times - 2.78\times}$ |
| **Extension: $\flat 13$**| `b13` | $0.02\%$ | $1{,}200$ | $0.82\%$ | $1{,}500$ | $\mathbf{2.44\times - 42.8\times}$ |
| **Density: 2+ Ext** | $d \ge 2$ | $1.85\%$ | $15{,}000$ ($8.57\%$) | $12.4\%$ | $15{,}000$ ($20.0\%$) | $\mathbf{1.61\times - 4.63\times}$ |
| **Density: 3+ Ext** | $d \ge 3$ | $0.12\%$ | $3{,}000$ ($1.71\%$) | $1.85\%$ | $3{,}000$ ($4.00\%$) | $\mathbf{2.16\times - 14.3\times}$ |
| **Density: 4 Ext** | $d = 4$ | $0.01\%$ | $800$ ($0.46\%$) | $0.21\%$ | $1{,}000$ ($1.33\%$) | $\mathbf{6.33\times - 46.0\times}$ |

### 3.4 Harmonic Rhythm & Duration Sampling

Chords are assigned duration tokens sampled categorically from empirical genre weights (`chord_gen.py:L161-L178`):

| Duration Token | Musical Meaning | Beats ($4/4$ Meter) | Pop/Rock Probability ($P_{\text{pop}}$) | Jazz Probability ($P_{\text{jazz}}$) | Default / Neutral Weight |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `"ww"` | Double Whole Note | $8.0\text{ beats}$ | $0.02$ ($2.0\%$) | $0.01$ ($1.0\%$) | $0.08$ |
| `"w."` | Dotted Whole Note | $6.0\text{ beats}$ | $0.03$ ($3.0\%$) | $0.01$ ($1.0\%$) | $0.07$ |
| `"w"` | Whole Note | $4.0\text{ beats}$ | $\mathbf{0.60}$ ($\mathbf{60.0\%}$) | $\mathbf{0.30}$ ($\mathbf{30.0\%}$) | $0.50$ |
| `"h."` | Dotted Half Note | $3.0\text{ beats}$ | $0.05$ ($5.0\%$) | $0.08$ ($8.0\%$) | $0.09$ |
| `"h"` | Half Note | $2.0\text{ beats}$ | $\mathbf{0.20}$ ($\mathbf{20.0\%}$) | $\mathbf{0.40}$ ($\mathbf{40.0\%}$) | $0.13$ |
| `"q."` | Dotted Quarter Note | $1.5\text{ beats}$ | $0.05$ ($5.0\%$) | $0.05$ ($5.0\%$) | $0.05$ |
| `"q"` | Quarter Note | $1.0\text{ beat}$ | $0.05$ ($5.0\%$) | $\mathbf{0.15}$ ($\mathbf{15.0\%}$) | $0.08$ |

$$\Delta t_{\text{seconds}} = \text{duration\_beats} \times \left(\frac{60}{\text{BPM}}\right)$$

---

## 4. Symbolic Realization: Voicings, Voice Leading & Track Roles

### 4.1 Track Roles & STEM Architecture

Every piece is composed across four distinct symbolic tracks (`render.py:L76-L86`, `audio_render.py:L35-L42`):
- **Track `V0` (Chords / Comping):** Sustained pads or rhythmic arpeggios realizing chord harmony.
- **Track `V1` (Bass):** Acoustic, electric, or synth bass carrying root motion, inversions, and walking lines.
- **Track `V2` (Melody):** Monophonic lead vocal, wind, brass, sax, or solo guitar line.
- **Track `V9` (Percussion):** General MIDI drum kit (Channel 10) providing metric and groove anchoring.

### 4.2 Voicing Engine Architecture: The 6 Voicer Policies

Realization of `V0` is executed by six dedicated voicer policies (`voicing/voicers/`):

```
+------------------------------------------------------------------------------------------------------------------+
| CANDIDATE GENERATION (select.py & candidates.py)                                                                 |
| 1. Select tones: 5th thinning (p_omit_fifth), root omission gate (root_omission_p), root doubling (root_double_p) |
| 2. Compute Distinguishing Chord Tone (DCT): highest-ranked differentiator (13th > 11th > 9th > 7th > 3rd > 5th)  |
| 3. Candidate pool generation: piano/guitar/synth templates, hand splits, fretboard shapes, drop-2 voicings       |
+------------------------------------------------------------------------------------------------------------------+
                                                        |
                                                        v
+------------------------------------------------------------------------------------------------------------------+
| EIGHT-LEVEL RELAXATION LADDER (engine.py:L341-L361)                                                              |
| L0: Nominal constraints (window, max_voices, max_doublings, cluster_cap)                                        |
| L1-L2: Secondary candidate generation fallback                                                                   |
| L3: Increase max_doublings by +1                                                                                 |
| L4: Increase max_voices by +1                                                                                    |
| L5: Force 5th omission on major/minor triads                                                                     |
| L6: Widen pitch window by +/- 12 semitones (1 octave)                                                            |
| L7: Relax DCT exposure branch (allow top, isolated, or octave)                                                   |
+------------------------------------------------------------------------------------------------------------------+
                                                        |
                                                        v
+------------------------------------------------------------------------------------------------------------------+
| HARD PRUNING & REJECTION FILTERS (engine.py:L200-L229, post_filter)                                              |
| - Low-Interval Limits (LIL): enforce critical bandwidth in bass register                                         |
| - Semitone Cluster Cap: max allowable adjacent semitone pairs                                                    |
| - Guitar Physicality: max fret span <= 4 (penalty if 5), max fret <= 12 or 15                                    |
| - DCT Exposure: DCT cannot be buried under upper root doublings (1-2 semitones above)                            |
+------------------------------------------------------------------------------------------------------------------+
                                                        |
                                                        v
+------------------------------------------------------------------------------------------------------------------+
| SOFT COST OPTIMIZATION & SOFTMAX SELECTION (engine.py:L958-L1011)                                                |
| Cost(c) = w_vl * VL + w_drift * Drift + w_space * Spacing + w_div * Div + w_role * Role - Bonus_trans            |
| Probability P(c) ~ exp(-(Cost(c) - min Cost) / tau)                                                              |
+------------------------------------------------------------------------------------------------------------------+
```

#### Exact Policy Hyperparameter Matrix

All parameters documented below are verbatim from source (`voicing/voicers/`):

| Hyperparameter | Symbol | `pop-piano` (`01`) | `pop-guitar` (`02`) | `pop-synth` (`03`) | `jazz-piano` (`04`) | `jazz-guitar` (`05`) | `jazz-synth` (`06`) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Pitch Range** | $[w_{\text{lo}}, w_{\text{hi}}]$ | $[43, 88]$ ($G_2 - E_6$) | $[40, 76]$ ($E_2 - E_5$) | $[36, 96]$ ($C_2 - C_7$) | $[48, 84]$ ($C_3 - C_6$) | $[45, 79]$ ($A_2 - G_5$) | $[40, 91]$ ($E_2 - G_6$) |
| **Voice Limits** | $[V_{\text{min}}, V_{\text{max}}]$ | $[3, 7]$ | $[3, 6]$ | $[3, 8]$ | $[3, 6]$ | $[3, 5]$ | $[3, 7]$ |
| **5th Omission Prob.** | $p_{\text{omit\_5th}}$ | $0.15$ | $0.35$ | $0.10$ | $0.60$ | $0.70$ | $0.45$ |
| **Root Doubling Prob.**| $p_{\text{double\_root}}$| $0.70$ | $0.60$ | $0.50$ | $0.05$ | $0.03$ | $0.15$ |
| **Root Omission Prob.**| $p_{\text{omit\_root}}$ | $0.02$ | $0.08$ | $0.20$ | $0.70$ | $0.75$ | $0.55$ |
| **Max Doublings** | $D_{\text{max}}$ | $3$ | $2$ | $4$ | $1$ | $1$ | $3$ |
| **Centroid Drift Free**| $\delta_{\text{free}}$ | $5\text{ st}$ | $5\text{ st}$ | $6\text{ st}$ | $4\text{ st}$ | $4\text{ st}$ | $5\text{ st}$ |
| **Centroid Drift Tol.** | $\delta_{\text{tol}}$ | $11\text{ st}$ | $10\text{ st}$ | $12\text{ st}$ | $8\text{ st}$ | $9\text{ st}$ | $11\text{ st}$ |
| **Softmax Temperature**| $\tau$ | $3.5$ | $5.0$ | $6.0$ | $2.2$ | $3.5$ | $3.0$ |
| **Weight: Voice Lead** | $w_{\text{vl}}$ | $1.0$ | $0.7$ | $0.5$ | $1.6$ | $1.2$ | $1.1$ |
| **Weight: Drift** | $w_{\text{drift}}$ | $0.7$ | $0.9$ | $1.0$ | $0.8$ | $0.9$ | $0.95$ |
| **Weight: Spacing** | $w_{\text{space}}$ | $0.6$ | $0.3$ | $0.9$ | $0.7$ | $0.3$ | $0.8$ |
| **Weight: Diversity** | $w_{\text{div}}$ | $1.1$ | $1.2$ | $1.3$ | $0.9$ | $1.0$ | $1.1$ |
| **Weight: Role/Shape** | $w_{\text{role}}$ | $0.8$ | $1.4$ | $0.7$ | $1.0$ | $1.3$ | $0.8$ |
| **PLR Trans. Bonus** | $B_{\text{plr}}$ | $1.5$ | $0.5$ | $0.4$ | $3.0$ | $2.0$ | $2.0$ |
| **Unmatched Penalty** | $P_{\text{unmatched}}$ | $2.0$ | $2.0$ | $1.2$ | $2.5$ | $2.0$ | $1.5$ |
| **VL Target Mean/SD** | $\mu_{\text{vl}} \pm \sigma_{\text{vl}}$ | $8.4 \pm 4.2$ | $9.5 \pm 5.0$ | $11.0 \pm 6.0$ | $6.0 \pm 3.5$ | $7.5 \pm 4.0$ | $7.0 \pm 4.0$ |
| **DCT Mode: Top** | $P(\text{top})$ | $0.50$ | $0.65$ | $0.35$ | $0.55$ | $0.60$ | $0.40$ |
| **DCT Mode: Isolated** | $P(\text{isolated})$ | $0.20$ | $0.30$ | $0.35$ | $0.40$ | $0.38$ | $0.30$ |
| **DCT Mode: Octave** | $P(\text{octave})$ | $0.30$ | $0.05$ | $0.30$ | $0.05$ | $0.02$ | $0.30$ |

### 4.3 ISMIR Rigor: Component Omission Guarantees

Reviewers must know whether labeled chord tones are sounded in audio:
1. **Upper Extensions (9th, 11th, 13th):**
   **STRICTLY SOUNDED AND GUARANTEED.** In `voicing/select.py:L32-L33`, extensions are *never* dropped during tone selection:
   > *"Extensions are NEVER dropped here -- every active extension slot survives into degrees unconditionally, per §4.1's hardest rule."*
   In `pop-piano`, `pop-synth`, `jazz-piano`, and `jazz-synth`, 100% of labeled extensions are realized. In `pop-guitar` and `jazz-guitar`, extension omission is permitted *solely* as an unplayable physical fallback beneath Step 8 of the ladder (`voicing/engine.py:L319-L334`), with a massive penalty of $+60.0 \times N_{\text{dropped}}$ (`voicing/voicers/pop_guitar.py:L105-L107`), maintaining a dataset-wide dropped extension rate $< 0.5\%$.
2. **Third ($3\text{rd}$):**
   **STRICTLY GUARANTEED.** Thirds are never dropped. In guitar voicings, Hazard 2 (`voicing/voicers/pop_guitar.py:L136-L143`) explicitly rejects any shape missing a 3rd to prevent false classification as a power chord (`5`).
3. **Fifth ($5\text{th}$):**
   Freely omittable *only* in plain `major` and `minor` triads with probability $p_{\text{omit\_5th}}$. In `diminished`, `augmented`, `sus4`, and `sus2` triads, the 5th defines harmonic quality and is **strictly preserved** (`voicing/select.py:L48`).
4. **Root Note:**
   Omission is only permitted when the independent Bass track (`V1`) is active (`bass_module_active = True`) and satisfies the rootless gate (`voicing/select.py:L54-L73`), ensuring the fundamental is always sounded in the low end.

### 4.4 Voice-Leading Optimization & Transformation Penalties

Candidate cost $C(c)$ is evaluated against previous voicing $p_{\text{prev}}$ (`voicing/engine.py:L960-L994`):
$$C(c) = w_{\text{vl}} \cdot d_{\text{vl}}(p_{\text{prev}}, p_c) - B_{\text{trans}}(C_{\text{prev}}, C) - 0.5 \min(|p_{\text{prev}} \cap p_c|, 3) + w_{\text{drift}} \cdot \text{Drift} + w_{\text{space}} \cdot \text{Space} + w_{\text{div}} \cdot \text{Div} + w_{\text{role}} \cdot \text{Role}$$
- **Optimal Voice-Leading Distance ($d_{\text{vl}}$):** Evaluated via the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`) over absolute semitone distances with an unmatched voice penalty $P_{\text{unmatched}}$ (`voicing/vl.py:L21-L40`).
- **Neo-Riemannian PLR Transformation Bonus:** $B_{\text{trans}} = B_{\text{plr}}$ for Parallel ($P$), Leittonwechsel ($L$), or Relative ($R$) triad shifts; $0.5 B_{\text{plr}}$ for D-type shifts or 4-27 set-class (dominant 7th / half-diminished) common-tone connections (`voicing/vl.py:L138-L152`).
- **Common-Tone Retention Bonus:** $-0.5 \times \min(|p_{\text{prev}} \cap p_c|, 3)$ semitones.

### 4.5 Rhythm, Arpeggiator Dynamics & Humanization

#### Arpeggio Performance Profiles (`instruments.py:L172-L238`, Track `V0` 30% mode)
- `keyboard-ringing`: fast threshold $120\text{ BPM}$, base velocity 84, gate ratio 1.50, pattern weights `{up: 0.40, down: 0.25, up_down: 0.25, outside_in: 0.10}`.
- `keyboard-dry`: fast threshold $120\text{ BPM}$, base velocity 88, gate ratio 0.80, pattern weights `{up: 0.35, down: 0.30, up_down: 0.25, outside_in: 0.10}`.
- `guitar-ringing`: fast threshold $90\text{ BPM}$, base velocity 78, gate ratio 1.75, pattern weights `{up: 0.45, down: 0.30, up_down: 0.25, outside_in: 0.0}`.
- `guitar-muted`: fast threshold $110\text{ BPM}$, base velocity 92, gate ratio 0.65, pattern weights `{up: 0.40, down: 0.35, up_down: 0.25, outside_in: 0.0}`.
- `synth-pulse`: fast threshold $150\text{ BPM}$, base velocity 96, gate ratio 0.85, pattern weights `{up: 0.45, down: 0.20, up_down: 0.25, outside_in: 0.10}`.

#### MIDI Sim-to-Real Humanization (`HumanizedMidiRenderer.java:L62-L72, L317-L384`)
- **Timing Jitter:** Target $\sigma_{\text{jitter}} = 15.0\text{ ms}$; converted to ticks via tempo meta-event: $\text{tickSigma} = \frac{15.0}{\text{msPerTick}}$.
- **Per-Channel Pocket Bias:**
  - Track `V0` (Pad/Chords): $0.0\text{ ms}$ (on the beat)
  - Track `V1` (Bass): $+8.0\text{ ms}$ (behind the beat, pocket feel)
  - Track `V2` (Melody): $-4.0\text{ ms}$ (ahead of the beat, airy lead feel)
  - Track `V0` (Arpeggio): $-6.0\text{ ms}$ (rushing slightly)
  - Track `V9` (Drums): $+2.0\text{ ms}$ (laid-back groove)
- **Guitar Strum Stagger:** Chords played on guitar patches (GM 24–31) execute a $7.0\text{ ms}$ per-string attack delay (`Math.round(7.0 / msPerTick)`), alternating downstroke (low to high string) on even chords and upstroke (high to low string) on odd chords.
- **Piano Damper Pedal:** Chords played on piano patches (GM 0–7) generate MIDI CC 64 (Sustain Pedal) events: value 127 at chord onset, release (value 0) $1\text{ slot}$ before next onset.
- **Velocity Jitter:** Gaussian velocity noise:
  $$v_{\text{humanized}} = \text{clip}\left(\text{round}\left(v_{\text{base}} + \mathcal{N}(0, \sigma_v)\right), 1, 127\right)$$
  where $\sigma_v = 8.0$ for harmonic tracks and $\sigma_v = 4.0$ for drum kit hits.

---

## 5. Audio Synthesis & Instrument Assignment

### 5.1 Synthesis Engine & Render Specs
- **Synthesizer:** FluidSynth CLI headless execution (`audio_render.py:L110-L125`):
  `fluidsynth -ni -F <out.wav> -r 22050 <soundfont.sf2> <song.mid>`
- **Sample Rate ($f_s$):** $22{,}050\text{ Hz}$ (`REFERENCE_RENDER_PROFILE`, `sound_design.py:L23`).
- **Bit Depth:** 16-bit PCM (`audio_render.py:L134`, encoded to lossless FLAC via FFmpeg `-c:a flac -sample_fmt s16`).
- **Channels:** Monophonic ($1\text{ channel}$) for raw stems; Master mix exports support both Mono ($1$) and Spatialized Stereo ($2\text{ channels}$).

### 5.2 Multi-SoundFont Ensemble Catalog

The pipeline deterministically rotates across four General MIDI SoundFont libraries per song ordinal (`sound_design.py:L180-L192`):
$$\text{SoundFont Index} = \text{derive\_seed}(\text{seed}, \text{"soundfont-selection"}, \text{ordinal}) \pmod{N_{\text{soundfonts}}}$$
1. `FluidR3_GM.sf2`: Full-frequency multi-sample GM default ($141\text{ MB}$).
2. `MuseScore_General_Full.sf2`: High-fidelity orchestral/acoustic/vocal sample library ($468\text{ MB}$).
3. `GeneralUser_GS.sf2`: Balanced GM/GS soundbank with warm Rhodes, electric guitars, and choir pads ($31\text{ MB}$).
4. `TimGM6mb.sf2`: Lightweight 90s digital rompler SoundFont ($5.7\text{ MB}$).

### 5.3 Allowed General MIDI (GM) Programs by Track Role

#### Chords / Harmony (`V0`, `instruments.py:L249-L356`)
- **Piano Family:**
  - GM 0: Acoustic Grand Piano (`pads`, `arpeggios`)
  - GM 1: Bright Acoustic Piano (`arpeggios`)
  - GM 3: Honky-Tonk Piano (`arpeggios`)
  - GM 4: Electric Piano 1 (Rhodes) (`pads`)
  - GM 5: Electric Piano 2 (Chorused) (`arpeggios`)
  - GM 6: Harpsichord (`arpeggios`)
  - GM 7: Clavinet (`arpeggios`)
  - GM 11: Vibraphone (`pads`, `arpeggios`)
  - GM 12: Marimba (`arpeggios`)
  - GM 17: Percussive Organ (`bass`, `arpeggios`)
  - GM 18: Rock Organ (`pads`, `bass`)
  - GM 19: Church Organ (`pads`)
  - GM 77: Shakuhachi (`pads`)
  - GM 109: Bagpipe (`pads`)
  - GM 110: Fiddle (`pads`, `arpeggios`)
  - GM 111: Shanai (`pads`)
- **Guitar Family:**
  - GM 24: Acoustic Guitar (Nylon) (`arpeggios`)
  - GM 25: Acoustic Guitar (Steel) (`arpeggios`)
  - GM 26: Electric Guitar (Jazz) (`pads`)
  - GM 27: Electric Guitar (Clean) (`pads`, `arpeggios`)
  - GM 28: Electric Guitar (Muted) (`arpeggios`)
  - GM 29: Overdriven Guitar (`pads`)
  - GM 30: Distortion Guitar (`pads`)
  - GM 31: Guitar Harmonics (`arpeggios`)
  - GM 104: Sitar (`arpeggios`)
  - GM 105: Banjo (`arpeggios`)
  - GM 106: Shamisen (`arpeggios`)
  - GM 107: Koto (`arpeggios`)
- **Synth / Pad Family:**
  - GM 88: New Age Pad (`pads`)
  - GM 89: Warm Pad (`pads`)
  - GM 91: Choir Pad (`pads`)
  - GM 92: Bowed Pad (`pads`)
  - GM 93: Metallic Pad (`pads`)
  - GM 94: Halo Pad (`pads`)
  - GM 95: Sweep Pad (`pads`)
  - GM 50: Synth Strings 1 (`pads`)
  - GM 51: Synth Strings 2 (`pads`)
  - GM 99: Atmosphere (`pads`)
  - GM 100: Soundtrack (`pads`)
  - GM 80: Synth Lead (Square) (`arpeggios`)

#### Bass Track (`V1`, `instruments.py:L358-L394`, Spec 18 §3.3)
Sampled categorically with the following prioritized weights:
- **GM 33 (Electric Bass Finger):** $\mathbf{35.0}$ (Natural warm fundamental)
- **GM 34 (Electric Bass Pick):** $\mathbf{25.0}$ (Transient pick punch)
- **GM 32 (Acoustic Bass / Upright):** $\mathbf{20.0}$ (Woody double-bass body resonance)
- **GM 35 (Fretless Bass):** $\mathbf{15.0}$ (Expressive mid-range growl)
- GM 36 (Slap Bass 1): $1.5$
- GM 38 (Synth Bass 1): $1.5$
- GM 43 (Contrabass): $1.0$
- GM 42 (Cello): $0.5$
- GM 81 (Sawtooth Wave): $0.2$
- GM 87 (Basslead): $0.1$
- GM 37 (Slap Bass 2): $0.08$ (Deprecated)
- GM 39 (Synth Bass 2): $0.08$ (Deprecated)
- GM 80 (Square Wave): $0.04$ (Deprecated)

#### Melody Track (`V2`, `instruments.py:L404-L438`, Spec 17 §3)
- Instruments: GM 40 (Violin), GM 41 (Viola), GM 110 (Fiddle), GM 56 (Trumpet), GM 57 (Trombone), GM 73 (Flute), GM 75 (Pan Flute), GM 77 (Shakuhachi), GM 27 (Clean Guitar), GM 29 (Overdriven Guitar), GM 30 (Distortion Guitar), GM 65 (Alto Sax), GM 66 (Tenor Sax), GM 71 (Clarinet), GM 80 (Square Lead), **GM 52 (Choir Aahs)**, **GM 53 (Voice Oohs)**, **GM 54 (Synth Voice)**.
- **Human Vocal Formants:** GM 52, 53, 54 prioritized in `lead-mid-neutral` and `lead-mid-active` profiles to achieve $\ge 25\%$ vocal lead representation across pop/rock songs.

#### Percussion Track (`V9`, `percussion_module.py:L35-L54`, GM Channel 10)
- **Kick Drum:** `BASS_DRUM` (MIDI 35, prob 0.80), `ACOUSTIC_BASS_DRUM` (MIDI 36, prob 0.20).
- **Snare Drum:** `ACOUSTIC_SNARE` (MIDI 38, prob 0.50), `ELECTRIC_SNARE` (MIDI 40, prob 0.40), `SIDE_STICK` (MIDI 37, prob 0.10).
- **Cymbals / Hi-Hat:** `CLOSED_HI_HAT` (MIDI 42, prob 0.50), `PEDAL_HI_HAT` (MIDI 44, prob 0.20), `RIDE_CYMBAL_1` (MIDI 51, prob 0.15), `RIDE_CYMBAL_2` (MIDI 59, prob 0.15).
- **Crash Cymbal:** `CRASH_CYMBAL_1` (MIDI 49, prob 0.50), `CRASH_CYMBAL_2` (MIDI 57, prob 0.25), `CHINESE_CYMBAL` (MIDI 52, prob 0.25). Downbeat crash probability = $0.60$.
- **Percussion Velocities (Gaussian Jitter, Spec 18 §4.1):**
  - Kick, Snare, Crash: $\mathcal{N}(100.0, 8.0^2)$
  - Ghost Snare (`"s"`): $\mathcal{N}(65.0, 8.0^2)$
  - Hi-Hat / Cymbal: $\mathcal{N}(80.0, 12.0^2)$

---

## 6. Random-Mix Augmentation & Acoustic Post-Processing

### 6.1 Stem Activation & Dropout Quotas

Condition projection (`tools/project_condition_corpus.py:L188-L251`) supports five condition masks:
- `cb`: Chords (`V0`) + Bass (`V1`)
- `cbp`: Chords (`V0`) + Bass (`V1`) + Percussion (`V9`)
- `cbm`: Chords (`V0`) + Bass (`V1`) + Melody (`V2`)
- `cbmp`: All four roles active
- `naturalistic`: Exact quota dropout sampling:
  $$P(\text{Melody Present}) = 0.70 \ (70.0\%), \quad P(\text{Percussion Present}) = 0.70 \ (70.0\%)$$
  producing a naturalistic distribution: $49\%$ 4-role, $21\%$ chord-bass-melody, $21\%$ chord-bass-percussion, and $9\%$ chord-bass core.

### 6.2 Per-Stem Gains & Randomization
- **Nominal Reference Gains (`REFERENCE_RENDER_PROFILE`, `sound_design.py:L30`):**
  - Track `V0` (Chords): $0.0\text{ dB}$
  - Track `V1` (Bass): $+1.0\text{ dB}$ (Spec 18 rebalancing, down from $+3.0\text{ dB}$)
  - Track `V2` (Melody): $-1.0\text{ dB}$
  - Track `V9` (Percussion): $-1.5\text{ dB}$ (Spec 18 rebalancing, up from $-6.0\text{ dB}$ for realistic kick masking)
- **Per-Song Dynamic Gain Jitter (`--gain-jitter-db 3.0`, `sound_design.py:L194-L212`):**
  $$s = \frac{\text{gain\_jitter\_db}}{3.0} = 1.0$$
  - $\Delta G_{\text{V0}} \sim U(-3.0, +3.0)\text{ dB}$
  - $\Delta G_{\text{V1}} \sim U(-3.0, +3.0)\text{ dB}$
  - $\Delta G_{\text{V2}} \sim U(-3.0, +4.0)\text{ dB}$
  - $\Delta G_{\text{V9}} \sim U(-4.0, +4.0)\text{ dB}$

### 6.3 DSP Processing & Production FX Chains

The master mix bus executes an FFmpeg complex filtergraph (`audio_render.py:L233-L357`):

```
+---------------------------------------------------------------------------------------------------------+
| ROLE FILTERING & PRE-PROCESSING                                                                         |
|                                                                                                         |
| V1 (Bass):         --> [aeval='val(0)-0.05*val(0)^3']                                                   |
|                        (soft cubic saturation)                                                          |
|                    --> [lowshelf=f=120:g=2.0:t=q] (low-end body warmth)                                 |
|                    --> [lowpass=f=3500] (GM click suppression) ------------------------+                |
|                                                                                        |                |
| V0 (Power Chords): --> [lowshelf=f=100:g=-3.0] (sub-mud cut)                           |                |
|                        [aeval='(3/2)*val(0)*(1-val(0)^2/3)'] (tube overdrive)          |                |
|                        [highshelf=f=4500:g=-6.0] (cabinet resonance roll-off)          |                |
|                        [lowshelf=f=250:g=2.5] (cabinet punch)                          |                |
|                        [pan=stereo|c0=0.6*c0|c1=0.4*c0] (stereo width +-20%) ----------+                |
|                                                                                        |                |
| V2 (Melody):       --> [pan=stereo|c0=0.5*c0|c1=0.5*c0] (dead center) -----------------+                |
|                                                                                        |                |
| V9 (Drums):        --> [pan=stereo|c0=0.4*c0|c1=0.6*c0] (stereo width -+20%) ----------+                |
+---------------------------------------------------------------------------------------------------------+
                                                                                        |
                                                                                        v
+---------------------------------------------------------------------------------------------------------+
| SUMMING BUS: amix=inputs=N:duration=longest:dropout_transition=0:normalize=0                            |
+---------------------------------------------------------------------------------------------------------+
                                                                                        |
                                                                                        v
+---------------------------------------------------------------------------------------------------------+
| SPLIT-BAND REVERB (Spec 18 §2.1): asplit [dry] & [to_rev]                                               |
|                                                                                                         |
| Wet path:          --> [highpass=f=150] (retains sub-150Hz bass fundamentals dry & phase-coherent)      |
|                    --> [afir] Synthetic RIR convolution (hall: 1.2s, plate: 0.8s, room: 0.4s)          |
|                                                                                                         |
| Recombine:         --> amix=inputs=2:weights=0.82 0.18 (82% Dry / 18% Wet)                              |
+---------------------------------------------------------------------------------------------------------+
                                                                                        |
                                                                                        v
+---------------------------------------------------------------------------------------------------------+
| MICRO-TUNING PITCH DRIFT: asetrate=22050*(2^(cents/1200)), aresample=22050 (cents ~ U(-25, +25))       |
+---------------------------------------------------------------------------------------------------------+
                                                                                        |
                                                                                        v
+---------------------------------------------------------------------------------------------------------+
| STUDIO PRODUCTION FX (Spec 17 §4):                                                                      |
|                                                                                                         |
| 1. Spectral EQ Tilt:   lowshelf=f=1500:g=tilt, highshelf=f=1500:g=-tilt (tilt ~ U(1.5, 3.0) dB)         |
| 2. Tape Saturation:    aeval='val(0)-0.1*val(0)^3|val(1)-0.1*val(1)^3' (analog console saturation)     |
| 3. Dynamic Bus Compand: compand=attacks=0.02:decays=0.15:points=-80/-80|-24/-24|-12/-8|0/-3            |
+---------------------------------------------------------------------------------------------------------+
                                                                                        |
                                                                                        v
+---------------------------------------------------------------------------------------------------------+
| MASTER NORMALIZATION: loudnorm=I=-18.0:TP=-1.0:LRA=7:print_format=none                                  |
+---------------------------------------------------------------------------------------------------------+
```

### 6.4 Ground-Truth Frame Alignment Guarantee
- **Deterministic Mathematical Alignment:** Duration in seconds is calculated analytically:
  $$t_{\text{end}} - t_{\text{start}} = \text{duration\_beats} \times \frac{60.0}{\text{BPM}}$$
- **Zero Start Padding:** FFmpeg and FluidSynth are executed with `start_padding_frames = 0` (`sound_design.py:L26`), guaranteeing sample 0 corresponds exactly to $t = 0.000\text{ s}$.
- **Release Preservation:** Reverb tail policy is set to `preserve_release` (`sound_design.py:L27`), while label boundaries terminate at the harmonic bar line, matching human annotation standards in real MIR benchmarks (Billboard, Isophonics).

---

## 7. Master Hyperparameter Summary Table for LaTeX

This table compiles every hyperparameter, mathematical symbol, exact value or range, and code provenance across the repository:

| Module / System | Parameter Description | Math Symbol | Exact Value / Range | Provenance File & Line Number |
| :--- | :--- | :--- | :--- | :--- |
| **Global Pipeline** | Total Synthetic Songs | $N_{\text{songs}}$ | $5{,}000$ | `CORPUS_GEN_REFERENCE.md:L52`, `target_corpus_gen.py:L52` |
| **Global Pipeline** | Total Chord Events | $N_{\text{events}}$ | $500{,}000$ | `CORPUS_GEN_REFERENCE.md:L51`, `target_corpus_gen.py:L52` |
| **Global Pipeline** | Pop/Rock Proportion | $r_{\text{pop}}$ | $0.65$ ($65.0\%$) | `CORPUS_GEN_REFERENCE.md:L53`, `target_corpus_gen.py:L53` |
| **Global Pipeline** | Bimodal Natural Ratio | $r_{\text{nat}}$ | $0.50$ ($50.0\%$) | `CORPUS_GEN_REFERENCE.md:L54`, `target_corpus_gen.py:L1619` |
| **Global Pipeline** | Rare Triad Target Scale | $S_{\text{triad}}$ | $3.0\times$ (default $2.5\times$) | `CORPUS_GEN_REFERENCE.md:L55`, `target_corpus_gen.py:L1613` |
| **Global Pipeline** | Sustained Inversion Rate | $p_{\text{inv}}$ | $0.10$ ($10.0\%$) | `CORPUS_GEN_REFERENCE.md:L56`, `target_corpus_gen.py:L1631` |
| **Global Pipeline** | No-Chord Injection Rate | $p_{\text{NC}}$ | $0.01$ ($1.0\%$) | `target_corpus_gen.py:L1279, L1608`, `chord_gen.py:L656` |
| **Global Pipeline** | Tempo Sampling Bounds | $\text{BPM}$ | $U_{\mathbb{Z}}(60, 180)$ | `target_corpus_gen.py:L1278, L1599` |
| **Markov Model** | Witten-Bell Discount Constant | $D$ | $25.0$ | `chord_gen.py:L200`, `GenParams.discount` |
| **Markov Model** | Laplace Smoothing Parameter | $k$ | $0.0$ | `chord_gen.py:L201`, `GenParams.add_k` |
| **Markov Model** | Level-1 Temperature (Pop/Rock)| $\tau_{1, \text{pop}}$ | $2.2$ | `chord_gen.py:L185`, `GENRES_TO_TEMPS` |
| **Markov Model** | Level-1 Temperature (Jazz) | $\tau_{1, \text{jazz}}$| $2.0$ | `chord_gen.py:L185`, `GENRES_TO_TEMPS` |
| **Markov Model** | Repeat Discount Factor | $\delta$ | $0.20$ | `chord_gen.py:L190`, `GENRES_TO_SELF_TRANSITION_DISCOUNT` |
| **Markov Model** | Epsilon Floor Mixing Mass | $\epsilon$ | $0.02$ ($2.0\%$) | `chord_gen.py:L204`, `GenParams.epsilon_floor` |
| **Markov Model** | Trie A Cutover Count | $N_A$ | $\ge 200$ | `chord_gen.py:L202`, `GenParams.min_count_a` |
| **Markov Model** | Trie B Cutover Count | $N_B$ | $\ge 500$ | `chord_gen.py:L203`, `GenParams.min_count_b` |
| **Markov Model** | Target Buffer Constant | $B$ | $50$ (dense: $5000$) | `target_corpus_gen.py:L304-L305` |
| **Voicing Engine** | Pop Piano Pitch Range | $[w_{\text{lo}}, w_{\text{hi}}]$ | $[43, 88]$ ($G_2 - E_6$) | `voicing/voicers/pop_piano.py:L129` |
| **Voicing Engine** | Pop Guitar Pitch Range | $[w_{\text{lo}}, w_{\text{hi}}]$ | $[40, 76]$ ($E_2 - E_5$) | `voicing/voicers/pop_guitar.py:L183` |
| **Voicing Engine** | Pop Synth Pitch Range | $[w_{\text{lo}}, w_{\text{hi}}]$ | $[36, 96]$ ($C_2 - C_7$) | `voicing/voicers/pop_synth.py:L23-L24, L340` |
| **Voicing Engine** | Jazz Piano Pitch Range | $[w_{\text{lo}}, w_{\text{hi}}]$ | $[48, 84]$ ($C_3 - C_6$) | `voicing/voicers/jazz_piano.py:L193` |
| **Voicing Engine** | Jazz Guitar Pitch Range | $[w_{\text{lo}}, w_{\text{hi}}]$ | $[45, 79]$ ($A_2 - G_5$) | `voicing/voicers/jazz_guitar.py:L87` |
| **Voicing Engine** | Jazz Synth Pitch Range | $[w_{\text{lo}}, w_{\text{hi}}]$ | $[40, 91]$ ($E_2 - G_6$) | `voicing/voicers/jazz_synth.py:L21-L22, L110` |
| **Voicing Engine** | Voice Lead Weight (Pop Piano) | $w_{\text{vl}}$ | $1.0$ | `voicing/voicers/pop_piano.py:L133` |
| **Voicing Engine** | Voice Lead Weight (Jazz Piano)| $w_{\text{vl}}$ | $1.6$ | `voicing/voicers/jazz_piano.py:L196` |
| **Voicing Engine** | Hungarian Unmatched Voice Pen.| $P_{\text{unmatched}}$ | $1.2 - 2.5$ | `voicing/voicers/*.py`, `unmatched_penalty` |
| **Voicing Engine** | PLR Transformation Bonus | $B_{\text{plr}}$ | $0.4 - 3.0$ | `voicing/voicers/*.py`, `plr_bonus` |
| **Bass Module** | Bass Pitch Tessitura | $[P_{\text{min}}, P_{\text{max}}]$ | $[28, 55]$ ($E_1 - G_3$) | `bass_module.py:L25-L32`, Spec 18 §3.1 |
| **Bass Module** | Bass Octave Jump Probability | $p_{\text{jump}}$ | $0.20$ ($20.0\%$) | `bass_module.py:L30` |
| **Bass Module** | Electric Bass Finger GM Weight| $W_{\text{GM33}}$ | $35.0$ | `instruments.py:L380`, Spec 18 §3.3 |
| **Bass Module** | Electric Bass Pick GM Weight | $W_{\text{GM34}}$ | $25.0$ | `instruments.py:L381`, Spec 18 §3.3 |
| **Bass Module** | Acoustic Upright Bass GM Weight| $W_{\text{GM32}}$ | $20.0$ | `instruments.py:L382`, Spec 18 §3.3 |
| **Bass Module** | Fretless Bass GM Weight | $W_{\text{GM35}}$ | $15.0$ | `instruments.py:L383`, Spec 18 §3.3 |
| **Percussion** | Percussion Omission Prob. | $p_{\text{omit\_perc}}$ | $0.30$ ($30.0\%$) | `percussion_module.py:L12` |
| **Percussion** | Drum Velocity Jitter Sigma | $\sigma_{\text{drum}}$ | $8.0$ (hi-hat: $12.0$) | `percussion_module.py:L136, L142` |
| **Melody Module** | Pentatonic Filter Intervals (5)| $I_5$ | $\{0, 5, 7, 10\}$ | `melody_module.py:L828`, Spec 19 §4.2 |
| **Melody Module** | 3rd Masking (sus4, sus2, 1) | $I_{\text{forbid}}$ | $\{3, 4\}$ (m3, M3 forbidden) | `melody_module.py:L832`, Spec 19 §4.2 |
| **Melody Module** | Vocal Formant Target Rate | $r_{\text{vocal}}$ | $\ge 25.0\%$ (Pop/Rock) | `CORPUS_GEN_REFERENCE.md:L73`, Spec 17 §3 |
| **MIDI Humanizer** | Target Timing Jitter | $\sigma_t$ | $15.0\text{ ms}$ | `HumanizedMidiRenderer.java:L62` |
| **MIDI Humanizer** | Velocity Jitter Sigma | $\sigma_v$ | $8.0$ (drums: $4.0$) | `HumanizedMidiRenderer.java:L63, L431` |
| **MIDI Humanizer** | Pocket Bias: Bass | $\Delta t_{\text{V1}}$ | $+8.0\text{ ms}$ | `HumanizedMidiRenderer.java:L68` |
| **MIDI Humanizer** | Pocket Bias: Melody | $\Delta t_{\text{V2}}$ | $-4.0\text{ ms}$ | `HumanizedMidiRenderer.java:L69` |
| **MIDI Humanizer** | Pocket Bias: Arpeggio | $\Delta t_{\text{V3}}$ | $-6.0\text{ ms}$ | `HumanizedMidiRenderer.java:L70` |
| **MIDI Humanizer** | Pocket Bias: Drums | $\Delta t_{\text{V9}}$ | $+2.0\text{ ms}$ | `HumanizedMidiRenderer.java:L71` |
| **MIDI Humanizer** | Guitar Strum Stagger Time | $\Delta t_{\text{strum}}$| $7.0\text{ ms}$ per string | `HumanizedMidiRenderer.java:L317` |
| **Audio Rendering** | Synthesis Sample Rate | $f_s$ | $22{,}050\text{ Hz}$ | `sound_design.py:L23`, `REFERENCE_RENDER_PROFILE` |
| **Audio Rendering** | Target Program Loudness | $\text{LUFS}$ | $-18.0\text{ LUFS}$ | `sound_design.py:L28`, `REFERENCE_RENDER_PROFILE` |
| **Audio Rendering** | True Peak Limiting Ceiling | $\text{dBTP}$ | $-1.0\text{ dBTP}$ | `sound_design.py:L29`, `REFERENCE_RENDER_PROFILE` |
| **Audio Rendering** | V0 Chords Reference Gain | $G_{\text{V0}}$ | $0.0\text{ dB}$ | `sound_design.py:L30`, Spec 18 §2.3 |
| **Audio Rendering** | V1 Bass Reference Gain | $G_{\text{V1}}$ | $+1.0\text{ dB}$ (reduced from $+3.0$) | `sound_design.py:L30`, Spec 18 §2.3 |
| **Audio Rendering** | V2 Melody Reference Gain | $G_{\text{V2}}$ | $-1.0\text{ dB}$ | `sound_design.py:L30`, Spec 18 §2.3 |
| **Audio Rendering** | V9 Percussion Reference Gain | $G_{\text{V9}}$ | $-1.5\text{ dB}$ (raised from $-6.0$) | `sound_design.py:L30`, Spec 18 §2.3 |
| **Audio Rendering** | Role Gain Randomization Jitter| $\Delta G$ | $\pm 3.0\text{ dB}$ (uniform) | `sound_design.py:L197`, `--gain-jitter-db` |
| **Audio Rendering** | Micro-Tuning Detune Drift | $\Delta f_{\text{cents}}$| $U(-25.0, +25.0)\text{ cents}$ | `sound_design.py:L217`, `--detune-cents` |
| **Audio Rendering** | Reverb Low-End Anchor Highpass| $f_{\text{HP}}$ | $150\text{ Hz}$ | `audio_render.py:L293`, Spec 18 §2.1 |
| **Audio Rendering** | Reverb Dry / Wet Mix Weights | $W_{\text{dry}} / W_{\text{wet}}$ | $0.82\text{ dry} \ / \ 0.18\text{ wet}$ | `audio_render.py:L295` |
| **Audio Rendering** | Spectral EQ Tilt Jitter Bounds | $\Delta G_{\text{tilt}}$| $U(1.5, 3.0)\text{ dB}$ at $1.5\text{ kHz}$ | `sound_design.py:L231-L240`, Spec 17 §4.3 |
| **Audio Rendering** | Bus Compressor Attack / Decay | $t_{\text{att}} / t_{\text{rel}}$ | $20\text{ ms} \ / \ 150\text{ ms}$ | `audio_render.py:L320`, `compand` |

---

## 8. Explicit Identification of Active vs. Dead Code

The repository contains historical predecessors and calibration tools alongside active production code:

1. **Active Generator Entrypoint vs. Deprecated Predecessors:**
   - **Active Entrypoint:** `target_corpus_gen.py` (inheriting from `chord_gen.py:ChordGenerator`). This is the sole entrypoint invoked by `CORPUS_GEN_REFERENCE.md` to produce the 5,000-song dataset with quota-aware rare triad and extension oversampling.
   - **Dead / Deprecated Code:** `old_src/old_generator.py` and `old_src/generator_multitrack.py`. These scripts implement a legacy single-pass Markov bigram generator without trie backoff, without sub-measure slicing, and without voicer policies. They are preserved strictly for historical provenance.
2. **Active Multi-SoundFont Renderer vs. Single SoundFont Legacy:**
   - **Active Mixing Pipeline:** `tools/project_condition_corpus.py` and `audio_render.py:render_audio_bundle` / `mix_audio_bundle_from_stems`. These scripts support multi-SoundFont rotation, FFmpeg DSP filtergraphs (`POWER_CHORD_DISTORTION_FILTER`, `v1_filter`), synthetic RIR convolution, and EQ tilt.
   - **Legacy Pipeline:** Single-file monolithic `render_midi_to_wav` in `render.py:L1040-L1080` (used only when `--stage all` is invoked without condition projection).
3. **Active Voicing Engine vs. Standalone Mock Implementations:**
   - **Active Voicing Engine:** `voicing/engine.py:Engine` initialized with the six policies in `voicing/voicers/`.
   - **Test Fixtures:** Minimal mock policies in `tests/test_engine_unit.py` designed solely for PyTest unit testing.
