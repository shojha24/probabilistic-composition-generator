# Spec 00: ACR Synthetic-Data Ablation Study Design Notes

**Status:** Research-design note; freeze before corpus production
**Scope:** Comparing real-only ACR training with real data augmented by
synthetically realized chord, bass, percussion, and melody parts
**Related specifications:** [Spec 08 - Generation Targets](08-generation-targets.md),
[Spec 13.5 - Multitrack Rendering](13.5-multitrack-rendering.md),
[Spec 14 - Melody Generation](14-melody-generation.md), and
[Spec 15 - Sound Design and ACR Corpus Production](15-sound-design-and-acr-corpus.md)

This document records the planned experiment so that the study can be
implemented consistently after the symbolic generator and audio-rendering
pipeline are ready. It is a research protocol, not a request to make the
generator produce five unrelated corpora.

## 1. Research question

The study asks whether artificially realized songs improve automatic chord
recognition (ACR) when added to existing real-world ACR training corpora, and
which synthetic musical parts account for any improvement.

The primary comparison is:

```text
real-only training
    versus
real training + matched synthetic data with selected role combinations
```

The synthetic examples must share their underlying chord-event labels and
composition identities across conditions. Only the selected synthetic
performance roles should change between the fixed ablation groups. This makes
the experiment about the information contributed by synthetic accompaniment
parts rather than about different randomly generated songs.

## 2. Study arms

The study has one control and five experimental groups. Every experimental
group uses the same real training data as the control and the same number of
base synthetic compositions.

| Arm | Training data | Synthetic role condition | Purpose |
|---|---|---|---|
| Control | Real data only | No synthetic examples | Baseline performance from the existing corpus. |
| Experiment 1: CB | Real + synthetic | V0 chords/arpeggios + V1 bass | Measures the contribution of the minimal synthetic harmonic accompaniment. |
| Experiment 2: CBP | Real + synthetic | V0 + V1 + V9 percussion | Measures the incremental effect of percussion relative to CB. |
| Experiment 3: CBM | Real + synthetic | V0 + V1 + V2 melody | Measures the incremental effect of melody relative to CB. |
| Experiment 4: CBMP | Real + synthetic | V0 + V1 + V2 + V9 | Measures the fixed, all-role synthetic condition. |
| Experiment 5: Naturalistic mix | Real + synthetic | V0 + V1, with naturalistic V2 and V9 quotas | Measures the practical value of a heterogeneous corpus in which melody and percussion are present for some songs and absent for others. |

The role names map to the current score contract:

| Role | JFugue voice | Meaning |
|---|---|---|
| Chords/arpeggios | V0 | The realized chord accompaniment, including either pads or timed arpeggios. |
| Bass | V1 | The generated bass part. |
| Melody | V2 | The optional chord-conditioned naturalistic melody. |
| Percussion | V9 | The synchronized percussion groove or rest timeline. |

### 2.1 Fixed experimental groups

Experiments 1 through 4 are the controlled ablation arms. Their synthetic
module inclusion is fixed for every synthetic song:

| Arm | `melody_condition` | `melody_percent` | `percussion_percent` |
|---|---|---:|---:|
| CB | `none` | 0 | 0 |
| CBP | `none` | 0 | 100 |
| CBM | `naturalistic` | 100 | 0 |
| CBMP | `naturalistic` | 100 | 100 |

The exact command-line/API values may be represented by a higher-level
condition profile, but the resolved values must be recorded in the manifest.
The 100% settings are intentional: an arm named "with melody" or "with
percussion" should not silently contain a mixture of present and absent
tracks.

### 2.2 Experiment 5: naturalistic mixture

Experiment 5 uses the ordinary naturalistic corpus policy rather than fixed
presence:

- V0 and V1 are present for every synthetic song.
- V2 uses the naturalistic melody quota, initially 70% of eligible songs.
- V9 uses the existing percussion quota, initially 70% of eligible songs.
- A song outside either quota retains a synchronized rest track and an
  auditable omission reason.
- Melody and percussion inclusion are selected deterministically and recorded
  per song.

For `N` eligible songs and requested percentage `p`, the target count is:

```text
min(N, max(0, floor(N * p / 100 + 0.5)))
```

This is an exact nearest-whole-song quota, not an independent per-song
Bernoulli draw. The group 5 manifest must record the requested percentages,
eligible count, target counts, realized counts, selected source IDs, and the
joint inclusion table:

| Melody | Percussion | Meaning |
|---|---|---|
| absent | absent | CB content |
| absent | present | CBP content |
| present | absent | CBM content |
| present | present | CBMP content |

