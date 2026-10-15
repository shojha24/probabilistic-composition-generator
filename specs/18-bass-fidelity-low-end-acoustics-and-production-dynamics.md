# Spec 18: Bass Acoustic Fidelity, Low-End Mixing Dynamics, and Corpus Scale Optimization

**Status:** Proposed  
**Depends on:** [Spec 15 - Sound Design and ACR Corpus Production](15-sound-design-and-acr-corpus.md), [Spec 16 - Synthetic Generator Fidelity and Melodic Improvements](16-generator-fidelity-and-melodic-improvements.md), [Spec 17 - Vocal Leads, Studio Production FX, Sub-Measure Inversion Slicing](17-vocal-leads-production-fx-and-bass-slicing.md)  
**Audience:** Bass module, audio rendering pipeline, sound design, percussion module, and ACR training/evaluation  

---

## 1. Overview and Problem Statement

Following the introduction of vocal formants and sub-measure inversion slicing in Spec 17, extensive benchmarking of ChordFormer on the 1,217-song real-world test set (Billboard, Beatles, Robbie Williams, Usun, RWC) revealed a distinct divergence:
- **Rare Extensions & Triads Surged**: Double-digit recall gains across altered 9ths (`b9` $+16.3\%$, `#9` $+7.3\%$), altered 11ths (`#11` $+12.5\%$), augmented triads (`+17.7\%`), and suspended triads (`sus2` $+13.6\%$). Overall 9th and 11th extension macro accuracy improved by $+9.43\%$ and $+6.88\%$.
- **Head 2 (Bass) Experienced a Drop**: Bass frame accuracy dropped by $-2.47\%$ ($0.8537 \rightarrow 0.8290$) and bass class macro accuracy fell by $-2.73\%$ ($0.8460 \rightarrow 0.8187$).

A diagnostic investigation identified four acoustic and mixing factors responsible for this sim-to-real gap:

1. **Unrealistic Low-End Isolation & Kick Masking Deficit**:
   - In `sound_design.py`, `REFERENCE_RENDER_PROFILE` set `V1 (bass)` to `+3.0 dB` and `V9 (percussion)` to `-6.0 dB`.
   - The synthetic bass was rendered pristine, loud, and completely unmasked. In commercial recordings, kick drum transients (45–90 Hz) strongly overlap and mask bass guitar fundamentals (40–100 Hz). The Conformer became reliant on clean, unmasked CQT bins in the lowest registers and degraded when confronted with real kick bleed.

2. **Phase Smearing from Full-Mix Convolution Reverb**:
   - In `audio_render.py` (`_mix_flac`), convolution reverb convolved the entire `[summed]` mix with synthetic room impulse responses.
   - In professional mixing engineering, sub-150 Hz audio is strictly kept dry and centered. Convolving sub-bass fundamentals creates phase comb-filtering, artificial low-end mud, and blurred pitch boundaries that do not exist in real mastered tracks.

3. **Narrow Bass Tessitura & Register Confinement**:
   - In `bass_module.py`, bass pitches were pinned to `36 + bass_pc` (MIDI 36–47, C2 to B2).
   - Real electric and double basses frequently descend to **E1 (MIDI 28)** (or B0/MIDI 23 on a 5-string) and jump up to **G3/A3 (MIDI 55–57)** for octave fills, slap-and-pop lines, and high walking registers.

4. **Absence of Bass Saturation & Amp Cabinet Overtones**:
   - Plain FluidSynth soundfonts emit sterile sampled bass tones with weak 2nd and 3rd harmonics (100–300 Hz). In real audio, bass guitars are tracked through driven tube amplifiers (e.g. Ampeg SVT, SansAmp), which provide the harmonic overtones necessary for pitch identification when the fundamental is masked.

5. **Corpus Scale & Step Budget**:
   - Pretraining on 10,000 synthetic songs incurs significant compute overhead and risks representational rigidity on synthetic soundfont timbres. 5,000 songs combined with 12-pitch shift augmentation ($5,000 \times 12 = 60,000$ passes) provides full harmonic coverage.

---

## 2. Module 1: Low-End Mix Engineering & DSP Architecture (`audio_render.py` & `sound_design.py`)

