# Melody Generation and Rendering Report

## Scope and entry points

The melody feature is represented by two `SongPartElement` implementations:

- [`MelodyBow`](ArtificialSongGenerator/src/parts/MelodyBow.java) is the
  implementation used by the current song generator. It creates a diatonic
  melody anchored to the final chord's root or fifth, with a deliberately
  shaped ending.
- [`MelodySimple`](ArtificialSongGenerator/src/parts/MelodySimple.java) is a
  separate, simpler random melody implementation. It remains constructible
  through the common element API, but the current [`SongPart`](ArtificialSongGenerator/src/parts/SongPart.java)
  constructor does not instantiate it.

The surrounding lifecycle is shared. [`Config`](ArtificialSongGenerator/src/main/Config.java)
selects whether a part has a melody and which instrument it uses;
`ChordProgressionFactory` supplies the harmonic and timing context;
[`SongPartElement`](ArtificialSongGenerator/src/parts/SongPartElement.java)
turns a generated Staccato string into a JFugue `Pattern`; and
[`ArtificialSongGenerator`](ArtificialSongGenerator/src/main/ArtificialSongGenerator.java)
assembles song parts, writes MIDI files, and creates segment and onset
annotations.

This report describes the behavior of the current source, including the
differences between the active and non-active melody implementations and the
places where the implementation is less general than its constructor
signature suggests.

## At-a-glance comparison

| Concern | `MelodyBow` | `MelodySimple` |
| --- | --- | --- |
| Used by `SongPart` | Yes | No |
| Musical model | Diatonic relation values around the final chord's root or fifth | Independent random scale-letter selection |
| Scale letters | `C D E F G A B` | `C D E F G A`; `B` is omitted |
| Chord use | Builds a chord-time raster and uses the final chord to establish the reference tone; does not target every chord during note selection | Ignores the supplied chords |
| Initial note field | Random relation within a 3- or 4-step hull plus an offset | No persistent contour; a note is selected afresh for each event |
| Structure | Off-beat thinning, copied prefixes, and a forced final relation of zero for parts longer than one bar | No phrase copying or cadence rule |
| Durations | Eighth notes, with probabilistic quarter and half notes when following slots are rests | Eighth or quarter notes; rests may be eighth, quarter, or half |
| Key marker | Emits `Key:` after converting a minor key to its relative major | Same minor-to-major conversion |
| Explicit octave | Yes; normally around octave 5, shifted for the instrument range | No; relies on JFugue's default octave/key handling |
| Instrument range | Checked and corrected by octave shifts, with one semitone of headroom from configured endpoints | Not checked |
| Caching | `getPattern()` caches the generated music string per element | Same inherited behavior |

## End-to-end generation pipeline

1. `ArtificialSongGenerator.main()` loads the optional configuration file and
   then calls `Config.loadDefaults()`. The defaults include
   `melody-enabled = 0.9`, three distinct song-part objects, allowed part
   lengths of `4`, `6`, `8`, `12`, or `16` bars, and a tempo range of `60` to
   `180` BPM.
2. Each `SongPart` draws its key, tempo, and length and creates one shared
   `Chord[]` through `ChordProgressionFactory.makeChords(length, key)`.
   That same key, tempo, length, and chord array are passed to every element
   in the part.
3. The part makes one Bernoulli draw against `MELODY_ENABLED`. If it succeeds,
   it selects an instrument with `Config.randomMelodyInstrument()` and adds
   one `MelodyBow` instance. Melody selection is independent of the chord,
   bass, arpeggio, and drum gates. The constructor retries the complete
   selection pass if a part would otherwise contain no element and no drums,
   so the final distribution is conditioned on every part being non-empty.
4. The generator first creates the configured number of distinct
   `SongPart` objects. It then forms a longer `songStructure` by reusing those
   objects. A repeated part therefore reuses its key, tempo, chords, selected
   instrument, and cached melody rather than regenerating the material.
5. On the first `getPattern()` call, `SongPartElement` calls the element's
   `makeMusic()` method. The returned JFugue Staccato string is cached. Each
   later call creates a new `Pattern` from that same string and applies the
   element's MIDI instrument and tempo.
6. For the demo pattern, `SongPart.getPattern()` assigns voices in element
   construction order and reserves voice `9` for drums. Because melody is
   added before the other melodic/harmonic elements, an enabled melody
   normally receives the first available voice.
