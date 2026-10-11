# Spec 16: Synthetic Generator Fidelity and Melodic Improvements

**Status:** Implemented  
**Depends on:** [Spec 14 - Melody Generation](14-melody-generation.md), [Spec 15 - Sound Design and ACR Corpus Production](15-sound-design-and-acr-corpus.md)  
**Audience:** Symbolic generator, melody module, MIDI renderer, audio rendering pipeline, and ACR evaluation

---

## 1. Overview and Problem Statement

Benchmark evaluations of the synthetic pretraining pipeline on the ChordFormer 1217 benchmark demonstrate that synthetic training examples successfully boost the long-tail recall of rare extensions and alterations (e.g., `#9`, `b9`, `#11`, `b13`, `aug`, `sus2`) and improve class-wise macro accuracy across multiple heads.

However, two critical discrepancies limit the real-world accuracy of models trained on this corpus:

1. **Acoustic Sim-to-Real Domain Gap**:
   Pretraining validation accuracy reaches **~97%**, yet real benchmark frame accuracy drops to **74%–85%**. Synthetic audio generated with a single General MIDI SoundFont (`FluidR3_GM.sf2`), identical static stem gains, and dry mixing lacks the timbral diversity, room acoustics, and tuning variations present in commercial music.

2. **Melodic Phrasing and Harmonic Detachment**:
   Empirical analysis of the generated melody stem (`V2`) in `gen/acr-canonical-all-roles_melody.txt` reveals:
   - **Staccato / Choppy Phrasing**: A rest-to-note ratio of **0.84**, with 84.6% of notes $\le 1$ beat immediately followed by short rests due to independent per-slot rest dice rolls (~50% chance of a rest per 8th/quarter slot).
   - **Erratic Contours**: Beam search resets voice-leading memory across rests, producing **30.8% large leaps ($> 5$ semitones)** and **13.3% octave+ jumps**, contrasted with **52.1% identical repeated notes** during active phrases.
   - **Harmonic Disconnection**: Over **88.7%** of melody notes are plain triad doublings (Root, 3rd, 5th) and **0%** are declared chord extensions, because beam search log-scoring heavily penalizes non-chord tones and tensions.

3. **Harmonic Prior Distortion**:
   Aggressively scaling rare triads and extensions across the entire corpus (`--triad-target-scale 2.5`) warps natural chord transition priors, causing slight regressions on common chord recall (7ths, 9ths, 13ths) and overall frame accuracy.

This specification formalizes the required improvements across the symbolic generator, melody engine, MIDI humanizer, and audio rendering pipeline to resolve these bottlenecks.

---

## 2. Melody Engine Improvements (`melody_module.py`)

### 2.1 Phrase-Level Rhythm and Breathing (Legato Phrasing)
- **Current Behavior**: `_build_rhythm_plan` independently samples a rest on every 16th-grid slot with probability `rest_probability / factor` (~50% in `lead-high-sparse`), scattering short rests between almost every note.
- **Specification**:
  - Replace per-slot independent rest selection with **structured phrase planning**.
  - Partition each song's timeline into alternating **active phrase blocks** (typically 2 to 4 bars, or 8 to 16 beats) and **breath pauses** (typically 1 to 2 beats, or 1 bar between major sections).
  - Within an active phrase block, rests are restricted to syncopations or phrase pickups; notes sustain naturally to subsequent attacks unless explicitly articulated as staccato.

### 2.2 Voice-Leading Memory Across Rests
- **Current Behavior**: In `_choose_sequence_beam`, `previous_midi` is cleared to `None` if the preceding candidate was a rest (`if prior.midi is None: break`). This completely disables the leap penalty (`leap * 0.16`), causing the beam search to jump wildly to the pitch closest to `preferred_center` when resuming after a rest.
- **Specification**:
  - Search backward through prior beam states to identify the **last sounding pitch** regardless of intermediate rests.
  - Apply an attenuated leap penalty across breath rests (e.g. `leap * 0.08`) so new phrases begin in a musically logical vocal register relative to where the previous phrase ended.

