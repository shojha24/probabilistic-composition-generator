Assuming distributions have already been calculated:

# 1. Generate the quota-aware target corpus:
#    5,000 songs total (2,500 per genre) and 500,000 chord events total.
#    Output directories will be:
#      ./gen/acr-target-500k/jazz-labels
#      ./gen/acr-target-500k/pop-rock-labels
python3 target_corpus_gen.py \
  --target-events 500000 \
  --target-songs 2500 \
  --tonic C \
  --bpm 120 \
  --seed 15001 \
  --dist-dir ./distributions \
  --out-dir ./gen/acr-target-500k

# 2. Generate the canonical all-role corpus:
#    V0 chords/arpeggios, V1 bass, V2 melody, V9 percussion.
#    30% arpeggios / 70% pads; melody and percussion are always included.
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
  --stage midi \
  --midi-output ./gen/midi/acr-canonical-all-roles

# 3. CB projection: V0 chords/arpeggios + V1 bass.
python3 render.py \
  --in-dir ./gen/acr-target-500k/pop-rock-labels \
  --in-dir ./gen/acr-target-500k/jazz-labels \
  --out ./gen/acr-condition-cb.txt \
  --seed 25001 \
  --mode mixed \
  --arpeggio-percent 30 \
  --pad-percent 70 \
  --condition cb \
  --stage midi \
  --midi-output ./gen/midi/acr-condition-cb

# 4. CBP projection: V0 + V1 + V9 percussion.
python3 render.py \
  --in-dir ./gen/acr-target-500k/pop-rock-labels \
  --in-dir ./gen/acr-target-500k/jazz-labels \
  --out ./gen/acr-condition-cbp.txt \
  --seed 25001 \
  --mode mixed \
  --arpeggio-percent 30 \
  --pad-percent 70 \
  --condition cbp \
  --stage midi \
  --midi-output ./gen/midi/acr-condition-cbp

# 5. CBM projection: V0 + V1 + V2 melody.
python3 render.py \
  --in-dir ./gen/acr-target-500k/pop-rock-labels \
  --in-dir ./gen/acr-target-500k/jazz-labels \
  --out ./gen/acr-condition-cbm.txt \
  --seed 25001 \
  --mode mixed \
  --arpeggio-percent 30 \
  --pad-percent 70 \
  --condition cbm \
  --stage midi \
  --midi-output ./gen/midi/acr-condition-cbm

# 6. CBMP projection: V0 + V1 + V2 melody + V9 percussion.
python3 render.py \
  --in-dir ./gen/acr-target-500k/pop-rock-labels \
  --in-dir ./gen/acr-target-500k/jazz-labels \
  --out ./gen/acr-condition-cbmp.txt \
  --seed 25001 \
  --mode mixed \
  --arpeggio-percent 30 \
  --pad-percent 70 \
  --condition cbmp \
  --stage midi \
  --midi-output ./gen/midi/acr-condition-cbmp

# 7. Naturalistic projection.
#    V0/V1 are always present. The 100% values preserve the requested
#    always-on melody and percussion setup while retaining naturalistic
#    condition-manifest semantics.
python3 render.py \
  --in-dir ./gen/acr-target-500k/pop-rock-labels \
  --in-dir ./gen/acr-target-500k/jazz-labels \
  --out ./gen/acr-condition-naturalistic.txt \
  --seed 25001 \
  --mode mixed \
  --arpeggio-percent 30 \
  --pad-percent 70 \
  --percussion-percent 70 \
  --condition naturalistic \
  --melody-percent 70 \
  --melody-profile lead-high-sparse \
  --melody-decoder sequence_beam \
  --stage midi \
  --midi-output ./gen/midi/acr-condition-naturalistic

All projections use the identical render seed ( 25001 ) and source corpus, preserving the deterministic 30/70 mode allocation and symbolic identity across outputs.