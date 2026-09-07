# Mixing and Sound-Design Report

## Scope and entry points

The repository has two distinct stages:

1. The Java generator creates an arrangement as JFugue patterns and exports
   MIDI files. Its relevant entry points are
   [`ArtificialSongGenerator`](ArtificialSongGenerator/src/main/ArtificialSongGenerator.java),
   [`SongPart`](ArtificialSongGenerator/src/parts/SongPart.java), and
   [`SongPartElement`](ArtificialSongGenerator/src/parts/SongPartElement.java).
2. The `Sampling/` workflow renders those MIDI files through a manually
   prepared Ardour or Mixbus session. The relevant assets are
   [`mixbus_roboter.lua`](Sampling/mixbus_roboter.lua),
   [`mixbus_roboter - win10.lua`](Sampling/mixbus_roboter%20-%20win10.lua),
   and [`generatemix`](Sampling/generatemix).

The generator itself does not synthesize, sample, process, balance, pan, or
master audio. It creates the musical material, selects General MIDI programs
for the demo MIDI, and records a separate sampler identifier for the later
DAW stage. The actual sampled timbres, effects, level balances, and stereo
placement are therefore properties of the prepared DAW session rather than
of the Java source.

## At-a-glance signal-flow comparison

| Stage | Input | Output | How parts are combined | Sound design applied in source |
| --- | --- | --- | --- | --- |
| Composition | Randomized song parts, chords, and configuration | JFugue `Pattern` objects | Demo pattern overlays all enabled roles on separate MIDI voices | Instrument program and tempo markers only |
| MIDI export | Demo pattern and role/instrument patterns | `<title>_Demo.mid`, `<title>_Drums.mid`, and `<title>_<Instrument>.mid` | Individual instrument files concatenate material across the song structure | None |
| DAW stem rendering | One instrument MIDI file and its matching named DAW track | One exported FLAC stem per MIDI file | A track is soloed and rendered by itself | Whatever sampler, processing, routing, and automation the DAW session already contains |
| Offline mix assembly | Rendered compressed stems | `<id>_mix.wav` and `<id>_mix.mp3` | SoX sums the stems, then applies normalization | `gain -n` peak normalization on the summed WAV only |

## Arrangement and MIDI mixing

### Part roles and shared timing

Each `SongPart` first chooses one key, tempo, bar length, and chord
progression. It can then independently add the following roles:

| Role | Generator | Default inclusion probability | Musical contribution |
| --- | --- | ---: | --- |
| Melody | `MelodyBow` | 0.9 | A ranged, mostly eighth-note diatonic line with rests, copied phrases, and a final cadence |
| Pad | `ChordPadsRanged` | 0.8 | Simultaneous chord tones sustaining for each source-chord duration |
| Arpeggio | `ChordArpeggios` | 0.5 | Repeated eighth-note chord tones following one locally randomized three-note style |
| Bass | `BassLine` | 0.9 | The root of each chord at octave 3, retaining the source chord duration |
| Drums | `RhythmSimpleGrooves` | 0.8 | Three rhythm layers: bass drum, snare/toms, and cymbal |

Every enabled role in a part uses that part's timing. Pads, arpeggios, and
bass share the same chord array; melody receives the same chord array but
uses its final chord as its principal tonal reference. The arrangement
therefore achieves basic vertical coherence before any later mix operation:
bass and harmony change together, while the melody and drums provide motion
above that shared grid.

The main generator creates a small pool of distinct parts (three by default)
and forms the final song by reusing them. Repeating a part repeats its cached
melody, harmony, bass, drum groove, instrument choices, key, and tempo
exactly. This is arrangement-level repetition, not a DAW-loop or
post-processing operation.

### Demo MIDI voice layout

`SongPart.getPattern()` produces the full demonstration arrangement. It walks
the enabled non-drum elements in construction order, assigns each to the next
available MIDI voice, and always reserves voice `9` for drums. In the usual
case, the order is:

```text
voice 0: melody
voice 1: pad
voice 2: arpeggio
voice 3: bass
voice 9: drums
```

That order is conditional, not fixed: disabled earlier roles shift later
roles down to the next available voice. A missing melodic/harmonic role is
represented by a silent whole-note-rest pattern in the demo so that the
remaining role-to-voice layout stays stable for that part. Drums use
JFugue's rhythm voice `9`, the General MIDI percussion channel.