### 2.3 Stepwise Motion Bias and Consecutive Repeat Penalties
- **Current Behavior**: Moving by a 2nd incurs a leap penalty of `0.32`, while holding the same pitch only incurs `-0.25` total penalty. Notes repeat identically 52.1% of the time, while stepwise motion accounts for only 11.3%.
- **Specification**:
  - **Stepwise Reward**: Introduce a `+0.40` log-score reward for melodic intervals of 1 or 2 semitones.
  - **Progressive Repeat Penalty**: Penalize consecutive identical pitches progressively:
    - 1 repetition (2 identical notes): `-0.15`
    - 2 repetitions (3 identical notes): `-0.60`
    - $\ge 3$ repetitions: `-1.50` (hard disincentive)

### 2.4 Harmonic Rebalancing (Extensions and Non-Chord Tones)
- **Current Behavior**: `_DEFAULT_BEHAVIORS` assigns `chord_tone: 5.0` (scaled to ~9.1 on strong beats) vs `extension: 2.4`, `scale_tone: 1.2`, `passing: 0.18`, and `suspension: 0.12`. In log-space, non-chord tones face a 3–4 point deficit and are eliminated during beam pruning.
- **Specification**:
  - Rebalance `_DEFAULT_BEHAVIORS`:
    - `chord_tone`: 3.5
    - `extension`: 4.0 (elevated to actively showcase 7ths, 9ths, 11ths, and 13ths)
    - `scale_tone`: 2.0
    - `passing`: 1.5
    - `suspension`: 1.2
    - `neighbor`: 1.0
  - Reward suspension resolutions (suspension on strong beat resolving stepwise downward to a chord tone on the following weak beat).

---

## 3. Audio & Acoustic Sim2Real Pipeline (`sound_design.py`, `audio_render.py`)

### 3.1 Multi-SoundFont Ensemble
- **Current Behavior**: All audio is rendered using a single SoundFont (`FluidR3_GM.sf2`), causing models to overfit to static sample attacks, filter curves, and phase characteristics.
- **Specification**:
  - Introduce a configurable SoundFont registry supporting 4–6 verified General MIDI SoundFonts (e.g. *GeneralUser GS*, *Arachno*, *Crisis General Midi 3.01*, *Yamaha XG* SoundFont sets, and specialized soundfonts for Acoustic Piano and Clean Electric Guitar).
  - During corpus rendering, deterministically sample a SoundFont per song or per role stem using `derive_seed(root_seed, "soundfont-selection", ordinal)`.
  - Record the selected SoundFont checksum and path in the render manifest.

### 3.2 Dynamic Role Gains and Section Dropouts
- **Current Behavior**: `REFERENCE_RENDER_PROFILE` fixes static gains across all songs: `{"V0": 0.0, "V1": 3.0, "V2": -1.0, "V9": -6.0}`.
- **Specification**:
  - Jitter stem gains uniformly or normally per song:
    - Chords ($V_0$): $\mathcal{U}(-3.0, +3.0)\text{ dB}$
    - Bass ($V_1$): $\mathcal{U}(0.0, +6.0)\text{ dB}$
    - Melody ($V_2$): $\mathcal{U}(-4.0, +3.0)\text{ dB}$
    - Percussion ($V_9$): $\mathcal{U}(-10.0, -2.0)\text{ dB}$
  - **Arrangement Dropouts**: Implement section-level role masks (e.g., piano-only intro for bars 1–4, drum dropouts on breakdowns, bass tacet on verses) so models do not rely on constant 4-role spectral presence.

### 3.3 Studio Convolution Reverb (RIR)
- **Current Behavior**: Audio stems are mixed dry and passed directly to `loudnorm`. Real music exhibits acoustic reverberation that smears transients and extends harmonic decays.
- **Specification**:
  - Integrate ffmpeg convolution reverb filter (`afir`) using open-source room impulse responses (small studio booth, drum room, plate reverb, live hall).
  - Apply random dry/wet mix ratios between 5% and 25% to the composite mix or individual melodic/chord stems.

### 3.4 Micro-Tuning Drift and Acoustic Room Noise
- **Current Behavior**: MIDI notes synthesize at rigid 440.00 Hz equal temperament with zero background noise.
- **Specification**:
  - Apply global song-level pitch drift ($\pm 15$ to $\pm 35$ cents) using ffmpeg audio resampling (`asetrate` / `aresample`).
  - Mix low-level ambient studio noise (tape saturation, room tone, pink noise at -40 to -48 dB LUFS) to teach the feature extractor noise robustness.

---

## 4. MIDI Performance & Physical Realism (`HumanizedMidiRenderer.java`, `bass_module.py`)