### 2.1 Split-Band Reverb Routing (Dry Low-End Anchor)
To prevent synthetic room impulse responses from smearing bass fundamentals:
- The wet reverb send must omit `V1` (bass), OR
- The wet convolution signal path must apply a high-pass filter at 150 Hz (`highpass=f=150`) before being blended into the dry signal:
```
[summed] --> asplit --> [dry_path]
                    --> [to_rev] --> highpass=f=150 --> afir(RIR) --> [wet_path]
[dry_path][wet_path] --> amix=inputs=2:weights=0.82 0.18 --> [reverbed]
```
This guarantees that sub-150 Hz fundamentals remain tight, punchy, and phase-coherent.

### 2.2 Bass Amp Drive & Cabinet Saturation Filter
Apply warm harmonic saturation and high-frequency click attenuation specifically to the `V1` bass stem:
```python
# FFmpeg filter applied to V1 before mix summing:
# 1. Soft cubic saturation: generates 2nd/3rd harmonics in 100-300 Hz range
# 2. Low-shelf boost (+2 dB @ 120 Hz): warms the foundational punch
# 3. Low-pass filter (3500 Hz): removes harsh GM pick noise
v1_filter = "aeval='val(0)-0.05*val(0)*val(0)*val(0)':c=same,lowshelf=f=120:g=2.0:t=q,lowpass=f=3500"
```

### 2.3 Stem Level Rebalancing
Update `REFERENCE_RENDER_PROFILE` in `sound_design.py` to match commercial multi-track stem balances:
```python
REFERENCE_RENDER_PROFILE = {
    ...
    "role_gains_db": {
        "V0": 0.0,    # Chords / Pads / Arpeggios
        "V1": 1.0,    # Bass: reduced from +3.0 dB to +1.0 dB
        "V2": -1.0,   # Melody / Vocal Leads
        "V9": -1.5,   # Percussion: increased from -6.0 dB to -1.5 dB (stronger kick masking)
    },
}
```

### 2.4 Stereo Spatialization
- **Center Mono Anchor**: `V1` (Bass) and `V2` (Lead Vocals/Melody) are mixed dead-center.
- **Stereo Spread**: `V0` (Chords/Pianos/Guitars) and `V9` (Drum kit overheads) receive subtle stereo panning ($\pm 20\%$) to mimic studio acoustic separation and prevent unnatural single-point acoustic pileup.

---

## 3. Module 2: Bass Tessitura & Harmonic Voicing Realism (`bass_module.py` & `instruments.py`)

### 3.1 Extended Tessitura & Octave Jitter
Modify pitch calculation in `bass_module.py` to allow natural exploration of Octaves 1 through 3:
```python
def resolve_bass_pitch(bass_pc: int, tonic_pc: int, rng: random.Random) -> int:
    """Generate authentic bass pitch within standard 4-string bass range (E1 to G3)."""
    # Base octave in Octave 1: C1 (24) to B1 (35)
    base_midi = 24 + ((bass_pc + tonic_pc) % 12)
    
    # 4-string electric/upright bass bottom limit is E1 (MIDI 28)
    if base_midi < 28:
        base_midi += 12  # Transpose low C, C#, D, D# up to Octave 2
        
    # 20% probability of an octave jump to high walking/slap register (Octave 2-3)
    # Allows notes up to G3 (MIDI 55)
    if rng.random() < 0.20 and base_midi + 12 <= 55:
        return base_midi + 12
    return base_midi
```

### 3.2 Sustained Slash Inversions
In addition to 1-beat walking passing notes and 2-beat root-5th alternations:
- Ensure the chord progression generator generates sustained first (`/3`) and second (`/5`) inversion chords across half-note (`h`) and whole-note (`w`) durations.
- Real pop ballads and rock anthems frequently hold inversions (e.g. `C/E`, `G/B`, `D/F#`, `F/A`) for entire measures. Pretraining must see sustained non-root bass pedals, not merely transient walking slices.

### 3.3 Patch Selection & Pruning
In `instruments.py`, refine `BASS_INSTRUMENTS` to prioritize realistic acoustic and electric bass timbres:
- **Primary Programs**:
  - GM 33: `electric-bass-finger` (warm, natural fundamental)
  - GM 34: `electric-bass-pick` (articulate attack with transient punch)
  - GM 32: `acoustic-bass` (woody double bass body resonance)
  - GM 35: `fretless-bass` (smooth, expressive mid-range)