With separate 70% quotas, the joint cells will not necessarily be exactly
49%, 21%, 21%, and 9% for a finite corpus. The realized counts are part of the
result, not an assumption. Group 5 is therefore a practical naturalistic
mixture, not a fifth fixed cell in the factorial design.

## 3. Canonical synthetic source set

### 3.1 Generate once, project many times

Each base synthetic song must be generated once with all available symbolic
parts and one immutable source manifest:

```text
ChordEvent labels
    -> VoicedChord realization
        -> V0 chords/arpeggios
        -> V1 bass
        -> V2 melody
        -> V9 percussion
```

The generator must retain:

- the original chord-event labels and boundaries;
- source song ID and composition-family ID;
- generator revision and source hash;
- base generation seed and derived child seeds;
- selected voicer and render mode;
- V0, V1, V2, and V9 score strings;
- melody/percussion inclusion decisions;
- all role and provenance hashes.

The fixed experimental conditions are then deterministic projections of this
bundle. They must not call the chord, voicing, bass, melody, or percussion
generators again.

This protects the core paired-comparison invariant:

```text
same source composition
same V0
same V1
same V2 when selected
same V9 when selected
different selected-role mask only
```

### 3.2 Matched composition cohort

The same ordered source-song cohort must feed all four fixed experimental
groups and group 5. If the synthetic corpus contains `N` base songs, each
experimental arm receives exactly `N` synthetic song identities, even when a
condition contains silent/rest roles.

Do not replace omitted roles by generating replacement songs. Doing so changes
the distribution of chord progressions and creates a sample-count confound.

All arms should share, unless the study explicitly introduces a secondary
factor:

- chord-event sequences and labels;
- source seeds and composition-family assignments;
- voicer choices and V0 realization;
- bass realization;
- tempo map, meter, and duration;
- pad/arpeggio allocation;
- melody generation profile and decoder;
- percussion pattern decisions;
- instrument/asset assignments;
- humanization settings; and
- audio mix/master settings.

The study may use multiple independent base seeds, but each seed must produce
the complete set of condition views before any arm is trained.

### 3.3 Fixed versus naturalistic variation

The purpose of groups 1 through 4 is to isolate role inclusion. Therefore,
group 5 should not quietly introduce unrelated differences in voicers, pad
versus arpeggio rates, sound assets, loudness, or mastering.

For the primary ablation:

- freeze one render profile shared by all five arms;
- use the same pad/arpeggio allocation in every arm;
- use the same instrument and sound-asset assignment per base song; and
- change only the selected-role mask and the fixed/quota inclusion policy.

If a second goal is to test a broad naturalistic rendering distribution, define
that as a separate render-profile factor and apply it deliberately. Do not
attribute a group 5 gain to melody/percussion quotas if it also comes from
different timbres, rooms, or mastering.

## 4. Score, MIDI, and audio artifacts

### 4.1 Canonical role outputs

The current multitrack renderer should remain the source export:

```text
mixed     -> V0 + V1 + V2 + V9 when all roles are available
chords    -> V0
bass      -> V1
melody    -> V2
percussion-> V9
manifest  -> source identity, provenance, quotas, and hashes
```

Canonical role sidecars should be generated once. They are the reusable
building blocks for every study arm.

### 4.2 Condition-specific derived scores

For the current JFugue/MIDI boundary, materialize one derived mixed score per
condition. This makes the exact input to the existing MIDI converter visible
and independently reproducible:

```text
condition=cb    -> V0 + V1
condition=cbp   -> V0 + V1 + V9
condition=cbm   -> V0 + V1 + V2
condition=cbmp  -> V0 + V1 + V2 + V9
condition=nat   -> per-song mask from the naturalistic quota manifest
```

These are projections of the canonical role tracks, not new generation paths.
The condition score should preserve the original role strings, absolute timing,
tempo, programs, durations, and markers. A condition-specific score must not
reroll a voicer, choose a new melody, or regenerate percussion.

The condition bundle should include:

- the selected-role score;
- optional selected-role MIDI;
- the source role-score hashes;
- the condition ID;
- the per-song selected-role mask;
- source manifest hash;
- condition/render-profile hash; and
- the shared label and timing metadata.

### 4.3 Future audio rendering

The future audio renderer should consume the same explicit selection manifest.
It must not infer inclusion by inspecting filenames, empty tracks, or available
MIDI channels.

The preferred long-term path is:

1. render the canonical role stems once with a pinned renderer and asset set;
2. mix selected stems according to `selected_roles`;
3. apply the declared common mix/master policy;
4. write the condition-specific audio and manifest;
5. validate stem alignment, duration, loudness, and checksums.

This avoids unnecessary resynthesis and ensures that a CB and CBMP render
differ because of selected content, not because the synthesizer made a new
random performance.

## 5. Condition manifest contract