The demo is an additive MIDI arrangement: events from the role voices occur
at the same times and are rendered by the MIDI player's selected programs.
It does not contain volume, pan, expression, sustain-pedal, effects-send, or
bus-routing messages. Apart from the General MIDI instrument setting and
tempo, there is no source-level mix automation.

### Standalone MIDI stems

`ArtificialSongGenerator.main()` also builds a drum pattern and one pattern
for every `Instrument` object in the current instrument pool. For every
position in the song structure, it appends:

- the full multivoice `SongPart` pattern to the demo pattern;
- the part's real or silent drum rhythm to the drum pattern; and
- the matching role pattern, or a silent replacement, to each instrument
  pattern.

This produces files named:

```text
<title>_Demo.mid
<title>_Drums.mid
<title>_<Instrument-name>.mid
```

The instrument files are stems by configured instrument name, not by musical
role. In particular, pads and arpeggios select from the same
`CHORD_INSTRUMENTS` configuration pool. If the same configured instrument is
used for a pad in one part and an arpeggio in another, its standalone MIDI
file concatenates both appearances and does not retain a pad/arpeggio track
identity. The demo MIDI retains their separation through the voices assigned
while that part is rendered; segment annotations retain the generator class
names.

Within one part, `SongPart.getPattern(Instrument)` supports only one matching
element. If two elements share an instrument in the same part, the code logs
an error and terminates rather than mixing both into that instrument stem.
This prevents an ambiguous single-stem export, but it also means this case is
not a supported layering mechanism.

## Timbre selection before audio rendering

### Instrument metadata

An instrument configuration entry has this effective form:

```text
name [lowest MIDI note, highest MIDI note, sampler name, demo MIDI name]
```

For example, `Piano [21,108,The_Giant_soft]` gives the role a stable name,
its playable MIDI range, and a downstream sampler preset identifier. When a
fourth value is present, it overrides the General MIDI program used by the
demo MIDI.

The Java renderer uses only `name` and `demo MIDI name` to write MIDI. It
retains `sampler name` on the `Instrument` object but never consumes it when
creating a pattern, writing a MIDI file, or playing the demo. The sampler
field is therefore an integration contract for the external sampling session,
not an instruction that causes the Java process to load a sample library.

The configured pitch limits provide another pre-rendering form of sound
design: melody generators transpose their output by octaves to fit the
instrument's range, pads fold chord tones into a selected one-octave window,
and bass is fixed to octave 3. This avoids obviously implausible register
assignments, although it is not audio processing.

### Source-level variation that affects later sound

The generator changes the density and articulation implied by the MIDI, which
in turn changes how a sampler is excited:

| Part | Pre-rendering design choices |
| --- | --- |
| Melody | Eighth-note raster, probabilistic rests, occasional quarter/half-note bindings, phrase-prefix copying, cadence, and octave range correction |
| Pads | Simultaneous triad voicings with whole- or half-note sustains in a fixed selected register |
| Arpeggios | One randomized eight-position pattern of chord-member indices, emitted as eighth notes and reused across the part |
| Bass | Long root notes at each chord change |
| Drums | Randomly selected kit sounds, groove patterns, off-sixteenth snare/tom hits, and optional final fills/crash symbols |

These choices produce different event densities and note durations, but the
source does not set MIDI velocity. JFugue defaults and the DAW/sampler's
response determine attack level and dynamic behavior unless the prepared DAW
session adds its own interpretation.

## DAW rendering and augmentation beyond MIDI generation

### Per-instrument rendering workflow

The Mixbus automation script operates on one MIDI instrument file at a time.
For each configured name in its `instrumentTable` and each song ID, its
`mixdown()` function:

1. Looks for `<midi-directory>/<id>_<instrument>.mid`; missing roles are
   skipped.
2. Skips a render when the target
   `<flac-multitracks>/<id>_<instrument>.flac` already exists.
3. Finds the DAW route whose name exactly matches the instrument name, clears
   existing regions, unsolos/unmutes all routes, and solos that route.
4. Imports the MIDI file into the selected session, using the MIDI tempo map.
5. Sets the session-range start/end from the imported region, triggers the
   DAW's audio-export action through GUI automation, and renames the exported
   `session.flac` to the stem's target name.