7. The main generator concatenates the demo pattern, a drum track, and one
   track per used `Instrument`. It writes MIDI files and then analyzes the
   files for onset annotations.

The Java generator renders MIDI, not sampled audio. The optional `--play`
flag sends the demo JFugue pattern to a `Player`; the `sampler` metadata in an
instrument configuration is retained for downstream sampling workflows and
is not used by the Java MIDI renderer itself.

## Shared timing, key, and chord contract

### Part duration

The generator assumes a 4/4 bar. A part with `length = L` occupies:

```text
L bars * 4 quarter notes per bar * 2 eighth-note slots per quarter = 8L slots
```

`MelodyBow` and `MelodySimple` both fill this same `8L`-slot time span. Their
Staccato events may have different written durations, but the combination of
notes and rests always consumes the complete part.

`SongPart.getLengthInSeconds()` uses the same 4/4 assumption:

```text
length * 4 * 60 / tempo
```

The random song structure uses that value to reach the configured minimum and
maximum song lengths.

### Chord progression

`ChordProgressionFactory` constructs a simple progression from the scale
degrees:

```text
I ii iii IV V vi
```

The seventh degree is omitted. The first chord is chosen from the tonic,
subdominant, or dominant positions, the final chord returns to the tonic-like
position, and middle bars are filled with random scale-degree chords. A
single class-level probability, randomly chosen between `0.1` and `0.4`, can
turn a middle bar into two half-duration chords. Consecutive multi-chord bars
are prevented.

For a major key, the factory builds the progression directly. For a minor
key, it changes the key to its relative major for JFugue chord construction
and uses degree `vi` as the tonic-like position. The resulting `Chord[]`
contains explicit whole (`w`) or half (`h`) durations in the normal factory
path. The sum of those durations equals the requested number of bars.

`MelodyBow` depends on this duration convention when it creates its chord
raster. `MelodySimple` does not inspect the chord array at all.

### Key normalization

Both melody implementations call
`JFugueExpansion.minToMajKey()` before writing their Staccato key marker.
For a minor input, this emits the relative-major key signature. The original
`SongPart.key` is retained separately and is the value written to the segment
annotation file, so the key marker inside a melody pattern and the key field
in the segment metadata can differ for minor parts.

## Common melody element interface

The effective construction and rendering interface is:

```java
new MelodyBow(Instrument instrument, int tempo, int length,
              Key key, Chord[] chords);

new MelodySimple(Instrument instrument, int tempo, int length,
                 Key key, Chord[] chords);

String makeMusic();
Pattern getPattern();
```

`SongPartElement` stores the instrument, tempo, length, key, and chord array
as final fields. It rejects a null instrument but does not validate the key,
chord array, chord count, durations, tempo, or part length.

`makeMusic()` returns a raw JFugue music string. The inherited
`getPattern()` method then:

1. Calls `makeMusic()` only when the element's cached `musicString` is empty.
2. Creates a `Pattern` from the cached string.
3. Applies `Instrument.getMidiString()` as the JFugue program/instrument.
4. Applies the element tempo.

The method returns a new `Pattern` on each call, but the musical string is
stable after its first request. Calling `makeMusic()` directly bypasses that
cache and can produce a new random result each time.

## `MelodyBow`: active melody generator

### Internal representation

`MelodyBow` separates composition decisions from Staccato serialization:

- `melodyRaster` is an `Integer[]` with `8L` entries.
- A null entry means an eighth-note rest.
- A non-null entry is a relative position in a diatonic scale, not a MIDI
  semitone interval.
- `chordRaster` is a `Chord[]` with `8L` entries used to associate source
  chords with eighth-note slots.

The relation-to-tone conversion is handled by
`getMelodyToneByRelation(root, octave, relation)`. It walks the ordered
letter sequence `C D E F G A B`, incrementing or decrementing the octave when
it crosses the end of that sequence. It returns an explicit tone such as
`E5` or `A4`.

### 1. Building the chord raster

The source chord array is expanded into the eighth-note grid before the
melody contour is generated:

- A chord whose first note has a duration ending in `w` occupies eight
  raster slots.
- A chord whose first note has a duration ending in `h` occupies four
  raster slots.
- No other duration suffix is recognized.