Every condition record should contain at least:

```json
{
  "condition": "cbm",
  "condition_description": "chords + bass + naturalistic melody",
  "base_song_id": "song_0042",
  "composition_family_id": "family_0042",
  "source_manifest_sha256": "...",
  "selected_roles": ["chords", "bass", "melody"],
  "selected_voices": ["V0", "V1", "V2"],
  "role_presence": {
    "chords": true,
    "bass": true,
    "melody": true,
    "percussion": false
  },
  "role_score_hashes": {
    "chords": "...",
    "bass": "...",
    "melody": "...",
    "percussion": "..."
  },
  "melody_included": true,
  "percussion_included": false,
  "label_source": "immutable_chord_events"
}
```

For group 5, `melody_included` and `percussion_included` are resolved
per-song values. For groups 1 through 4 they are fixed by the arm definition.
Absent roles must be represented explicitly in metadata even if they are not
present as audible voices in a condition-specific mixed score.

At the corpus level, record:

- study protocol version;
- arm/condition ID;
- base synthetic cohort hash;
- source generator revision;
- render profile and asset registry version;
- requested and realized role quotas;
- selected source IDs per role;
- total synthetic song/event/duration counts;
- split assignments;
- complete-file and per-block hashes; and
- any audio QA or rendering failures.

## 6. Dataset construction and training protocol

### 6.1 Real-data invariants

The real corpus must be identical across the control and all experimental
arms:

- same source files;
- same train/validation/test split;
- same preprocessing;
- same label representation;
- same sample/window extraction;
- same augmentation policy unless it is explicitly part of the study; and
- same evaluation set.

The control must contain no synthetic examples. Synthetic data must not leak
into the real validation or test set.

### 6.2 Synthetic-data invariants

For a fair comparison:

- keep the number of base synthetic songs equal across experimental arms;
- keep the synthetic duration or training-example budget equal;
- keep the real-to-synthetic ratio equal;
- keep the optimizer, architecture, training steps, and sampling policy equal;
- use identical model initialization seeds within each replicate; and
- record whether sampling is source-balanced or duration-weighted.

If one condition produces different valid frame counts because of a role
selection or audio-QA failure, do not silently oversample it. Either repair
the rendering issue, remove the matched source from every experimental arm, or
declare a preplanned rebalancing rule.

### 6.3 Split leakage

Split at the composition-family level. All condition views derived from one
base synthetic composition must stay in the same split. Do not place CB in
training and CBMP from the same composition in validation.

The same rule applies to:

- melody-inclusion variants;
- percussion-inclusion variants;
- role stems;
- condition-specific mixes;
- audio augmentations; and
- repeated renders with different sound assets.

The primary evaluation should be held-out real audio. A separate held-out
synthetic challenge set may measure symbolic-to-audio generalization but must
not replace the real-data evaluation.

## 7. Analysis plan

### 7.1 Fixed-arm comparisons

The four fixed experimental groups form a 2x2 design over percussion and
melody, with CB as the no-percussion/no-melody synthetic baseline:

```text
CB    = no percussion, no melody
CBP   = percussion,    no melody
CBM   = no percussion, melody
CBMP  = percussion,    melody
```

Report:

- percussion effect without melody: `CBP - CB`;
- melody effect without percussion: `CBM - CB`;
- melody effect with percussion: `CBMP - CBP`;
- percussion effect with melody: `CBMP - CBM`; and
- interaction estimate: `CBMP - CBP - CBM + CB`.

Each term should be computed on the same held-out real evaluation set. Report
absolute performance, confidence intervals or replicate variability, and
relative improvement over the real-only control.

### 7.2 Group 5 interpretation

The naturalistic mixture is not a clean factorial cell because it contains
multiple role combinations and quota-selected omissions. Treat it as a
practical corpus arm:

- compare Naturalistic Mix against Control to measure overall utility;
- compare Naturalistic Mix against CBMP to measure fixed-dense versus
  heterogeneous role inclusion;
- report its four realized joint-inclusion cells separately; and
- stratify results by the actual per-song role mask.

Do not interpret a Naturalistic Mix versus CBMP difference as a pure melody or
percussion effect. It also reflects missing-role diversity and any selected
naturalistic profile behavior.

### 7.3 ACR metrics

Use the same metrics for every arm:

- frame-level root accuracy;
- chord-quality accuracy;
- extension/seventh accuracy where labels support it;
- no-chord precision and recall;
- event-level accuracy;
- macro metrics by genre, chord family, key/mode, voicing, and duration;
- calibration and confidence behavior; and
- error/confusion matrices.

Report results separately for the real evaluation set and any synthetic
challenge set. The primary claim should be about improvement on held-out real
audio.

