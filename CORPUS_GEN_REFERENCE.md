Assuming distributions have already been calculated:

# Audio rendering prerequisites (Ubuntu 24.04 / WSL2):
#   Install FluidSynth, ffmpeg, and the FluidR3 General MIDI SoundFont:
sudo apt update
sudo apt install -y fluidsynth ffmpeg fluid-soundfont-gm

# Verify the tools and the installed SoundFont:
fluidsynth --version
ffmpeg -version
test -f /usr/share/sounds/sf2/FluidR3_GM.sf2 && echo "SoundFont found"

# 1. Generate the quota-aware target corpus:
#    5,000 songs total (2,500 per genre) and 500,000 chord events total.
#    Output directories will be:
#      ./gen/acr-target-500k/jazz-labels
#      ./gen/acr-target-500k/pop-rock-labels
python3 target_corpus_gen.py \
  --target-events 500000 \
  --target-songs 2500 \
  --debug \
  --seed 15001 \
  --dist-dir ./distributions \
  --out-dir ./gen/acr-target-500k

# 2. Generate the canonical all-role corpus:
#    V0 chords/arpeggios, V1 bass, V2 melody, V9 percussion.
#    30% arpeggios / 70% pads; melody and percussion are always included.
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
  --melody-profile lead-high-sparse \
  --melody-decoder sequence_beam \
  --stage score

# 3. Project all five condition views from the canonical role files.
#    This does not call the symbolic generator again. It reuses the canonical
#    V0/V1/V2/V9 blocks, voicing decisions, timing, and provenance.
#    Generates Scores, MIDI, Audio, and Manifests in one unified pass.
SOUNDFONT=/usr/share/sounds/sf2/FluidR3_GM.sf2
python3 tools/project_condition_corpus.py \
  --canonical ./gen/acr-canonical-all-roles.txt \
  --out-dir ./gen/acr-conditions \
  --conditions cb cbp cbm cbmp naturalistic \
  --seed 25001 \
  --melody-percent 70 \
  --percussion-percent 70 \
  --stage audio \
  --soundfont "${SOUNDFONT}"

# Condition outputs are written under:
#   ./gen/acr-conditions/<condition>/scores.txt
#   ./gen/acr-conditions/<condition>/scores.txt.manifest.json
#   ./gen/acr-conditions/<condition>/midi/START_SONG_<ordinal>.mid
#   ./gen/acr-conditions/<condition>/audio/song_<ordinal>/mix.flac
#
# The condition manifests retain the MIDI and audio checksums, SoundFont
# checksum, renderer versions, selected role mask, and mix profile.

All projections reuse the canonical source cohort and render seed. The
canonical render pays the symbolic-generation cost once; projection is a
role-mask operation, and MIDI conversion is an independently stoppable
follow-up stage. Canonical all-role rendering with `render.py --stage audio`
also retains independent role stems for diagnostic ablations.
