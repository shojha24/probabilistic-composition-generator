# ACR Corpus Generation and Training Reference

Assuming distributions have already been calculated:

## Prerequisites & SoundFont Setup

```bash
# Audio rendering prerequisites (Ubuntu 24.04 / WSL2):
# Install FluidSynth, ffmpeg, Fluid SoundFont, and MuseScore General Full SoundFont:
sudo apt update
sudo apt install -y fluidsynth ffmpeg fluid-soundfont-gm musescore-general-soundfont-lossless

# Setup multi-SoundFont ensemble directory (Spec 16 §3.1 & Spec 17 §3.3):
# Populates ./soundfonts with distinct General MIDI SoundFonts:
#   1. FluidR3_GM.sf2 (Full-frequency multi-sample GM default)
#   2. MuseScore_General_Full.sf2 (High-fidelity multi-velocity orchestral/acoustic/vocal sample library)
#   3. GeneralUser_GS.sf2 (Balanced GM/GS bank with warm Rhodes/pianos, guitars, and vocal pads)
#   4. TimGM6mb.sf2 (Lightweight 90s digital rompler SoundFont)
mkdir -p soundfonts
ln -sf /usr/share/sounds/sf2/FluidR3_GM.sf2 soundfonts/FluidR3_GM.sf2
ln -sf /usr/share/sounds/sf2/MuseScore_General_Full.sf2 soundfonts/MuseScore_General_Full.sf2
curl -L -o soundfonts/GeneralUser_GS.sf2 "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2"
curl -L -o soundfonts/TimGM6mb.sf2 "https://raw.githubusercontent.com/arbruijn/TimGM6mb/master/TimGM6mb.sf2"

# Verify tools and discovered SoundFonts:
fluidsynth --version
ffmpeg -version
python3 -c 'from sound_design import resolve_soundfonts; sfs = resolve_soundfonts(soundfont_dir="soundfonts"); print(f"Found {len(sfs)} soundfonts:", [sf.name for sf in sfs])'
```

---

## 5,000 Song Production Pipeline (Spec 18)

### Why 5,000 Songs vs. 10,000 Songs (Spec 18 §5.1)
Empirical analysis and sim-to-real transfer benchmarks confirm that **5,000 songs is the optimal corpus scale**:
- **300 Audio Hours / 60,000 Augmented Passes**: 5,000 songs with 12-pitch shift augmentation ($5,000 \times 12 = 60,000$) provides full harmonic coverage and CQT representation learning without representational rigidity on synthetic soundfont envelopes.
- **Statistical Coverage**: Exceeds >1,000 instances per rare chord class.
- **Pipeline Turnaround**: ~2 hours audio render / ~2.5 hours pretraining on modern GPUs.

### Step 1: Generate 5k Target Corpus with Sustained Inversions & Sub-Measure Slicing

- **5,000 songs total** (~500,000 chord events; 3,250 pop/rock, 1,750 jazz; 65/35 split).
- **`--bimodal-natural-ratio 0.5`**: 50% natural distribution (safeguarding real-world frame accuracy and common-chord priors), 50% targeted rare distribution. (Note: pure targeted generation with `--bimodal-natural-ratio 0.0` is also supported for stress-testing rare chord quotas without deficit).
- **`--triad-target-scale 3.0`**: Scales rare triad quotas (`sus2`, `sus4`, `diminished`, `augmented`) by 3.0x within the targeted cohort.
- **`--sustained-inversion-rate 0.10`**: Generates sustained first (`/3`) and second (`/5`) inversion chords across half-note (`h`) and whole-note (`w`) durations (Spec 18 §3.2).
- **`--slice-inversions`** (enabled by default): Splits walking basslines, root-5th alternations, and approach notes into sub-measure time-aligned Harte labels (e.g. `0.000 2.000 C:maj`, `2.000 4.000 C:maj/5`, `C:maj/3`, `C:maj/7`). Guarantees that synthetic non-root bass rates reach **12%–15%**, matching physical acoustic reality.

