# Probabilistic composition generator

## Purpose

This project creates symbolic chord progressions for chord-recognition data.

The current pipeline has these Python stages:

1. `extract_distributions.py` learns count tables from normalized JAMS files.
2. `chord_gen.py` samples chord-event sequences from those tables and assigns
   genre-specific durations.
3. `chord_module.py` selects a genre-compatible voicer and renders pad chords.
4. `bass_module.py` renders the generated bass notes.
5. `render.py` combines the tracks into synchronized JFugue score text.

The voicing stage is documented in [`voicing/README.md`](voicing/README.md).
It converts chord events into MIDI pitches after chord generation.

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
chord_module.py + bass_module.py
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

`render.py` combines two tracks for each song:

- `chord_module.py` selects one piano, guitar, or synth policy for the song
  genre and renders voiced block chords;
- `bass_module.py` renders the generated bass pitch in a low register.

The output uses `START_SONG_N` and `END_SONG` markers. Both tracks use the
chord duration tokens, so they remain synchronized. `--mode arpeggios` is
part of the interface, but currently reports that arpeggio rendering is not
implemented.

The chord track is written to JFugue voice `V0` and the bass track to `V1`.
Each note in a block chord receives the duration token. This keeps all chord
tones sustained for the complete chord duration and prevents the bass from
playing after the chord track.

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
- selects a piano, guitar, or synth voicer by genre;
- renders pad chords and bass notes;
- preserves each chord's duration token in both tracks;
- writes `START_SONG_N` and `END_SONG` blocks.

Arpeggio mode is exposed by the interface but is not implemented yet.

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
| `chord_gen.py` | Sample timed symbolic chord events |
| `chord_module.py` | Select a voicer and render pad chord tracks |
| `bass_module.py` | Render bass tracks |
| `render.py` | Combine tracks into JFugue score text |
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

- The Java renderer remains a proof of concept and is not invoked by
  `render.py`.
- Arpeggio rendering is not implemented.
- Statistical distribution targets need larger calibration reports.
- Extended guitar shapes use an explicit programmatic derivation because
  complete canonical coverage is not available in the reviewed sources.
- A narrow pop-synth context can still fail on a dense major seventh,
  ninth, sharp-eleventh chord.

The next implementation step is to invoke the Java renderer from the score
pipeline, if a single-command MIDI workflow is required.
