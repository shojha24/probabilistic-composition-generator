# Probabilistic Composition Generator

> **Preprint Companion Repository:** *Distribution-Shifted Synthetic Multitrack Pretraining for Large-Vocabulary Audio Chord Recognition* (Sharabh Ojha, UCLA)  
> **Companion Benchmark & Sim-to-Real Training Repository:** [`ChordFormer-Artificial-Dataset-Benchmarking`](https://github.com/shojha24/ChordFormer-Artificial-Dataset-Benchmarking)

## Overview: Distribution-Shifted Harmonic Sampling & Random-Mix Synthesis Pipeline

This repository implements the procedural symbolic-to-acoustic data generation engine described in Section 3 of our preprint. Rather than replicating the severe Zipfian major/minor skew of real-world musical corpora (>87% `maj`/`min` duration in the 1,217-song benchmark), the generator acts as an explicit **distributional intervention** across three coupled subsystems:

1. **Distribution-Shifted Harmonic Sampling (`extract_distributions.py`, `chord_gen.py`, `target_corpus_gen.py`):**
   - Factorizes each chord event $C_t$ by extending the 6-head structural space of Jiang et al. (2019) and ChordFormer (Akram et al., 2025): whereas the original ChordFormer (`triad_limit=6`, 100 logits) does not classify power chords (`5`) or root-only unisons (`1`) separately—collapsing them into the `maj` triad—our generator synthesizes dedicated standalone exemplars for `5` and `1`, allowing Head 0 to expand to 8 triad qualities $S_t = (r_t, q_t)$ (97 classes including `N`), alongside Bass $b_t$ (13 classes), and 7th (`N, 7, b7, bb7`), 9th (`N, 9, #9, b9`), 11th (`N, 11, #11`), and 13th (`N, 13, b13`) extensions (124 logits total).
   - Samples progressions along empirical transition graphs extracted via ChoCo from McGill Billboard and OMRAS2 (1,114 pop/rock songs; 139,078 events) plus the Weimar Jazz Database and iReal Pro (2,477 jazz charts; 161,022 events) using Witten-Bell interpolated backoff ($D = 25.0$), Stage 1 temperature scaling ($\tau_{1,\text{pop}} = 2.2$, $\tau_{1,\text{jazz}} = 2.0$), and a 4-slot extension prefix trie (Trie A/B/C).
   - Applies **deterministic quota scheduling** across a 50/50 split of 2,500 natural songs and 2,500 targeted songs ($S_{\text{triad}} = 3.0\times$ rare-triad multiplier, minimum spacing $\Delta_{\text{pos}} \ge 2$ along attested 1- or 2-step transitions), expanding duration shares relative to the 1,217-song real corpus by **$15.6\times$ on `sus2`** ($0.34\% \to 5.31\%$), **$9.2\times$ on `dim`** ($0.49\% \to 4.53\%$), **$8.5\times$ on `aug`** ($0.21\% \to 1.77\%$), **$2.4\times$ on `sus4`** ($2.21\% \to 5.28\%$), **$12.3\times$ on `b9`** ($0.13\% \to 1.54\%$), **$8.3\times$ on `b13`** ($0.08\% \to 0.68\%$), **$7.1\times$ on `#11`** ($0.13\% \to 0.92\%$), and **$2.2\times$ on `#9`** ($0.56\% \to 1.25\%$).
2. **Physically Constrained Multi-Instrument Voicing & Humanization (`render.py`, `voicing/select.py`, `HumanizedMidiRenderer.java`):**
   - Arranges progressions onto a 4/4 16th-note grid across four synchronized JFugue voices: `V0` harmonic accompaniment (70% block pads, 30% timed arpeggios), `V1` bass (MIDI $[28, 55]$, $\text{E}_1$--$\text{G}_3$), `V2` chord-conditioned monophonic lead/melody, and `V9` Channel 10 percussion.
   - Enforces **acoustic faithfulness** across six genre-instrument policies (`pop-piano`, `pop-guitar`, `pop-synth`, `jazz-piano`, `jazz-guitar`, `jazz-synth`): $100\%$ of labeled $7\text{th}$--$13\text{th}$ extensions are physically sounded in piano/synth policies (`<0.5%` drop rate on 6-string guitar shapes with $\le 4$-fret spans), the Distinguishing Chord Tone (DCT) is exposed against upper-root masking, and voicings are sampled via a Boltzmann distribution ($\tau_{\text{voic}} \in [2.2, 6.0]$) over Hungarian voice-leading distance and Neo-Riemannian $\text{P}/\text{L}/\text{R}$ parsimony.
   - Applies micro-timing humanization ($\sigma_t = 15.0\,\text{ms}$ Gaussian jitter, role-specific pocket offsets, $7.0\,\text{ms}$/string guitar strums, CC64 sustain pedaling, and velocity humanization).
3. **Multi-SoundFont Synthesis & Random-Mix Augmentation (`audio_render.py`, `tools/project_condition_corpus.py`):**
   - Renders isolated stems via FluidSynth at $f_s = 22{,}050\,\text{Hz}$ (16-bit FLAC) rotating across four General MIDI SoundFonts (`FluidR3_GM`, `MuseScore_General_Full`, `GeneralUser_GS`, `TimGM6mb`).
   - Mitigates acoustic overfitting via independent Bernoulli stem dropout on `V2` and `V9` ($P = 0.70$ each: 49% four-stem `cbmp`, 21% `cbm`, 21% `cbp`, 9% `cb`), continuous stem gain perturbations ($\Delta G \in [-4, +4]\,\text{dB}$), FFmpeg nonlinear bass/console saturation, guitar cabinet overdrive/EQ, split-band room convolution ($1.2/0.8/0.4\,\text{s}$, 82%/18% dry/wet), continuous pitch detuning ($\Delta f_{\text{cents}} \sim U(-25, +25)\,\text{cents}$), spectral tilt, bus compression, and $-18.0\,\text{LUFS}$ loudness normalization.

---

## Reproducing the 5,000-Song Pretraining Dataset & `.lab` Annotations

Execute the following commands from the repository root to generate the **5,000-song ($460.3\,\text{h}$, 500,000-event)** synthetic pretraining dataset (65% pop/rock = 3,250 songs; 35% jazz = 1,750 songs; 50% natural + 50% quota-targeted) and export frame-exact Harte `.lab` annotations:

```bash
# 1. Extract empirical Stage 1/2/3 transition tables from normalized ChoCo/JAMS corpora
python3 extract_distributions.py \
  --data-root ./data_normalized \
  --out-dir ./distributions

# 2. Sample the 2,500 natural songs (1,625 pop/rock + 875 jazz, 100 events/song)
python3 chord_gen.py \
  --genre pop_rock --songs 1625 --num 100 --debug --seed 42 \
  --dist-dir ./distributions --out-dir ./gen/acr-target-5k/natural-pop-rock-labels

python3 chord_gen.py \
  --genre jazz --songs 875 --num 100 --debug --seed 42 \
  --dist-dir ./distributions --out-dir ./gen/acr-target-5k/natural-jazz-labels

# 3. Sample the 2,500 quota-scheduled targeted songs (250,000 events with 3.0x rare-triad upweighting)
python3 target_corpus_gen.py \
  --target-events 250000 \
  --target-songs 1250 \
  --debug \
  --seed 42 \
  --dist-dir ./distributions \
  --out-dir ./gen/acr-target-5k/targeted

# 4. Render synchronized 4-voice JFugue scores (70% pads / 30% arpeggios, 100% canonical stems for projection)
python3 render.py \
  --in-dir ./gen/acr-target-5k/natural-pop-rock-labels \
  --in-dir ./gen/acr-target-5k/natural-jazz-labels \
  --in-dir ./gen/acr-target-5k/targeted/pop-rock-labels \
  --in-dir ./gen/acr-target-5k/targeted/jazz-labels \
  --out ./gen/acr-target-5k/all_roles_5k.txt \
  --mode mixed --pad-percent 70 --arpeggio-percent 30 \
  --melody-condition naturalistic --melody-percent 100 \
  --percussion-percent 100 \
  --seed 42

# 5. Project random-mix stem conditions (P=0.70 Bernoulli dropout on V2/V9) and render 22,050 Hz FLAC audio
python3 tools/project_condition_corpus.py \
  --canonical ./gen/acr-target-5k/all_roles_5k.txt \
  --out-dir ./gen/acr-conditions-5k \
  --conditions cb cbp cbm cbmp naturalistic \
  --stage audio \
  --soundfont ./soundfonts/FluidR3_GM.sf2 \
  --seed 42

# 6. Export frame-exact Harte .lab annotations (anchored at t = 0.000 s, start_padding_frames=0)
#    and build the 252-bin CQT HDF5 storage for Stage 1 pretraining
python3 ../ChordFormer-Artificial-Dataset-Benchmarking/convert_synth_labels.py \
  --total_songs 5000 --pop_count 3250

python3 ../ChordFormer-Artificial-Dataset-Benchmarking/create_synth_storage.py \
  --overwrite --num_songs 5000 --num_workers 16
```

---

## Purpose

This project creates symbolic chord progressions for chord-recognition data.

The current pipeline has these Python stages:

1. `extract_distributions.py` learns count tables from normalized JAMS files.
2. `chord_gen.py` samples chord-event sequences from those tables and assigns
   genre-specific durations.
3. `chord_module.py` selects a genre-compatible voicer and renders pad or
   instrument-aware arpeggiated chords.
4. `bass_module.py` renders the generated bass notes.
5. `percussion_module.py` renders an optional synchronized percussion groove.
6. `render.py` combines the tracks into synchronized JFugue score text.

The voicing stage is documented in [`voicing/README.md`](voicing/README.md).
It converts chord events into MIDI pitches after chord generation.

`target_corpus_gen.py` is a separate opt-in generator for the §08
quota-aware corpus. It reuses `chord_gen.py`'s natural sampling primitives but
does not change the ordinary generator interface or behavior.

JFugue score text is now produced by `render.py`. MIDI conversion and
humanization use `HumanizedMidiRenderer.java`.

## Pipeline

```text
normalized JAMS files
        |
        v
extract_distributions.py
        |
        +--> distributions/pop_rock/
        +--> distributions/jazz/
        |
        v
chord_gen.py
        |
        +--> chord-event JSON files with durations
        |
        v
chord_module.py + bass_module.py + percussion_module.py
        |
        +--> synchronized JFugue score tracks
        |
        v
render.py
        |
        v
HumanizedMidiRenderer.java
        |
        v
MIDI files
        |
        v
FluidSynth + ffmpeg
        |
        +--> role FLAC stems
        +--> mixed FLAC audio
```

The first two Python stages do not render audio. They create and sample
symbolic chord events.

## Stage 1: extract distributions

Run the extractor from the repository root:

```bash
python extract_distributions.py \
  --data-root ./data_normalized \
  --out-dir ./distributions
```

If no options are given, the script uses:

- `DATA_ROOT`, when the environment variable is set;
- otherwise `./data_normalized`;
- otherwise `./distributions` for output.

The extractor routes source corpora to these genre groups:

- `pop_rock`
- `jazz`

The extractor reads normalized JAMS data. It parses:

- key and mode data;
- root notes;
- triad quality;
- bass notes;
- seventh;
- ninth;
- eleventh;
- thirteenth.

Unparseable chords and silence markers break a training sequence. The extractor
does not join Markov context across a broken sequence.

### Extracted tables

For each genre, the extractor writes:

```text
distributions/{genre}/stage1_backbone.json
distributions/{genre}/stage2_bass.json
distributions/{genre}/stage3_extensions.json
distributions/{genre}/meta.json
```

It also writes:

```text
distributions/extraction_report.json
```

The files contain raw counts and probability data.

### Stage 1 table

The backbone table models the `(root, triad)` state.

It contains:

- level 0 unigram counts;
- level 1 bigram counts;
- level 2 trigram counts.

The generator uses these tables with interpolated backoff.

### Stage 2 table

The bass table models bass behavior.

It contains:

- `P(bass_t | root_t)`;
- `P(bass_t | root_t, bass_{t-1})`.

### Stage 3 table

The extension table models this ordered slot sequence:

```text
seventh -> ninth -> eleventh -> thirteenth
```

It contains three trie levels:

- Level A: `(root, triad)` context;
- Level B: triad context;
- Level C: genre-wide context.

Each trie node contains marginal, bass-conditioned, and history-conditioned
counts.

## Stage 2: generate chord events

Run the generator after the distribution files exist:

```bash
python chord_gen.py \
  --genre jazz \
  --tonic C \
  --num 48 \
  --bpm 120 \
  --songs 100 \
  --seed 7 \
  --dist-dir ./distributions
```

The default genre is `jazz`.

Generated files are placed automatically in `gen/jazz-labels/` for jazz and
`gen/pop-rock-labels/` for pop/rock. With `--random-genre`, each song is
written to the directory for the genre selected for that song. Use
`--out-dir PATH` when one shared output directory is required.

The default values are:

- tonic: `C`;
- chord count per song: `48`;
- BPM metadata: `120`;
- song count: `100`;
- random seed: unset;
- distribution directory: `./distributions`;
- output directory: genre-specific under `./gen/` (`jazz-labels` or
  `pop-rock-labels`); `--out-dir` overrides this behavior.

### Generator stages

For each song, the generator performs these stages:

1. Sample the root and triad with the Stage 1 backoff model.
2. Sample the bass with the Stage 2 autoregressive model.
3. Walk the Stage 3 extension trie.
4. Sample a duration from the genre-specific duration distribution.
5. Compute per-chord beat and second timing from the BPM.
6. Write the resulting chord events and timing metadata.

The generator does not choose MIDI octaves or instrument shapes. Those tasks
belong to `chord_module.py` and the voicing engine.

## Chord, bass, and score rendering

Generated JSON files contain a duration token and timing metadata for every
chord. The token is selected from the genre-specific duration distribution,
using the song BPM to calculate seconds.

Render a generated directory as synchronized JFugue score text:

```bash
python render.py \
  --in-dir ./gen/pop-rock-labels \
  --out ./gen/pop_rock_scores.txt \
  --mode pads \
  --seed 7
```

Render a deterministic percentage mix across the input songs:

```bash
python render.py \
  --in-dir ./gen/target-jazz-labels \
  --in-dir ./gen/target-pop-rock-labels \
  --out ./gen/mixed_scores.txt \
  --mode mixed \
  --arpeggio-percent 30 \
  --pad-percent 70 \
  --seed 7
```

Mixed mode selects the rendering mode once per source song using the supplied
seed and allocates the nearest whole-song count to each percentage. The
percentages must sum to 100; either percentage may be omitted and is inferred
as the complement. The per-song `render_mode` values and aggregate
`render_mode_counts`, `render_mode_percentages`, and `render_mode_targets`
are recorded in the manifest. Existing `pads` and `arpeggios` modes remain
single-mode renders.

Control the target percentage of corpus songs with audible percussion using
`--percussion-percent`; it defaults to `70` to preserve the existing 70/30
audible/silent split:

```bash
python render.py \
  --in-dir ./gen/pop-rock-labels \
  --out ./gen/percussion_scores.txt \
  --mode pads \
  --percussion-percent 85 \
  --seed 7
```

The value must be between 0 and 100. Directory rendering uses the seeded
percentage to select the nearest whole-song quota exactly, so a 30% request
on 10 songs produces percussion on exactly 3 songs. A song outside the
selected quota still gets a synchronized silent `V9` track. The requested
percentage, target count, allocation mode, and realized count are recorded in
the manifest; the legacy `percussion_inclusion_probability` field is retained
as the requested fraction, while `percussion_selection_mode` identifies the
exact-quota behavior. The Python directory API exposes the same control as
`percussion_percent`; single-song rendering retains its seeded one-song
inclusion behavior because no corpus quota exists.

Enable the opt-in naturalistic melody layer with a deterministic corpus quota:

```bash
python render.py \
  --in-dir ./gen/pop-rock-labels \
  --out ./gen/melody_scores.txt \
  --mode pads \
  --melody-condition naturalistic \
  --melody-percent 70 \
  --melody-profile lead-high-sparse \
  --seed 7
```

Naturalistic rendering adds a synchronized `V2` melody sidecar and records
per-event source mapping, role, pitch-source, local-key diagnostics, and
instrument-collapse provenance in the manifest. A song outside the exact
melody quota receives a synchronized V2 rest track with
`omission_reason=corpus_quota`. `--melody-percent 0` and
`--melody-percent 100` are supported explicitly.

### Reproducible sound-design stages

The score manifest is a closed-world sound-design record: it identifies the
selected roles, derived humanization seeds, reference render profile, source
checksums, and explicitly records `audio_rendered: false` until an approved
audio adapter is used. Generate one canonical all-role score first, then
project condition views without regenerating the underlying song:

```bash
python render.py --in-dir ./gen/pop-rock-labels --out ./gen/all_roles.txt \
  --seed 7 --melody-condition naturalistic --melody-percent 100 \
  --percussion-percent 100

python tools/project_condition_corpus.py \
  --canonical ./gen/all_roles.txt \
  --out-dir ./gen/conditions \
  --conditions cb cbp cbm cbmp naturalistic \
  --seed 7
```

Supported condition IDs are `cb`, `cbp`, `cbm`, `cbmp`, and `naturalistic`.
Fixed condition views force only their declared V0/V1/V2/V9 role inclusion;
the manifest retains each role's provenance and hash. The legacy
`render.py --condition` option remains useful for a one-off direct render, but
it is not the efficient matched-cohort projection path. To produce
deterministic MIDI as a separately stoppable stage, use:

```bash
python render.py --in-dir ./gen/pop-rock-labels --out ./gen/scores.txt \
  --seed 7 --stage midi --midi-output ./gen/midi
```

The MIDI stage compiles and invokes the project-owned `HumanizedMidiRenderer`
with a seed derived from the render seed and song ordinal. It records each
MIDI checksum in the manifest. Audio rendering is an explicit downstream
stage. It uses FluidSynth with a user-supplied SoundFont and ffmpeg for
lossless FLAC encoding and mixing:

```bash
python render.py --in-dir ./gen/pop-rock-labels --out ./gen/scores.txt \
  --seed 7 --stage audio \
  --midi-output ./gen/midi \
  --audio-output ./gen/audio \
  --soundfont ./soundfonts/reference.sf2
```

The audio stage converts the mixed and role score sidecars to MIDI, renders
the chord, bass, melody, and percussion stems independently, applies the
declared role gains and `reference-transparent-v1` loudness/true-peak policy,
and writes one `mix.flac` plus retained role FLAC files under
`./gen/audio/song_<ordinal>/`. The manifest records the SoundFont checksum,
FluidSynth/ffmpeg versions, profile, stem checksums, mix checksum, and
`audio_rendered: true`. Install `fluidsynth` and `ffmpeg` separately; the
repository does not redistribute a SoundFont.

For a MIDI-only manifest produced earlier, the same work is available as a
standalone command:

```bash
python audio_render.py \
  --manifest ./gen/scores.txt.manifest.json \
  --midi-dir ./gen/midi \
  --output-dir ./gen/audio \
  --soundfont ./soundfonts/reference.sf2
```

Condition projections support `--stage audio` as well. Their projected MIDI
already contains the declared role mask, so the projection audio stage writes
the matched condition mix without regenerating symbolic material.

Directory rendering requires an explicit integer `--seed` and accepts one or
more `--in-dir` values. Each directory's source files are validated and
sorted by numeric ID before rendering, so `song_10.json` follows
`song_9.json`; input directories are processed in command-line order.
Malformed filenames, duplicate numeric IDs within one directory, and
duplicate input directories fail the render.

`render.py` combines three accompaniment tracks for each song by default and
adds a synchronized fourth track when naturalistic melody is enabled:

- `chord_module.py` tries the renderer's six-voicer order, prioritizing the
  song's genre, and renders either voiced block chords or profile-driven
  arpeggios from those same selected voicings;
- `bass_module.py` renders the generated bass pitch in a low register.
- `melody_module.py` optionally renders a chord-conditioned monophonic
  melody on voice `V2`.
- `percussion_module.py` repeats a seeded kick, snare, and cymbal groove on
  voice `V9` (the General MIDI percussion channel). Each song has a configurable
  chance of audible percussion, defaulting to 70%; the remaining songs retain
  a synchronized silent `V9` track. When percussion is audible, songs at
  60–120 BPM have a 20% chance of a cut-time (double-time) feel, while songs at
  121–180 BPM have a 20% chance of a half-time feel. The selected feel and
  inclusion result are recorded in the manifest.

The output uses `START_SONG_N` and `END_SONG` markers. All tracks use the
chord duration timeline, so they remain synchronized. In arpeggio mode,
each playable source event emits every selected V0 pitch exactly once on a
profile-selected sixteenth or eighth-note grid. A pair of adjacent pitches may
share an onset, and every arpeggiated note ends at the source-event boundary
with at least an eighth note of sustain. If a source event cannot fit the
complete pass while preserving that sustain tail, it is rendered as a
simultaneous pad chord instead. No-chord events remain duration-sized rests.
The manifest records the performance profile, timing, pair/onset counts,
per-event pad fallbacks, pattern provenance, source voicings, and guitar
string metadata. Timed V0 scores also carry `#ARPEVENT<n>` marker metadata at
source boundaries so the Java humanizer can preserve those limits; the
markers do not produce notes.

Built-in profiles use a 35% chance of an eligible eighth-note pass when the
profile's fast-tempo preference does not already select eighths, and a 25%
chance of pairing each available adjacent pitch pair. Pairs are forced when
they are required to fit the complete voicing and its eighth-note sustain tail
in the selected chord duration. Events too short even with forced pairs use
the simultaneous pad fallback.

The chord track is written to JFugue voice `V0` and the bass track to `V1`.
Each note in a block chord receives the duration token. This keeps all chord
tones sustained for the complete chord duration and prevents the bass from
playing after the chord track.

Every score has a sidecar manifest at
`<score>.manifest.json`. It records the numeric source ID and filename,
source directory, source SHA-256, genre, tonic, BPM, chord count, selected
voicer, per-song seed, render command, and generator revision. The manifest is
the authoritative pairing between source labels and `START_SONG_N` score
blocks. It also records the six-voicer preference order and aggregate
`voicer_counts`. Arpeggio records additionally include the selected
performance profile, fixed 4/4 meter, per-event subdivision/onset/attack/pair
counts, pattern and motif provenance, sustain-to-event-end behavior, source
voicing MIDI lists, and guitar string metadata when applicable.
Each failed fallback voicer is logged with its policy and failing chord before
the next voicer is tried. If every policy fails, the raised error repeats the
complete per-voicer failure list. A voicing failure stops the render; events
are never silently skipped.

Every directory render also writes synchronized role exports beside the mixed
score. For `scores.txt`, the additional files are
`scores_chords.txt`, `scores_bass.txt`, `scores_percussion.txt`, and the
existing `scores.txt.manifest.json`. They contain the same
`START_SONG_N`/`END_SONG` blocks and preserve the exact retained JFugue V0,
V1, and V9 fragments. The manifest records the derived paths, voice mapping,
complete-file SHA-256 hashes, output ordinals, and per-block hashes. Legacy
manifests without `track_outputs` remain valid for mixed-only artifacts.

Instrument programs are selected reproducibly when `--seed` is supplied.
Chord tracks choose from the expanded role-aware catalog in `instruments.py`.
This includes grand, bright, honky-tonk, Rhodes, DX7, harpsichord, clavinet,
vibraphone, marimba, organ, acoustic and electric guitars, overdrive,
distortion, sitar, banjo, shamisen, koto, synth pads, choir, bowed, metallic,
halo, sweep, synth strings, atmosphere, and soundtrack programs.

Bass tracks choose among acoustic, finger/pick electric, fretless, slap,
synth, contrabass, cello, square-wave, sawtooth, and basslead programs.
Instruments marked for pads, arpeggios, or bass are filtered by rendering
mode. The programs are zero-based General MIDI values, which are the numeric
form accepted by JFugue's `I<number>` syntax.

In pad mode, the bass has a seeded 30% chance to use the same instrument
program as the chord pad. Its pitch remains in the bass register; only the
instrument timbre collapses to the pad timbre.

When the generated bass pitch is in MIDI 40–47 and the corresponding chord
voicing also contains a note in MIDI 40–47, the bass moves down one octave.
If there is no overlap, the bass pitch is unchanged.

### Useful generator options

Use a fixed seed for repeatable output:

```bash
python chord_gen.py --genre jazz --seed 7
```

Use random song properties:

```bash
python chord_gen.py --debug
```

The debug shortcut enables random genre, tonic, song length, and BPM.

Control generation smoothing and backoff:

```bash
python chord_gen.py \
  --discount 200 \
  --add-k 0 \
  --min-count-a 5000 \
  --min-count-b 5000 \
  --epsilon-floor 0.0
```

Control Stage 1 sampling:

```bash
python chord_gen.py \
  --temperature 1.5 \
  --self-transition-discount 0.25
```

The temperature changes Stage 1 probability concentration. The self-transition
discount reduces exact repeated `(root, triad)` states.

### Target corpus generation

The ordinary `chord_gen.py` interface generates natural, unconstrained songs.
To generate the §08 quota-aware corpus, use the separate target generator:

```bash
python target_corpus_gen.py \
  --target-songs 100 \
  --seed 7 \
  --dist-dir ./distributions
```

For a target corpus with randomized per-song tonic and BPM values, use the
debug shortcut:

```bash
python target_corpus_gen.py --debug --seed 7
```

The target generator produces exactly 250,000 chord events by default: 125,000 jazz events
and 125,000 pop/rock events. It writes target output separately from the
validation-style generated labels:

```text
gen/target-jazz-labels/
gen/target-pop-rock-labels/
```

Target songs are tagged with a `voicer_family` hint, distributed approximately
equally among `guitar`, `piano`, and `synth`. Guitar-tagged songs use a
guitar-compatible sparse extension profile; the dense-extension quotas are
filled by the piano- and synth-tagged songs. During combined directory
rendering, `render.py` tracks all six realized voicers and orders candidates
hierarchically for each song: the least-used in-genre voicer, the hinted
in-genre voicer, the remaining in-genre voicers, then the equivalent
out-of-genre sequence. It still falls back when a progression cannot be
voiced by an earlier candidate.

Target generation does not preflight the assigned family. Family metadata is
only a rendering preference, so a full render may fall back to another
genre-compatible family when the hint cannot be realized.

Songs without `voicer_family` metadata, including older or independently
created JSON files, remain supported: combined directory rendering still uses
the six-voicer hierarchy, while standalone rendering chooses a
genre-compatible family using the normal family weights. An unrecognized hint
is treated as absent.

Render both target directories into one score corpus:

```bash
python render.py \
  --in-dir ./gen/target-jazz-labels \
  --in-dir ./gen/target-pop-rock-labels \
  --out ./gen/scores.txt \
  --mode pads \
  --seed 7
```

Validate the combined target score corpus and write a machine-readable report:

```bash
python3 eda/validate_rendered_corpus.py \
  --labels-dir ./gen/target-jazz-labels ./gen/target-pop-rock-labels \
  --scores ./gen/scores.txt \
  --manifest ./gen/scores.txt.manifest.json \
  --json-out ./gen/rendered_corpus_validation.json
```

The validator pairs events through the score's manifest and checks absolute
root and bass pitch classes, requested chord pitch classes, root omission,
extension retention, DCT exposure, MIDI ordering, low-interval limits, and
source hashes. It exits nonzero for a hard invariant or pairing failure. To
validate one corpus, pass one `--labels-dir`; for a combined corpus, pass both
label directories after the same option. `--manifest` and `--genre` are
optional.

Create a deterministic 100-song selection from the existing target labels,
rendered score, and MIDI artifacts:

```bash
python3 tools/curate_target_corpus.py \
  --manifest ./gen/target-scores.txt.manifest.json \
  --score ./gen/target-scores.txt \
  --midi-dir ./gen/target-output \
  --out-dir ./gen/curated-songs \
  --seed 20250308
```

The curation uses exact 50-jazz/50-pop-rock and 30-arpeggio/70-pad quotas,
balances all six realized voicer variants, and greedily covers the observed
triads, extensions, extension densities, duration tokens, root/bass
intervals, instruments, percussion states, voicing decisions, and arpeggio
patterns. Each `song_NNN/` directory contains `labels.json`, `score.txt`,
`midi/song.mid`, separate `tracks/` files, and `metadata.json`. The top-level
`manifest.json` records source ordinals, hashes, quotas, coverage, and
selection criteria; `validation.json` records the bundle-level checks.

Use `--target-events N` for a smaller or custom total. The event budget is
split between genres, and the §08 minimums are scaled for smaller budgets:

```bash
python target_corpus_gen.py \
  --target-events 20000 \
  --target-songs 100 \
  --seed 7 \
  --out-dir ./gen-target
```

With `--out-dir`, target mode creates `jazz-labels/` and
`pop-rock-labels/` subdirectories below the supplied path. In target mode,
`--target-songs` controls the number of songs **per genre** and defaults to
100. `--random-tonic` and `--random-bpm` are honored per song; `--debug`
enables both. Generation reports an error if a requested budget is too small
to provide at least one event per song.

## Chord-event data

The internal chord-event contract contains:

```text
root_interval
triad
bass_interval
seventh
ninth
eleventh
thirteenth
```

Intervals are pitch-class intervals relative to the active tonic or root
context defined by the generator specification.

The event also carries readable root, bass, and Harte fields when available.
The voicing engine uses the structured interval and token fields. It must not
parse the display names to recover chord meaning.

The supported triad classes are:

- `major`
- `minor`
- `diminished`
- `augmented`
- `sus4`
- `sus2`
- `5`
- `1`

`1` is a bare-root chord. It is valid input and must not be relabeled as major.

## Voicing integration

The voicing engine receives a `Song` containing `ChordEvent` records.

Example:

```python
from voicing.engine import Engine
from voicing.types import Song
from voicing.voicers import jazz_piano

engine = Engine(
    jazz_piano.POLICY,
    {"bass_module_active": True, "section": "verse", "seed": 7},
    root_omission_gate=True,
)

voiced = engine.run(song)
```

Select a different policy to change the instrument and genre behavior:

```python
from voicing.voicers import pop_piano, pop_guitar, pop_synth
from voicing.voicers import jazz_piano, jazz_guitar, jazz_synth
```

The voicing engine performs tone selection, candidate generation, hard
filtering, cost calculation, and probabilistic selection. See
[`voicing/README.md`](voicing/README.md) for the full process.

## Score and JFugue rendering

The rendering flow is:

```text
Chord events
    |
    v
Voiced MIDI pitches
    |
    v
JFugue Staccato pattern
    |
    v
HumanizedMidiRenderer
    |
    v
MIDI sequence
```

`render.py` currently produces the synchronized score text. It:

- reads generated chord-event JSON files;
- selects among six genre-family voicers, prioritizing the source genre;
- renders pad or instrument-aware timed arpeggios and bass notes;
- preserves each chord's duration token in both tracks;
- writes `START_SONG_N` and `END_SONG` blocks;
- writes a manifest sidecar for numeric source ordering and provenance.

Arpeggio mode schedules one attack for each note in the selected voicing
without changing the voicing, bass, or percussion decisions. Keyboard and
synth profiles use ascending pitch order; guitar profiles use the voicing
engine's physical string order. Attacks are normally sixteenth notes, with a
probabilistic eighth-note choice when the source chord is long enough for the
complete pass and its minimum eighth-note sustain tail. Adjacent notes may
also share an onset according to the profile's pair probability. Every
arpeggiated attack uses an absolute-time numeric JFugue duration that carries
it to the end of its source chord. If neither grid can satisfy the sustain
requirement, the event uses a simultaneous pad-style chord token instead.

`HumanizedMidiRenderer.java` provides the next, Java-based conversion step. It:

- reads JFugue pattern text;
- creates a MIDI sequence;
- applies timing humanization;
- groups notes that belong to one chord;
- applies velocity variation;
- applies channel timing bias;
- preserves note durations;
- resolves retrigger overlap;
- writes MIDI files.

The Python-to-JFugue event format is defined by `render.py`.

### Run the Java MIDI renderer

Install a JDK and place `jfugue-5.0.9.jar` in the repository root (or replace
the path below with the location of your JFugue JAR). First create score text:

```bash
python3 render.py \
  --in-dir ./gen/pop-rock-labels \
  --out ./gen/pop_rock_scores.txt \
  --mode pads \
  --seed 7
```

Compile the renderer:

```bash
javac -cp jfugue-5.0.9.jar HumanizedMidiRenderer.java
```

Run it with the score input and MIDI output directory:

```bash
java -cp ".:jfugue-5.0.9.jar" \
  HumanizedMidiRenderer \
  ./gen/pop_rock_scores.txt \
  ./gen/midi_output
```

On Windows, use `;` instead of `:` in the Java classpath:

```powershell
java -cp ".;jfugue-5.0.9.jar" HumanizedMidiRenderer `
  .\gen\pop_rock_scores.txt .\gen\midi_output
```

The renderer writes one `.mid` file per `START_SONG_N` block. The
`render.py --stage midi` and `render.py --stage audio` paths invoke this
renderer automatically.

Do not treat a JFugue string as the source of chord semantics. The structured
chord event and voiced MIDI data remain the source records.

## Current output boundaries

The current responsibilities are:

| Component | Responsibility |
|---|---|
| `extract_distributions.py` | Learn count tables |
| `chord_gen.py` | Sample natural timed symbolic chord events |
| `target_corpus_gen.py` | Generate the separate quota-aware target corpus |
| `tools/curate_target_corpus.py` | Create an auditable diversity-aware target subset |
| `chord_module.py` | Select a voicer and render pad or arpeggio chord tracks |
| `bass_module.py` | Render bass tracks |
| `percussion_module.py` | Render optional voice-9 percussion tracks |
| `melody_module.py` | Generate opt-in chord-aware voice-2 melody tracks |
| `render.py` | Combine tracks into score text, MIDI, and audio manifests |
| `audio_render.py` | Synthesize MIDI stems and mix lossless FLAC audio |
| `eda/validate_rendered_corpus.py` | Validate manifest-paired rendered corpora |
| `voicing/` | Select and realize MIDI voicings |
| `HumanizedMidiRenderer.java` | Convert JFugue text to humanized MIDI |
| `old_src/` | Historical implementation and reference material |

The Python pipeline produces score text and can invoke the separate MIDI and
audio stages. Java converts score text to MIDI; FluidSynth and ffmpeg produce
the retained audio stems and mixes.

## Validation

Run the Python tests from the repository root:

```bash
pytest -q -n 0
```

This checks the current shared engine, voicers, and corpus smoke tests.

Validate extracted tables during extraction. The extractor checks that:

- observed states have level-0 entries;
- trie nodes have non-empty marginal distributions;
- malformed records are reported.

Use a fixed seed when comparing changes to generation or voicing behavior.

## Known limits and next steps

The project has these known limits:

- The Java renderer remains a proof of concept and is not invoked by
  `render.py`.
- Melody tracks are opt-in; use `--melody-condition naturalistic` to emit
  synchronized V2 melody output and provenance.
- Statistical distribution targets need larger calibration reports.
- Extended guitar shapes use an explicit programmatic derivation because
  complete canonical coverage is not available in the reviewed sources.
- A narrow pop-synth context can still fail on a dense major seventh,
  ninth, sharp-eleventh chord.
- In `extract_distributions.py`, Harte chord parsing maps standalone 6ths
  (`6`, `maj6`, `min6`) to `seventh="bb7"`, and drops conflicting tokens when
  both a 6th and 7th occur in the same chord (e.g. `6(7)` or `7(6)`). Future
  revisions should parse these into both a 7th (`seventh`) and a 13th
  (`thirteenth`), allowing both degrees to populate the training distributions
  and be sampled together during generation without competing for the same
  7th slot.

Future work includes richer room, EQ, dynamics, and audio QA profiles.
