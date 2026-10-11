Assuming distributions have already been calculated:

# Audio rendering prerequisites (Ubuntu 24.04 / WSL2):
#   Install FluidSynth, ffmpeg, Fluid SoundFont, and MuseScore General Full SoundFont:
sudo apt update
sudo apt install -y fluidsynth ffmpeg fluid-soundfont-gm musescore-general-soundfont-lossless

# Setup multi-SoundFont ensemble directory (Spec 16 §3.1):
#   Populates ./soundfonts with distinct General MIDI SoundFonts:
#     1. FluidR3_GM.sf2 (Full-frequency multi-sample GM default)
#     2. MuseScore_General_Full.sf2 (High-fidelity multi-velocity orchestral/acoustic sample library)
#     3. GeneralUser_GS.sf2 (Balanced GM/GS bank with warm Rhodes/pianos and guitars)
#     4. Roland_SC-88.sf2 (Classic Sound Canvas SC-88 rompler sound)
#     5. TimGM6mb.sf2 (Lightweight 90s digital rompler SoundFont)
mkdir -p soundfonts
ln -sf /usr/share/sounds/sf2/FluidR3_GM.sf2 soundfonts/FluidR3_GM.sf2
ln -sf /usr/share/sounds/sf2/MuseScore_General_Full.sf2 soundfonts/MuseScore_General_Full.sf2
curl -L -o soundfonts/GeneralUser_GS.sf2 "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2"
curl -L -o soundfonts/TimGM6mb.sf2 "https://raw.githubusercontent.com/arbruijn/TimGM6mb/master/TimGM6mb.sf2"

# Verify tools and discovered SoundFonts:
fluidsynth --version
ffmpeg -version
python3 -c 'from sound_design import resolve_soundfonts; sfs = resolve_soundfonts(soundfont_dir="soundfonts"); print(f"Found {len(sfs)} soundfonts:", [sf.name for sf in sfs])'

# 1. Generate the quota-aware bimodal target corpus:
#    5,000 songs total (3,500 pop/rock, 1,500 jazz; 70/30 split) and 500,000 chord events total.
#    --bimodal-natural-ratio 0.6: 60% of songs follow natural empirical transition distributions
#    (safeguarding real-world frame accuracy and common-chord priors), while 40% aggressively
#    target rare chords and extensions.
#    --triad-target-scale 2.5: scales rare triad quotas (sus2, sus4, diminished, augmented) by 2.5x
#    within the targeted cohort.
#    Output directories will be:
#      ./gen/acr-target-500k/jazz-labels
#      ./gen/acr-target-500k/pop-rock-labels
python3 target_corpus_gen.py \
  --target-events 500000 \
  --total-songs 5000 \
  --pop-rock-ratio 0.7 \
  --bimodal-natural-ratio 0.0 \
  --triad-target-scale 2.5 \
  --debug \
  --seed 15001 \
  --dist-dir ./distributions \
  --out-dir ./gen/acr-target-500k

# 2. Generate the canonical all-role corpus:
#    V0 chords/arpeggios, V1 bass, V2 melody, V9 percussion.
#    30% arpeggios / 70% pads; melody and percussion are always included in canonical master.
#    Melody generation (Spec 16) automatically applies legato phrase-level breathing,
#    voice-leading memory across rests, and rebalanced chord extension / suspension engagement.
#    Only the score stage is needed here; condition projection handles MIDI & audio.
python3 render.py \
  --in-dir ./gen/acr-target-500k/pop-rock-labels \
  --in-dir ./gen/acr-target-500k/jazz-labels \
  --out ./gen/acr-canonical-all-roles.txt \
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

# 3. Project all five condition views from the canonical role files. Note that
#    when I run this, I usually only run with conditions naturalistic.
#    This does not call the symbolic generator again. It reuses the canonical
#    V0/V1/V2/V9 blocks, voicing decisions, timing, and provenance.
#    Generates Scores, MIDI, Audio, and Manifests in one unified pass.
#
#    Audio rendering options (Spec 16):
#      --soundfont-dir: directory containing multiple SoundFonts for deterministic ensemble rotation
#      (or fallback to a single --soundfont /usr/share/sounds/sf2/FluidR3_GM.sf2).
#      --gain-jitter-db: dynamic stem gain variation (default 3.0 dB).
#      --reverb / --no-reverb: synthetic impulse response convolution room reverb (default True).
#      --detune-cents: subtle micro-tuning pitch drift (default 25.0 cents).
#      Behind the scenes: applies guitar strum stagger (7 ms per string alternating down/up),
#      piano damper pedal CC64 events across chord boundaries, and walking basslines with chromatic approaches.
SOUNDFONT_DIR="./soundfonts"
FALLBACK_SOUNDFONT="./soundfonts/FluidR3_GM.sf2"

python3 tools/project_condition_corpus.py \
  --canonical ./gen/acr-canonical-all-roles.txt \
  --out-dir ./gen/acr-conditions \
  --conditions naturalistic \
  --seed 25001 \
  --melody-percent 70 \
  --percussion-percent 70 \
  --stage audio \
  --soundfont-dir "${SOUNDFONT_DIR}" \
  --soundfont "${FALLBACK_SOUNDFONT}" \
  --gain-jitter-db 3.0 \
  --reverb \
  --detune-cents 25.0

# Condition outputs are written under:
#   ./gen/acr-conditions/<condition>/scores.txt
#   ./gen/acr-conditions/<condition>/scores.txt.manifest.json
#   ./gen/acr-conditions/<condition>/midi/START_SONG_<ordinal>.mid
#   ./gen/acr-conditions/<condition>/audio/song_<ordinal>/mix.flac
#
# Shared canonical audio stems are rendered once under:
#   ./gen/acr-conditions/_stems/audio/song_<ordinal>/<track>.flac
# (or reused from canonical audio if already generated, or custom via --stems-dir).
# Condition audio mixes are generated by mixing the active role stems via ffmpeg
# with role-specific gains, room reverb, and EBU R128 loudness normalization.
#
# The condition manifests retain the MIDI and audio checksums, SoundFont
# checksums, renderer versions, selected role mask, and mix profile.

All projections reuse the canonical source cohort and render seed. The
canonical render pays the symbolic-generation cost once; projection is a
role-mask operation, and MIDI conversion is an independently stoppable
follow-up stage. In stage audio, role stems are synthesized once and
mixed to create condition-specific audio views without redundant resynthesis.
Canonical all-role rendering with `render.py --stage audio` also retains
independent role stems for diagnostic ablations.