```bash
python3 target_corpus_gen.py \
  --target-events 500000 \
  --total-songs 5000 \
  --pop-rock-ratio 0.65 \
  --bimodal-natural-ratio 0.5 \
  --triad-target-scale 3.0 \
  --sustained-inversion-rate 0.10 \
  --slice-inversions \
  --debug \
  --seed 15001 \
  --dist-dir ./distributions \
  --out-dir ./gen/acr-target-5k
```

### Step 2: Canonical Multi-Role Rendering

- Renders V0 chords/arpeggios, V1 bass, V2 melody, V9 percussion.
- 30% arpeggios / 70% pads; melody and percussion included in canonical master.
- **Extended Bass Tessitura & Patch Prioritization (Spec 18 §3)**:
  - Explores E1 (MIDI 28) to G3 (MIDI 55) with 20% octave jitter to high walking/slap registers.
  - Prioritizes GM 33 (*electric-bass-finger*), GM 34 (*electric-bass-pick*), GM 32 (*acoustic-bass*), and GM 35 (*fretless-bass*), while deprecating plain synth/slap rompler patches.
- **Percussion Velocity Humanization (Spec 18 §4)**:
  - Drum hits incorporate Gaussian velocity jitter: kick and snare ($100 \pm 8$), closed/pedal hi-hat ($80 \pm 12$).
- **Human Vocal Formants (Spec 17 §3)**: Melody engine catalog integrates GM programs 52 (*Choir Aahs*), 53 (*Voice Oohs*), and 54 (*Synth Voice*), prioritized for pop/rock verses, choruses, and ballads ($\ge 25\%$ vocal lead selection rate).
- Melody generation automatically applies legato phrase-level breathing, voice-leading memory, and stepwise motion.
- Only the `score` stage is needed here; condition projection handles MIDI & audio.

```bash
python3 render.py \
  --in-dir ./gen/acr-target-5k/pop-rock-labels \
  --in-dir ./gen/acr-target-5k/jazz-labels \
  --out ./gen/acr-canonical-5k-all-roles.txt \
  --seed 25001 \
  --mode mixed \
  --arpeggio-percent 30 \
  --pad-percent 70 \
  --percussion-percent 100 \
  --melody-condition naturalistic \
  --melody-percent 100 \
  --melody-profile mixed \
  --melody-decoder sequence_beam \
  --melody-phrase-bars 4 \
  --melody-breath-beats 2.0 \
  --melody-stepwise-bonus 2.00 \
  --melody-repeat-penalty -0.60 \
  --stage score
```

### Step 3: Project Conditions & Multi-SoundFont Audio Render with Low-End DSP & Production FX
/
- Reuses canonical V0/V1/V2/V9 blocks without symbolic regeneration.
- **Multi-SoundFont Rotation**: Deterministically rotates across installed SoundFonts per song ordinal.
- **Low-End Mix Engineering & DSP (Spec 18 §2)**:
  - **Split-Band Dry Low-End Reverb Anchor**: Wet convolution path applies a high-pass filter at 150 Hz (`highpass=f=150`) before RIR convolution, keeping sub-150 Hz bass fundamentals completely dry, punchy, and phase-coherent.
  - **Bass Amp Drive & Cabinet Saturation**: FFmpeg filter on `V1` applies soft cubic saturation (`aeval='val(0)-0.05*val(0)^3'`), 120 Hz low-shelf warmth (`lowshelf=f=120:g=2.0`), and 3500 Hz low-pass filter (`lowpass=f=3500`) to remove harsh GM click noise.
  - **Stem Level Rebalancing**: `V0: 0.0 dB`, `V1: 1.0 dB` (down from +3.0 dB), `V2: -1.0 dB`, `V9: -1.5 dB` (up from -6.0 dB for realistic kick-bass masking).
  - **Stereo Spatialization**: `V1` and `V2` anchored dead-center; `V0` and `V9` receive subtle $\pm 20\%$ stereo panning in stereo profiles.