## 8. Replicates and statistical discipline

One generation seed and one model seed are not enough to support a strong
claim. The preferred design uses:

- multiple independent synthetic-cohort seeds;
- multiple model-initialization/training seeds;
- the same seed grid for every arm; and
- paired analysis across the same source cohort and evaluation examples.

At minimum, preserve all seeds and report the per-replicate result before
aggregating. If compute limits require a smaller pilot, label it exploratory
and do not use it to select a final corpus policy without a confirmatory run.

## 9. Implementation mapping to the current repository

The current code already provides most of the required symbolic inputs:

| Need | Current capability | Required study addition |
|---|---|---|
| Chord labels | Source JSON and render manifests | Carry unchanged into each condition manifest. |
| V0/V1/V2/V9 role scores | `render_song_tracks()` and directory sidecars | Add deterministic role-mask projection and condition output bundles. |
| Exact melody quota | `melody_inclusion_plan()` and `--melody-percent` | Reuse for group 5; force 0/100 for fixed arms. |
| Exact percussion quota | `percussion_inclusion_plan()` and `--percussion-percent` | Reuse for group 5; force 0/100 for fixed arms. |
| Reproducible source ordering | Numeric song ordering and manifests | Store one shared base cohort/order for all arms. |
| Role pairing | `track_outputs` and multitrack validator | Validate condition masks and cross-condition role hashes. |
| MIDI conversion | `HumanizedMidiRenderer.java` | Convert condition scores after fixing its unseeded humanization RNG. |
| Audio production | Not yet implemented | Follow Spec 15; consume explicit condition manifests. |
| Model comparison | Outside this repository | Keep training/evaluation scripts pinned to this protocol. |

The preferred implementation shape is a small condition registry plus a pure
projection helper. It should accept a `RenderedSongTracks` bundle and return
the selected role line without rerunning any generator:

```python
CONDITIONS = {
    "cb": ("chords", "bass"),
    "cbp": ("chords", "bass", "percussion"),
    "cbm": ("chords", "bass", "melody"),
    "cbmp": ("chords", "bass", "melody", "percussion"),
}
```

Group 5 should use a per-song resolved mask from the quota manifest rather than
one global `nat` mask.

## 10. Acceptance criteria

The study implementation is ready for a pilot when:

1. One base synthetic cohort produces all five experimental manifests without
   regenerating the underlying songs.
2. Fixed groups contain exactly the role combinations declared in the arm
   table.
3. Group 5 records exact melody and percussion quota counts and the four-cell
   joint inclusion table.
4. V0 and V1 score hashes are identical across all condition views for each
   base song.
5. A selected V2 or V9 hash matches the canonical role hash exactly.
6. Condition-specific scores preserve source timing, labels, programs, and
   output ordinals.
7. Repeating the same cohort seed and condition profile reproduces all score
   files and manifests byte-for-byte.
8. No condition-specific render consumes a new random draw from another
   module's stream.
9. Every condition view has a source manifest, selection mask, role hashes,
   split assignment, and provenance record.
10. The control and all experimental arms use the same real-data evaluation
    set and training-budget policy.
11. All condition views derived from one composition family remain in one
    split.
12. Pilot results report absolute metrics, control-relative differences, and
    replicate variability.

## 11. Open decisions

1. Should the primary shared render profile use pads only, or a fixed
   pad/arpeggio mixture?
2. Which exact melody profile and decoder should be frozen for CBM, CBMP, and
   the naturalistic mixture?
3. Should group 5 use 70% for both melody and percussion in the first release,
   or should one quota be changed to produce a desired joint-cell balance?
4. Should group 5 share the fixed arms' timbre/mix profile, with naturalistic
   variation tested separately?
5. What synthetic-to-real ratio and total training-example budget will be
   fixed across model runs?
6. How many independent generation and model seeds are affordable for the
   pilot and confirmatory studies?
7. Which ACR metrics and minimum practically meaningful improvement define a
   successful augmentation?

## 12. Summary

The recommended design is a matched five-arm study:

```text
Control: real
Exp 1:   real + synthetic CB
Exp 2:   real + synthetic CBP
Exp 3:   real + synthetic CBM
Exp 4:   real + synthetic CBMP
Exp 5:   real + synthetic naturalistic mixture
```

Generate each synthetic song once, retain all role scores and provenance, and
derive the arm-specific JFugue/MIDI/audio views through explicit role masks.
Groups 1 through 4 provide interpretable fixed-part ablations. Group 5
provides the practical naturalistic corpus condition, with exact melody and
percussion quotas and per-song role diversity. Keeping those two purposes
separate makes the fixed-arm effects interpretable while still testing the
mixed corpus that is most likely to be used in practice.
