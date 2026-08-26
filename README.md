# Probabilistic composition generator

## Purpose

This project creates symbolic chord progressions for chord-recognition data.

The current pipeline has two Python stages:

1. `extract_distributions.py` learns count tables from normalized JAMS files.
2. `chord_gen.py` samples chord-event sequences from those tables.

The voicing stage is documented in [`voicing/README.md`](voicing/README.md).
It converts chord events into MIDI pitches after chord generation.

JFugue rendering is the next integration stage. A minimally working proof of
concept is available in `HumanizedMidiRenderer.java`. Older rendering code is
available in `old_src/`.

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
        +--> chord-event JSON files
        +--> generated score input
        |
        v
voicing/ engine
        |
        +--> voiced MIDI chord data
        |
        v
JFugue / HumanizedMidiRenderer
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
4. Write the resulting chord events.

The generator does not choose MIDI octaves or instrument shapes. Those tasks
belong to the voicing engine.

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

## JFugue rendering plan

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

`HumanizedMidiRenderer.java` currently provides a proof of concept. It:

- reads JFugue pattern text;
- creates a MIDI sequence;
- applies timing humanization;
- groups notes that belong to one chord;
- applies velocity variation;
- applies channel timing bias;
- preserves note durations;
- resolves retrigger overlap;
- writes MIDI files.

The Java renderer is not yet the integrated output stage of the Python
generator. The integration must define:

1. the Python-to-JFugue event format;
2. channel assignments;
3. tempo and duration mapping;
4. voicer-to-instrument mapping;
5. output naming and metadata;
6. error handling for invalid patterns.

Do not treat a JFugue string as the source of chord semantics. The structured
chord event and voiced MIDI data remain the source records.

## Current output boundaries

The current responsibilities are:

| Component | Responsibility |
|---|---|
| `extract_distributions.py` | Learn count tables |
| `chord_gen.py` | Sample symbolic chord events |
| `voicing/` | Select and realize MIDI voicings |
| `HumanizedMidiRenderer.java` | Prototype JFugue-to-MIDI rendering |
| `old_src/` | Historical implementation and reference material |

The current Python chord generator does not produce final audio.

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

- The JFugue renderer is a proof of concept, not the final integrated stage.
- The Python-to-JFugue adapter is not yet defined.
- Statistical distribution targets need larger calibration reports.
- Extended guitar shapes use an explicit programmatic derivation because
  complete canonical coverage is not available in the reviewed sources.
- A narrow pop-synth context can still fail on a dense major seventh,
  ninth, sharp-eleventh chord.

The next implementation step is to connect voiced chord events to a stable
JFugue pattern format and then pass that format to
`HumanizedMidiRenderer`.