The raster is filled in source order. In the normal factory path, the final
whole-note chord begins at slot `(L - 1) * 8`, which is also where the
cadence lookup later reads the final chord.

Although this looks like a per-slot harmonic context, the current
implementation does not use `chordRaster[i]` while selecting ordinary
melody notes. It uses only the final chord entry to establish the reference
scale letter. The TODO in the source for avoiding chord notes and
non-chord tones is not implemented.

### 2. Initial random contour

At each class load, `MelodyBow` chooses four static probabilities with the
shared `SecureRandom` wrapper:

```text
PROB_QuarterBind  in [0.1, 0.9)
PROB_Rest         in [0.1, 0.4)
PROB_QuarterNote  in [0.1, 0.8)
PROB_HalfNote     = PROB_QuarterNote + a value in [-0.2, 0.2)
```

These values are shared by every `MelodyBow` element in the JVM. They are not
configuration keys.

For each call to `makeMusic()`, two more values define the relation hull:

```java
int hullInterval = Random.rangeInt(3, 5); // 3 or 4
int hullOffset = Random.rangeInt(0, 2);   // 0 or 1
```

Each raster slot is then initialized independently:

- With `PROB_Rest`, the slot is null.
- Otherwise, its relation is drawn with the exclusive-upper-bound call
  `Random.rangeInt(-hullInterval + hullOffset,
                   hullInterval + hullOffset)`.

The possible relation intervals are therefore:

| `hullInterval` | `hullOffset` | Possible relation values |
| ---: | ---: | --- |
| 3 | 0 | `-3` through `2` |
| 3 | 1 | `-2` through `3` |
| 4 | 0 | `-4` through `3` |
| 4 | 1 | `-3` through `4` |

The relation is measured in diatonic letter steps from the later-selected
reference tone. It is not a chromatic interval and does not encode an
accidental.

### 3. Off-beat thinning

The next pass is intended to smooth the contour. For every odd-indexed
eighth-note slot, an independent `PROB_QuarterBind` draw can replace the
entry with null. This removes off-beat notes but never directly removes an
even-indexed downbeat note. The draw is still made for an already-null slot,
so the probability affects only non-null entries in practice.

This is the only smoothing pass. There is no interpolation, step-size
limiter, curve fitting, or explicit preference for chord tones.

### 4. Prefix copying

Two independent copy operations introduce a coarse phrase structure:

1. A random prefix of the raster is copied to the position one quarter of the
   way through the raster.
2. Another random prefix of the original raster is copied to the halfway
   position.

For a part of `L` bars, the destination offsets are `2L` and `4L` eighth-note
slots. The loop also limits the copied source to the first `L` slots, even
though the random upper bound is larger. Copied values are exact copies:
they are not transposed, inverted, or rhythmically transformed. The source
comment mentions possible transposition, but no transposition is performed by
the current code.

The copies happen before the ending is forced, so a later cadence can overwrite
anything in the last bar's first slot.

### 5. Forced ending

For parts longer than one bar, slot `(L - 1) * 8`, the first eighth-note slot
of the final bar, is set to relation `0`. Every later raster slot is set to
null. The serialized ending is therefore one final note followed by rests:

```text
... [final relation-zero note] Ri Ri Ri Ri Ri Ri Ri
```

The final note is not necessarily the literal tonic of the input key. Its
absolute letter and octave are determined by the reference-tone calculation
below. A one-bar part does not receive this special ending treatment.

### 6. Reference tone and octave

The reference tone is chosen from the chord at the start of the final bar:

```java
chordRaster[(getLength() - 1) * 8]
```

For a normal triad, there is a 75% chance of using `notes[0]`, usually the
root, and a 25% chance of using `notes[2]`, usually the fifth. The code keeps
only the first character of that note's tone string and maps it through
`DIATONIC_MAP`:

```text
C -> 0, D -> 1, E -> 2, F -> 3,
G -> 4, A -> 5, B -> 6
```

Consequences of this implementation are:

- Accidentals in the final chord are ignored while selecting the reference
  scale letter.
- A custom chord shape may not have a root/fifth at indexes `0` and `2`.
- The source chord's absolute octave is not used as the melody octave.
- The initial reference octave is always `5`.

The selected hull is then fitted to the configured instrument range. The
reference octave is raised until the lower hull is not below the instrument's
minimum, and lowered until the upper hull is not above the instrument's
maximum. Both checks use a one-semitone margin:

```text
lowest melody value - 1 >= instrument minimum
highest melody value + 1 <= instrument maximum
```

The temporary lower and upper notes used for this alignment are not emitted
directly. They establish the reference octave used to translate each raster
relation.

### 7. Converting the raster to Staccato

The output starts with a JFugue key marker:

```text
Key:<normalized-key>
```

Each raster entry then contributes one or more eighth-note slots. Every event
is preceded by a space, so the generated string is a sequence of ordinary
Staccato tokens:

| Raster condition | Output | Slots consumed |
| --- | --- | ---: |
| Null | `Ri` | 1 |
| Note at an even index, next three slots null, and half-note draw succeeds | `<tone>h` | 4 |
| Note at an even index, next slot null, and quarter-note draw succeeds | `<tone>q` | 2 |
| Any other note | `<tone>i` | 1 |

Half-note testing happens before quarter-note testing. Both duration choices
are available only on even-indexed slots, which represent downbeats in the
eighth-note raster. The source nulls are skipped when a longer note is chosen,
so the output remains exactly `8L` eighth-note slots long. Rests are always
serialized as `Ri`; adjacent rests are not combined into longer rest tokens.

All emitted melody notes include an explicit octave. A conceptual result
might look like:

```text
Key:Cmaj A5i Ri C5q G5h Ri Ri E5i ... F5h Ri Ri Ri
```

The exact notes, rests, copied phrases, and durations are random. The example
illustrates syntax and timing, not a deterministic output.

### 8. Per-note range correction

After a relation is translated into a `Note`, the note is checked again
against the instrument range. If it is too low, twelve semitones are added
until it passes; if it is too high, twelve semitones are subtracted until it
passes. This correction is independent for each note, so octave changes can
interrupt the intended contour or make neighboring scale relations sound
less smooth.

`MelodyBow` logs a fine-level message when it makes one of these corrections.
It does not perform a final assertion that the entire generated string is
within range after the two independent hull-alignment passes.

## `MelodySimple`: the non-active random implementation

`MelodySimple` uses the same `SongPartElement` constructor and inherited
rendering method, but its composition algorithm is independent of the chord
progression.

### Event generation

It creates a counter measured in eighth-note slots and repeatedly appends one
event until the part is full:

1. With one slot left, it forces an eighth-note rest (`Ri`).
2. With two slots left, it forces a quarter-note rest (`Rq`).
3. Otherwise, with a static `PROB_Rest` probability, it creates a rest:
   - a half rest (`Rw`) if at least four slots remain and the half-rest draw
     succeeds;
   - otherwise a quarter rest (`Rq`) if at least two slots remain and the
     quarter-rest draw succeeds;
   - otherwise an eighth rest (`Ri`).
4. If it does not create a rest, it selects a random letter from
   `C D E F G A`. With `PROB_EigthNote` it emits an eighth note; otherwise it
   emits a quarter note.

Its static probabilities are selected once when the class is initialized:

```text
PROB_Rest        in [0.1, 0.4)
PROB_HalfRest    in [0.4, 0.6)
PROB_QuarterRest in [0.7, 0.9)
PROB_EigthNote  in [0.1, 0.5)
```

The `currentNote` field is initialized randomly when an instance is created,
but every non-rest event overwrites it with a new random scale index. It
therefore does not provide continuity between notes.

### Output and limitations

`MelodySimple` also starts with a normalized `Key:` marker, but note tokens
are emitted without octave numbers, for example:

```text
Key:Cmaj Ei Ri Aq
```

The implementation does not use the supplied key to choose scale letters,
does not inspect `Chord[]`, does not force a final cadence, and does not check
instrument range. It is consequently a fully random, key-marker-only
alternative rather than a simplified version of the `MelodyBow` harmonic
model.

## Instrument selection and configuration

`SongPart` uses the `melody-instruments` configuration list. The default
entries are:

```text
Violin [55,96,Violins_SessionStringsPro]
Viola [48,84,Violas_SessionStringsPro]
Erhu [57,84,EthnoWorld_Erhu,Fiddle]
Jinghu [69,93,EthnoWorld_JinghuOperaViolin,Fiddle]
MorinKhuur [50,84,EthnoWorld_MorinKhuurViolin,Fiddle]
ElectricGuitarLead [40,79,ChrisHein????CleanGuitar+VSTAmp,Distortion_Guitar]
Trumpet [52,89,Trumpet1_SessionHornsPro]
Flugelhorn [52,83,Flugelhorn_SessionHornsPro,Trombone]
Trombone [40,72,TenorTrombone_SessionHornsPro]
Clarinet [50,87,Clarinet_Essential]
AltoSax [49,81,AltoSax_SessionHornsPro,Alto_Sax]
TenorSax [44,76,TenorSax_SessionHornsPro,Tenor_Sax]
Flute [60,96,Flute_Essential]
PanFlute [41,75,EthnoWorld_PanFlute,Pan_Flute]
Shakuhachi [57,88,EthnoWorld_Shakuhachi,Skakuhachi]
Fujara [36,96,EthnoWorld_Fujara,Blown_Bottle]
```

`Config.parseInstrument()` removes whitespace and interprets an entry as:

```text
name [lowest MIDI note, highest MIDI note, sampler name, demo MIDI name]
```

The first two attributes drive `MelodyBow` range correction. The sampler name
is stored on the pooled `Instrument` for external use. The fourth attribute,
when present, is the value passed to JFugue through
`Instrument.getMidiString()` by `SongPartElement.getPattern()`. If omitted,
the instrument name itself is used as the JFugue instrument string.

Instrument choice has two interacting modes:

- With instrument memoization enabled (the default), a position is normally
  reused and the `memoize-instruments-fuzziness` value (default `0.33`)
  occasionally sends selection through the non-memoized branch.
- With exploitation enabled (the default), that branch draws configured
  entries without replacement until the role-specific list is empty, then
  refills it.
- If exploitation is disabled, the branch selects directly from the full
  melody list.

The selected `Instrument` object is shared through the global instrument pool.
Its name identifies the standalone MIDI track, while its MIDI string identifies
the demo playback program.

## MIDI rendering and track assembly

### Demo pattern

`SongPart.getPattern()` creates a multi-voice demo pattern:

- The `SongPartElement` iterator is consumed in construction order. An enabled
  melody is therefore before pads, arpeggios, and bass.
- Voice `9` is reserved for drums. If drums are absent, a silent rhythm still
  occupies that position.
- Other elements are assigned the next available voice. There is no fixed
  melody channel; the melody's voice depends on which preceding roles exist.
- Missing element positions receive whole-note rests for the part.
- If more than the available voices are needed, the method warns that the
  demo MIDI is incomplete.

The melody `Pattern` already contains its tempo and instrument assignment when
it is inserted. `JFugueExpansion.repairMusicString()` then normalizes repeated
spaces and repairs the ordering of tempo and voice markers before the pattern
is written.

### Instrument tracks

For every pooled instrument, the main generator accumulates one pattern across
all entries in `songStructure`. `SongPart.getPattern(instrument)` returns the
first matching element's pattern, or a silent part-length pattern when that
instrument is absent. These track patterns intentionally do not use voices.

Files are written using the instrument name:

```text
<title>_<Instrument.getName()>.mid
```

There is no melody-specific suffix such as `<title>_Melody.mid`. A melody
played by `Violin` is therefore found in the `Violin` track, potentially
alongside any other role that uses the same instrument name in other parts.
Within one part, a second element using the same instrument is treated as
unsupported: the generator logs an error and exits.

`ArtificialSongGenerator.saveToMidi()` checks whether a pattern contains any
non-rest notes before saving. A completely silent melody track does not
produce a MIDI file and is not passed to the onset annotator.

### Optional playback and Staccato inspection

The command-line options relevant to melody rendering are:

- `--print-staccato` prints the assembled demo pattern, with line breaks near
  tempo markers.
- `--play` plays that demo pattern through JFugue's `Player` after file
  generation.

Neither option changes melody generation or bypasses the per-element cache.

## Annotation behavior

### Segment annotations

`ArffUtil.saveSongStructureToArff()` writes a segment row for every reused
`SongPart`, followed by an `end` row. Melody-bearing segments contain:

```text
Instruments: [<selected instrument name>, ...]
Generator:   [MelodyBow, ...]
```

If a caller explicitly constructs a `MelodySimple` element and inserts it
into a song part, its generator name would be `MelodySimple`; the normal
`SongPart` path records `MelodyBow`.