### 4.1 Guitar Strum Stagger
- **Specification**: For guitar instruments (`family == "guitar"`), stagger note-on events across strings by 5–10 ms per string, alternating between downstrokes (low-to-high pitch order) and upstrokes (high-to-low pitch order) to simulate physical strumming.

### 4.2 Piano Damper Pedal Simulation (`CC64`)
- **Specification**: For keyboard instruments playing pads or sustained chord progressions, emit MIDI Control Change 64 (Damper Pedal) down events at chord onsets and release/catch events at chord transitions to reproduce sympathetic string resonance and natural decay overlap.

### 4.3 Bass Walking and Chromatic Approach Notes (`bass_module.py`)
- **Specification**: Expand bass pattern generators to include scalar walking lines, root-fifth alternations, and chromatic approach notes (half-step above/below the target root on beat 4), reducing model over-reliance on fundamental bass downbeats.

---

## 5. Corpus Distribution & Harmonic Prior Management (`target_corpus_gen.py`)

### 5.1 Bimodal Corpus Cohort Split (60% Natural / 40% Targeted)
- **Current Behavior**: Aggressively scaling rare triads across all songs (`--triad-target-scale 2.5`) penalizes common chord transition probabilities, degrading frame accuracy on natural music.
- **Specification**:
  - Split target corpus generation into two deterministic cohorts:
    1. **Natural Transition Cohort (60%)**: Generated directly from empirical chord transition matrices without quota-scaling. This guarantees that common cadences (I–V–vi–IV, ii–V–I) and natural class distributions are accurately modeled.
    2. **Quota-Targeted Cohort (40%)**: Focuses quotas specifically on edge cases, altered dominants (`#9`, `b9`, `#11`, `b13`), rare triads (`sus2`, `dim`, `aug`), and dense 3–4 extension voicings.

---

## 6. Capability Testing Protocols and Acceptance Criteria

Every new capability introduced by this specification must satisfy strict, quantitative acceptance gates.

### 6.1 Melodic Capability Acceptance Criteria
A test harness analyzing 500 generated songs from `acr-canonical-all-roles_melody.txt` must satisfy:

| Metric | Current Baseline | Target Acceptance Gate |
| :--- | :--- | :--- |
| **Rest-to-Note Ratio** | **0.84** (choppy) | **$\le 0.35$** (legato, connected phrases) |
| **Short Notes ($\le$ 1 beat)** | **84.6%** | **$40\% - 60\%$** (healthy mix of 8ths, quarters, halves, and ties) |
| **Stepwise Motion (1–2 semitones)** | **11.3%** | **$\ge 50.0\%$** (vocal/lead melodic continuity) |
| **Large Leaps ($>$ 5 semitones)** | **30.8%** | **$\le 15.0\%$** |
| **Octave+ Leaps ($\ge$ 12 semitones)** | **13.3%** | **$\le 3.0\%$** |
| **Consecutive Identical Notes** | **52.1%** (repetitive) | **$\le 20.0\%$** |
| **Declared Extension Engagement** | **0.0%** (triad doubling) | **$\ge 15.0\%$** (melodic 7ths, 9ths, 11ths, 13ths) |
| **Non-Chord Tones (NCTs)** | **11.3%** | **$15.0\% - 30.0\%$** (controlled passing/neighbor/suspension tones) |

### 6.2 Acoustic Sim2Real Capability Criteria
- **Multi-SoundFont Diversity**: In multi-soundfont mode, an audio validation check over 100 songs must verify that at least 3 distinct SoundFont checksums appear across the rendered audio stems.
- **Mix Gain Variance**: Measured stem LUFS across 50 songs must show a standard deviation of at least $1.5\text{ dB}$ per role, confirming active gain jitter.
- **Reverb Filter Execution**: ffmpeg mix logs must verify `afir` filter complex inclusion when reverb is enabled.
- **Micro-Tuning Verification**: FFT spectral analysis of rendered solo sine/flute stems must verify pitch peak shifts within $\pm 35$ cents of standard 440 Hz.

### 6.3 MIDI Realism Capability Criteria
- **Guitar Strum Delay**: For guitar channels, Note-On timestamps across simultaneous chords must exhibit a 5–10 ms stagger in the resulting MIDI sequence rather than collapsing to tick 0.
- **Piano Sustain Pedal**: Keyboard pads must contain matching MIDI `CC64` pedal-down ($127$) and pedal-release ($0$) control changes spanning chord boundaries.
- **Bass Independence**: Bass track pitch entropy must exceed baseline; at least 15% of measure transitions must feature scalar walking or chromatic approach notes.