The script does **not** construct sampler plugins, map the `sampler` metadata
to a plugin preset, alter plugin parameters, set faders, set pan positions,
or create buses. Those decisions must already exist in the session's route
named for each instrument. Its isolation-by-solo design means each resulting
FLAC is a rendered individual stem, potentially including that route's
instrument and insert effects, but not a sum of all song parts.

The Windows variant expresses the same route/solo/import/export model with
Windows paths and an `xdotool`-for-Windows command to confirm the export
dialog. The Linux-oriented script contains example snippets and uses
`xdotool key Return` for the same export-dialog automation. Both are
partially automated workflows that depend on a live, correctly configured
DAW project.

### Offline sum and normalization

`Sampling/generatemix` is a separate shell workflow for IDs `01` through
`10`. It first decodes matching `<id>_*.mp3` files to WAV, then runs:

```text
sox --combine mix <id>_*.mp3.wav <id>_mix.wav gain -n
```

SoX adds all matching decoded stems and uses `gain -n` to normalize the
result. It then measures the mix duration, pads every individual decoded
stem with trailing silence to that duration, encodes the padded stem as an
MP3, encodes the normalized mix as `<id>_mix.mp3`, and moves temporary WAV
files to the system trash command.

This is the only explicit final-mix processing in the repository. It is a
single normalization step after a straight sum; the script applies no
per-track gain compensation, panning, EQ, compression, limiting, reverb,
delay, sidechain processing, fades, crossfades, or mastering chain. Balance
and effects must consequently be supplied during the DAW rendering stage if
they are desired.

## Metadata and traceability

The segment ARFF file records the start time, part mark, tempo, key,
instrument names, and generator class names for each occurrence. It can
identify a `MelodyBow`, `ChordPadsRanged`, `ChordArpeggios`, `BassLine`, or
`RhythmSimpleGrooves` contribution at a segment boundary.

The onset ARFF file is generated by reading the exported MIDI files. It
records active MIDI pitches by configured instrument name at each onset time.
It does not record DAW sampler choice, plugin state, effects, mix levels,
panning, or a pad-versus-arpeggio role label when both share one instrument
stem. No annotation is generated for rendered FLAC/MP3 processing.

## Current implementation observations and limitations

| Area | Current behavior | Consequence |
| --- | --- | --- |
| Composition vs. audio | Java generates and exports MIDI only. | Sample libraries and all audio treatment are external to the generator. |
| Demo mix controls | The demo contains program, tempo, voice, note, and rest information but no mix-control messages. | Playback balance depends on the MIDI renderer/soundfont rather than an authored source mix. |
| DAW configuration | The robot script selects named routes but never creates or configures them. | Route names, samplers, effects, faders, and routing must be prepared consistently outside versioned code. |
| Stem identity | Standalone files are keyed by instrument name. | Musical-role identity can be lost when pads and arpeggios use the same instrument across different parts. |
| DAW render mode | The script solos one route before export. | It produces isolated processed stems, not a complete DAW mix; the later SoX step is responsible for summing stems. |
| Final processing | `generatemix` applies `gain -n` after SoX mixing. | The final level is normalized, but no source-controlled artistic balance or mastering processing is present. |
| Dynamic control | No explicit velocity generation is visible in the role generators. | Dynamics are left to JFugue defaults and the sampler/session rather than being composed per note. |
| Automation robustness | Export completion is confirmed with an external delayed GUI keystroke and the script renames a fixed `session.flac` export name. | Rendering depends on DAW UI state, export settings, timing, and filesystem paths; it is not a headless reproducible audio-render pipeline. |

## Summary

The repository's musical "mix" is primarily an arrangement: melody,
simultaneous pads, arpeggios, root bass, and three-layer drums are aligned in
shared song parts and written to MIDI. The MIDI demo overlays those roles on
separate voices, while the standalone outputs organize material by
instrument name. Timbre is planned through configured sampler identifiers
and pitch ranges, but it is not rendered or augmented by Java.

Audio sound design begins only after MIDI export. A prebuilt Ardour/Mixbus
session is expected to turn each named MIDI stem into a processed FLAC using
the route's existing instrument and effects configuration. The checked-in
offline mixer then performs a plain stem sum with final peak normalization.
Accordingly, the repository defines musical content and the automation
mechanics around audio rendering, but it does not version or program the
actual sampler patches, processing chains, spatial mix, or mastering
decisions.