The segment file records the source `SongPart.key`, tempo, mark, and start
time. It does not record the melody raster, relation values, random
probabilities, phrase-copy offsets, or note durations. For minor parts, the
segment key is the original minor key even though the melody's Staccato
`Key:` marker contains the relative major key.

### Onset annotations

After MIDI files are saved, `OnsetAnnotator` reads note-on and note-off events
from every generated file. Its ARFF schema contains a column for every
configured melody, chord, bass, and drum instrument. At each onset time it
records active MIDI note numbers; a newly started note is marked with `+`.

This annotation is instrument-centric, not generator-centric. It preserves
the rendered pitch and timing of a melody, but does not say whether those
events came from `MelodyBow`, `MelodySimple`, or another element that shares
the same instrument track. A sustained note remains represented through its
active-note state until its MIDI note-off event.

## Randomness, caching, and reproducibility

All random draws use the static `SecureRandom` instance in
[`util.Random`](ArtificialSongGenerator/src/util/Random.java). There is no
seed or dependency-injection interface.

| Draw | Scope | When it occurs |
| --- | --- | --- |
| Melody enable gate | One `SongPart` construction attempt | Each element-selection pass |
| Melody instrument | `Config` role selector | When a melody element is constructed |
| `MelodyBow` probability constants | Shared by the class/JVM | Class initialization |
| Hull interval and offset | One `MelodyBow.makeMusic()` invocation | First `getPattern()` for that element in normal use |
| Raster rests and relations | One generated melody string | During `makeMusic()` |
| Duration and copy decisions | One generated melody string | During raster serialization |

Because `SongPartElement.getPattern()` caches `makeMusic()`, all occurrences of
the same `SongPart` in the final structure repeat the exact same melody.
Calling `makeMusic()` directly instead of `getPattern()` can reroll the
melody. Reproducing a song therefore requires matching the configuration,
object construction order, random stream, part reuse, and the class-load
timing of the static probability draws.

## Current implementation observations and edge cases

| Area | Current behavior | Consequence |
| --- | --- | --- |
| Active implementation | `SongPart` always constructs `MelodyBow` when the melody gate succeeds | `MelodySimple` is not part of ordinary command-line generation |
| Chord following | `MelodyBow` creates `chordRaster` but uses it only for the final reference tone | Ordinary notes are not selected as chord tones and can be non-chord tones; the generator follows a broad diatonic frame rather than the changing progression |
| Scale spelling | Relations walk natural letter names and reference selection uses only the first tone character | Accidentals are discarded during melody relation selection; the source key marker does not by itself add explicit accidentals to generated tone strings |
| Minor keys | Melody output normalizes a minor key to its relative major, while segment metadata keeps the original key | Pattern-level and annotation-level key strings can differ |
| Known ending issue | The source documents a TODO that an `F#maj` ending can produce `F` instead of the root | The final relation-zero note is not guaranteed to be the exact written tonic for every key |
| Chord input contract | `MelodyBow` assumes factory-style `w`/`h` durations and enough slots to fill the raster | Custom durations can leave null raster entries, overrun the raster, or cause a null dereference at final-chord lookup |
| Chord shape | The final reference uses `notes[0]` or `notes[2]` | The 75% root / 25% fifth interpretation is valid for the standard triads, not arbitrary chord arrays |
| Range fitting | Reference-hull and per-note octave loops are independent and use one semitone of headroom | Narrow or poorly aligned custom ranges can disrupt the contour and are not validated by a final assertion |
| Simple melody octave | `MelodySimple` emits notes without octave data and performs no range check | Actual pitch depends on JFugue defaults and may not fit the configured instrument |
| Duration probability | `PROB_HalfNote` is computed by adding a random value in `[-0.2, 0.2)` to `PROB_QuarterNote` | The effective probability can be below `0` or above `1`; `Random.nextBoolean()` then behaves as never or always true for those cases |
| Part reuse | Distinct parts are reused in the final song structure | Repeated segment marks repeat identical melody material instead of generating variations |
| Instrument identity | MIDI files are keyed by instrument name, not by generator role | A melody track does not carry a melody-specific file label; generator identity survives only in segment annotations |
| Input validation | The common base class validates only the instrument reference | Null keys/chords and malformed lengths or chord arrays can fail later inside a generator |
| Reproducibility | `SecureRandom` is global and unseeded | A configuration file alone cannot reproduce a particular melody |