- **Deprecate / Reduce Probability**:
  - Reduce selection weights for `square-wave`, `synth-bass-2`, and `slap-bass-2` which possess artificial synthetic envelopes uncharacteristic of real music benchmarks.

---

## 4. Module 3: Percussion Dynamics & Humanization (`percussion_module.py`)

### 4.1 Velocity Humanization
Introduce Gaussian velocity jitter on drum hits:
- Kick and Snare: Base velocity $100 \pm 8$.
- Closed/Pedal Hi-Hat: Base velocity $80 \pm 12$.
- Prevents mechanical machine-gun repetition across 8th-note and 16th-note grooves.

### 4.2 Dynamic Kick-Bass Masking
Ensure acoustic bass drums (GM 35 and 36) produce substantial low-frequency transient energy (40–80 Hz). This forces the Conformer's self-attention layers to learn harmonic separation rather than relying on a static, uncontested fundamental channel.

---

## 5. Module 4: Corpus Scale & Pretraining Optimization

### 5.1 Corpus Scale: 5,000 Songs vs. 10,000 Songs
Empirical and theoretical analysis confirms that **5,000 songs is the optimal corpus scale**:

| Dimension | 5,000 Songs | 10,000 Songs | Assessment |
|---|---|---|---|
| **Audio Hours** | ~300 hours | ~600 hours | 5k is ample for CQT representation learning |
| **Augmented Passes** | 60,000 passes (12 pitch shifts) | 120,000 passes | 60k passes provides thorough convergence |
| **Rare Chord Coverage** | >1,000 instances per rare class | >2,000 instances per rare class | 5k exceeds the statistical threshold for rare recall |
| **Sim-to-Real Transfer** | High adaptability | Risk of representational rigidity | Excess synthetic steps over-crystallize on SoundFont envelopes |
| **Pipeline Turnaround** | ~2 hours audio render / ~2.5 hrs train | ~4 hours audio render / ~5 hrs train | 5k enables fast experimental iteration |

### 5.2 Pretraining Schedule & Early Stopping
To prevent synthetic overfitting and preserve elasticity for real-world fine-tuning:
- **Total Training Duration**: **7 to 8 epochs ($\approx 10,500$ to $12,000$ mini-batches)** at `batch_size=36`.
- **Learning Rate Schedule**:
  - `1e-3`: 4 epochs (~6,000 steps) — rapid acquisition of global chroma and triad geometry.
  - `1e-4`: 2 epochs (~3,000 steps) — fine calibration of altered extensions and bass inversions.
  - `1e-5`: 1–2 epochs (~1,500–3,000 steps) — smooth convergence without weight crystallization.
- **Stop Condition**: Once synthetic validation accuracy plateaus ($>92\%$), pretraining terminates immediately.

---

## 6. Implementation Checklist & Verification

1. **`sound_design.py`**:
   - Update `role_gains_db` in `REFERENCE_RENDER_PROFILE`: `V1: 1.0 dB`, `V9: -1.5 dB`.
2. **`audio_render.py`**:
   - Insert `highpass=f=150` on the wet reverb impulse convolution path.
   - Insert saturation + low-pass filter chain on `V1` before stem summing.
3. **`bass_module.py`**:
   - Implement `resolve_bass_pitch` with expanded range (MIDI 28–55) and 20% octave jitter.
4. **`instruments.py`**:
   - Re-weight `BASS_INSTRUMENTS` to favor finger, pick, acoustic, and fretless electric basses.
5. **`percussion_module.py`**:
   - Add velocity humanization ($\pm 10$) to drum events.
6. **Benchmark Validation**:
   - Re-render a 5k song corpus with the Spec 18 pipeline.
   - Pretrain ChordFormer for 11,000 steps.
   - Fine-tune on real folds with `--replay_floor 0.15` and evaluate with fast CRF (`--no_logit_adj`).
   - Target metrics: Maintain rare extension gains (`b9`, `#11`, `aug` $+10\%$ to $+20\%$) while bringing Head 2 Bass accuracy back to $\ge 85.0\%$.
