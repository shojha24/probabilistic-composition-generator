# Probabilistic composition generator

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

Control the per-song probability of audible percussion with
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

The value must be between 0 and 100. This is a seeded probability evaluated
once per song, not an exact corpus quota. A song that fails the draw still
gets a synchronized silent `V9` track. The requested percentage, probability,
and realized count are recorded in the manifest. The Python APIs expose the
same control as `percussion_percent`.

Directory rendering requires an explicit integer `--seed` and accepts one or
more `--in-dir` values. Each directory's source files are validated and
sorted by numeric ID before rendering, so `song_10.json` follows
`song_9.json`; input directories are processed in command-line order.
Malformed filenames, duplicate numeric IDs within one directory, and
duplicate input directories fail the render.

`render.py` combines three tracks for each song:

- `chord_module.py` tries the renderer's six-voicer order, prioritizing the
  song's genre, and renders either voiced block chords or profile-driven
  arpeggios from those same selected voicings;
- `bass_module.py` renders the generated bass pitch in a low register.
- `percussion_module.py` repeats a seeded kick, snare, and cymbal groove on
  voice `V9` (the General MIDI percussion channel). Each song has a configurable
  chance of audible percussion, defaulting to 70%; the remaining songs retain
  a synchronized silent `V9` track. When percussion is audible, songs at
  60–120 BPM have a 20% chance of a cut-time (double-time) feel, while songs at
  121–180 BPM have a 20% chance of a half-time feel. The selected feel and
  inclusion result are recorded in the manifest.

The output uses `START_SONG_N` and `END_SONG` markers. All three tracks use
the chord duration timeline, so they remain synchronized. In arpeggio mode,
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

The planned rendering flow is:

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

The renderer writes one `.mid` file per `START_SONG_N` block. The Java step
is implemented, but it is not automatically invoked by `render.py`.

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
| `render.py` | Combine tracks into JFugue score text and manifests |
| `eda/validate_rendered_corpus.py` | Validate manifest-paired rendered corpora |
| `voicing/` | Select and realize MIDI voicings |
| `HumanizedMidiRenderer.java` | Convert JFugue text to humanized MIDI |
| `old_src/` | Historical implementation and reference material |

The Python pipeline produces score text, not final audio. Java converts the
score text to MIDI; audio synthesis is a separate downstream step.

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

- NO_CHORD events are not currently generated.
- The Java renderer remains a proof of concept and is not invoked by
  `render.py`.
- Melody tracks are yet to be generated.
- Statistical distribution targets need larger calibration reports.
- Extended guitar shapes use an explicit programmatic derivation because
  complete canonical coverage is not available in the reviewed sources.
- A narrow pop-synth context can still fail on a dense major seventh,
  ninth, sharp-eleventh chord.

The next implementation step is to invoke the Java renderer from the score
pipeline, if a single-command MIDI workflow is required.