- **Studio Production FX (`--production-fx`, default True)**:
  - **Dynamic Bus Compression (`compand`)**: Moderate attack (20ms) and decay (150ms) for consistent RMS density.
  - **Analog Tape Saturation (`aeval`)**: Soft polynomial overdrive saturation (`val(0)-0.1*val(0)^3|val(1)-0.1*val(1)^3`) emulating vintage console tape harmonics.
  - **Spectral EQ Tilt Jitter**: Randomized high/low-shelf tilt ($\pm 1.5$ to $3.0\text{ dB}$ around 1.5 kHz) preventing Conformer over-indexing.
  - **True Peak Limiting & Loudness Normalization**: Mastered to $-18\text{ LUFS}$ with $-1.0\text{ dBTP}$ ceiling.
- **Guitar strum stagger**: 5–10 ms string stagger with down/up stroke alternation.
- **Piano damper pedal**: CC64 sustain pedal events across chord transitions.

```bash
SOUNDFONT_DIR="./soundfonts"
FALLBACK_SOUNDFONT="./soundfonts/FluidR3_GM.sf2"

python3 tools/project_condition_corpus.py \
  --canonical ./gen/acr-canonical-5k-all-roles.txt \
  --out-dir ./gen/acr-conditions-5k \
  --conditions naturalistic \
  --seed 25001 \
  --melody-percent 70 \
  --percussion-percent 70 \
  --stage audio \
  --soundfont-dir "${SOUNDFONT_DIR}" \
  --soundfont "${FALLBACK_SOUNDFONT}" \
  --gain-jitter-db 3.0 \
  --reverb \
  --detune-cents 25.0 \
  --production-fx
```

### Step 4: Extract CQT Features & Pretrain ChordFormer

In `ChordFormer-Artificial-Dataset-Benchmarking`:

```bash
# Convert sliced labels to Harte format
python convert_synth_labels.py --input_dir ../probabilistic-composition-generator/gen/acr-target-5k

# Extract CQT spectrograms and labels
python extract_synth_cqt.py
python extract_synth_xchord.py

# Pretrain Stage 1 (7-8 epochs, ~11,000 steps at batch_size=36)
# LR schedule: 1e-3 (4 epochs) -> 1e-4 (2 epochs) -> 1e-5 (1-2 epochs)
python pretrain_synthetic.py --total_songs 5000 --epochs 8

# Fine-Tune Stage 2 with Fast CRF and Replay Floor
python finetune_real_fold.py 0 --replay_floor 0.15 --no_logit_adj
```

---

## Output Architecture

Condition outputs are written under:
```
gen/acr-conditions-5k/<condition>/scores.txt
gen/acr-conditions-5k/<condition>/scores.txt.manifest.json
gen/acr-conditions-5k/<condition>/midi/START_SONG_<ordinal>.mid
gen/acr-conditions-5k/<condition>/audio/song_<ordinal>/mix.flac
```

Shared canonical audio stems are rendered once under:
```
gen/acr-conditions-5k/_stems/audio/song_<ordinal>/<track>.flac
```

Condition audio mixes are generated by mixing the active role stems via FFmpeg:
```
[V1 stem]  --> [amp_sat + lowshelf + lowpass] --------+
[V0 stem]  --> [panning +-20%] -----------------------+--> amix --> [split dry] -----------------------------------------+--> eq_tilt --> tape_sat --> compand --> loudnorm
[V2 stem]  --> [center] ------------------------------+             --> [highpass=150] --> afir(RIR) --> amix(dry+wet) -+
[V9 stem]  --> [panning -+20%] -----------------------+
```
The condition manifests retain the full provenance: audio checksums, SoundFont checksums, renderer versions, selected role mask, mix profile, EQ tilt applied, and production FX metadata.