### 6.4 Corpus Distribution Capability Criteria
- Manifest validation must verify that exactly 60% of songs are tagged with `cohort: "natural"` and 40% with `cohort: "targeted"`.
- Rare extension quotas (#9, b9, #11, b13, aug, dim, sus2) must still achieve 100% of their intended coverage targets in the targeted cohort.

---

## 7. Regression Testing Protocols and Invariant Safeguards

All improvements must maintain absolute backward compatibility and pass regression gates before corpus regeneration.

### 7.1 Determinism and Reproducibility Invariant
- Given identical CLI flags and `--seed <S>`, all outputs (symbolic progression JSONs, JFugue score strings, MIDI sequences, stem FLACs, and condition mixes) must remain **100% bit-identical or sample-identical** across repeated runs.
- Derivation seeds must continue using `derive_seed(root_seed, domain, ordinal)` with standard domain names.

### 7.2 Single-SoundFont Fallback Invariant
- If only a single SoundFont path is provided (e.g. `/usr/share/sounds/sf2/FluidR3_GM.sf2`), the rendering pipeline must fall back cleanly without error, maintaining exact backward compatibility with existing single-SoundFont configurations.

### 7.3 Multi-Role Format and Channel Contract Invariants
- JFugue score syntax must remain strictly compliant:
  - `V0`: Chords/Arpeggios
  - `V1`: Bass
  - `V2`: Melody
  - `V9`: Percussion (Channel 10)
- Condition projections (`cb`, `cbp`, `cbm`, `cbmp`, `naturalistic`) must retain their exact role masks and manifest checksum tracking.
- Output Harte `.lab` syntax must strictly adhere to the standard chord dictionary (`N`, triads, 7ths, 9ths, 11ths, 13ths, inversions) without malformed chords.

### 7.4 Existing Test Suite Invariant
- The full suite of unit and integration tests under `tests/` (`test_melody.py`, `test_audio_render.py`, `test_condition_projection.py`, `test_target_corpus_gen.py`, `test_rendered_corpus.py`) must continue to pass cleanly.

---

## 8. Pipeline Interface & Documentation Updates

While most improvements function automatically behind the scenes, several pipeline entry points introduce enhanced options.

### 8.1 Step 1: `target_corpus_gen.py`
- **New Option**: `--bimodal-natural-ratio <FLOAT>` (default: `0.60`).
  - Sets the proportion of songs generated using empirical prior distributions vs. quota-targeted rare chords. Setting to `0.0` reproduces the previous pure quota-targeted behavior.
- **Manifest Addition**: Song JSON headers record `"cohort": "natural"` or `"cohort": "targeted"`.

### 8.2 Step 2: `render.py`
- **Updated Melody Configuration**:
  - The default `--melody-profile lead-high-sparse` automatically incorporates the new legato phrasing, voice-leading memory across rests, and rebalanced behaviors.
  - Optional tuning flags:
    - `--melody-phrase-bars <INT>` (default: `4`)
    - `--melody-breath-beats <FLOAT>` (default: `2.0`)
    - `--melody-stepwise-bonus <FLOAT>` (default: `0.40`)
    - `--melody-repeat-penalty <FLOAT>` (default: `-0.60`)

### 8.3 Step 3: `tools/project_condition_corpus.py` & `audio_render.py`
- **SoundFont Options**:
  - Existing: `--soundfont <PATH>` (single SoundFont fallback).
  - Enhanced: `--soundfont-dir <DIR>` or `--soundfonts <PATH1> <PATH2> ...` to supply a directory or list of General MIDI SoundFonts for automatic deterministic ensemble rotation.
- **Mix Enhancements**:
  - `--gain-jitter-db <FLOAT>` (default: `3.0` dB; set to `0.0` for static gains).
  - `--reverb <none|subtle|medium|hall>` (default: `subtle`).
  - `--detune-cents <FLOAT>` (default: `25.0`; set to `0.0` for strict 440.00 Hz).
- **Manifest Contract Additions**:
  - In `scores.txt.manifest.json` and `audio_manifest.json`:
    - `soundfont_asset_manifest`: lists the active SoundFont ID and SHA256 digest for each stem.
    - `mix_profile`: records gain offsets, detune offset, and impulse response used for each track.
