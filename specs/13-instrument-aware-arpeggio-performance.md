# Spec 13 - Instrument-Aware Arpeggio Performance

- **Status:** proposed implementation specification
- **Applies to:** `chord_module.py`, `instruments.py`, `render.py`,
  `HumanizedMidiRenderer.java`, rendered-corpus validation, tests, and
  documentation
- **Parent:** spec 12

## 1. Decision

Replace the fixed, uninterrupted sixteenth-note arpeggio serializer with an
instrument-aware performance scheduler. The scheduler is intentionally a
single-pass model: each selected voicing note is attacked once, optional
adjacent notes may share an onset, and every attack sustains to the source
chord boundary. Each arpeggiated attack must retain at least an eighth note
of sustain; if no supported grid can satisfy that requirement for the whole
voicing, the source event is rendered as a simultaneous pad chord instead.

The scheduler must preserve the selected `VoicedChord` sequence from spec 12.
It may change only:

- note onset spacing;
- traversal order;
- optional simultaneous attacks for adjacent traversal positions;
- attack velocity; and
- pattern changes at controlled phrase boundaries.

It must attack every MIDI value in the current event's
`VoicedChord.midi` exactly once, without adding a pitch absent from that list
or re-octaving a selected pitch. It must not change the selected voicer, bass,
or percussion output.

The revised pipeline is:

```text
ChordEvent labels
    -> existing Engine / selected policy
    -> VoicedChord sequence
    -> instrument performance profile
    -> timed arpeggio attacks
    -> JFugue score and MIDI rendering
```

This is a performance-layer enhancement, not a seventh voicer and not a new
harmonic generator.

## 2. Motivation

Spec 12 intentionally establishes a simple, deterministic baseline: one
selected chord tone on every sixteenth-note position. That implementation is
harmonically safe but has several audible limitations:

1. sixteenths remain mandatory even at 180 BPM;
2. every note ends when the next note begins, so acoustic instruments cannot
   ring naturally;
3. a new pattern phase is selected for every chord event;
4. all attacks have equal intended emphasis before generic humanization;
5. guitar voicings are traversed by sorted pitch rather than physical string;
6. a pattern can continue unchanged for an arbitrarily long phrase; and
7. strict cycles cannot express bass returns, top-note returns, or a lighter
   pass through dense voicings.

The selected voicings are already suitable source material. The missing
information belongs in a scheduler that understands tempo, meter position,
instrument family, and available physical provenance.

## 3. Invariants

### 3.1 Harmonic authority

For every playable source event:

```text
set(emitted MIDI pitches) <= set(VoicedChord.midi)
```

The subset relation is intentional. A short event or ornamented motif need not
attack every selected voice, but no unselected pitch is permitted.

`VoicedChord.midi`, roles, DCT, hands, shape ID, omissions, collision merges,
and diagnostics remain exactly those selected by the existing engine. Pad and
arpeggio modes must continue to select the same voicer and the same source
voicings for equivalent runs.

### 3.2 Timeline

- The first arpeggio onset for an event must occur at that event's start.
- No arpeggio onset may occur at or after the next source event's start.
- A sounding note may overlap a later arpeggio onset in the same event.
- A sounding note must end no later than the source event boundary.
- Every arpeggiated attack must have at least two sixteenth-note units of
  sustain before that boundary.
- If that minimum tail cannot be met for every selected voicing note, the
  event must use the pad fallback and emit no arpeggio attacks.
- A no-chord event must contain no V0 or V1 pitch and must reset all arpeggio
  phrase state.
- The V0, V1, and V9 tracks must retain the same total source timeline.

### 3.3 Track isolation

For equivalent pad and arpeggio runs, the following must remain byte-identical:

- V1 pitch/rest sequence;
- V1 program when it is not intentionally collapsed to a pad;
- V9 token sequence;
- percussion inclusion, kit, and feel; and
- selected voicer, source MIDI, roles, and voicing diagnostics.

Arpeggio performance random draws must use a deterministic child RNG and must
not perturb voicing, bass, percussion, or instrument-selection streams.

## 4. Performance profiles

### 4.1 Catalog contract

Extend `Instrument` with an optional arpeggio performance profile identifier:

```python
@dataclass(frozen=True)
class Instrument:
    name: str
    program: int
    roles: frozenset[str]
    arpeggio_profile: str | None = None
```

Every instrument carrying the `arpeggios` role must name a valid profile.
Non-arpeggio instruments may leave it as `None`. Catalog validation must fail
before rendering if an eligible instrument has no profile or names an unknown
profile.

Profile configuration must be immutable data, separate from scheduling code:

```python
@dataclass(frozen=True)
class ArpeggioProfile:
    family: str
    fast_bpm_threshold: int
    normal_subdivision: str
    fast_subdivision: str
    gate_ratio: float
    base_velocity: int
    pattern_weights: Mapping[str, float]
    motif_weights: Mapping[str, float]
    change_interval_bars: tuple[int, ...]
    eighth_probability: float
    pair_probability: float
```

`gate_ratio` remains accepted for profile compatibility but does not control
the current timed scheduler. Every attack sustains to the source-event end.
`eighth_probability` is the chance of selecting an eligible eighth-note grid
when the profile's tempo preference is sixteenth notes. `pair_probability` is
the chance of grouping two adjacent traversal positions into one onset. If a
complete one-pass schedule cannot leave the minimum eighth-note sustain tail,
the event is serialized as a pad-style simultaneous chord instead.

### 4.2 Required initial profiles

The initial catalog must provide at least these behaviors:

| Profile | Normal grid | Fast grid | Fast threshold | Eighth chance | Pair chance | Character |
|---|---:|---:|---:|---:|---:|---|
| `keyboard-ringing` | sixteenth | eighth | 120 BPM | 35% | 25% | piano, electric piano, vibraphone |
| `keyboard-dry` | sixteenth | eighth | 120 BPM | 35% | 25% | harpsichord, clavinet, marimba |
| `guitar-ringing` | sixteenth | eighth | 90 BPM | 35% | 25% | nylon, steel, clean guitar, koto |
| `guitar-muted` | sixteenth | eighth | 110 BPM | 35% | 25% | muted guitar, banjo-like short attacks |
| `synth-pulse` | sixteenth | eighth | 150 BPM | 35% | 25% | square and other rhythmic synth leads |

These are defaults, not per-genre probabilities. Instrument entries determine
which profile applies. Additional profiles may be introduced without changing
the scheduler interface.

Above the profile threshold, prefer the fast subdivision. At any tempo, an
eligible event may still select the eighth subdivision using
`eighth_probability`. An eighth grid is eligible only when the chord has
enough sixteenth-note units for at least `ceil(voice_count / 2)` paired or
single onsets. Otherwise the scheduler falls back to sixteenths.

Subdivision selection occurs per playable source event after instrument
selection. A no-chord boundary starts a new phrase but does not redraw the
profile or instrument.

## 5. Timed performance model

### 5.1 Intermediate event

Do not directly build note strings inside the scheduling loop. Introduce a
small immutable intermediate record:

```python
@dataclass(frozen=True)
class ArpeggioAttack:
    event_index: int
    midi: int
    onset_sixteenths: int
    duration_sixteenths: float
    velocity: int
    source_index: int
```

`onset_sixteenths` is absolute from the start of V0. Fractional onsets are not
required by this spec. Each selected source index occurs once per event, and
two adjacent source indices may have the same onset. The duration is the
remaining source-event length:

```text
duration_sixteenths = source_event_end - onset_sixteenths
```

The scheduler reserves two sixteenth-note units after the latest arpeggio
onset. An event that cannot fit all selected source indices, allowing at most
two indices per onset, within that reservation returns a pad fallback
diagnostic and no `ArpeggioAttack` records for that event.

The pure scheduler accepts:

```python
voiced: Sequence[VoicedChord | None]
events: Sequence[ChordEvent | dict]
bpm: int | float
profile: ArpeggioProfile
rng: random.Random
```

and returns:

```python
attacks: list[ArpeggioAttack]
diagnostics: list[dict]
```

It must not inspect raw root names, resolve chord degrees, select instruments,
or mutate supplied MIDI, roles, hands, or diagnostics.

### 5.2 Serialization requirement

The JFugue serialization backend must preserve each attack's absolute onset
and duration to the source-event boundary. A pair is serialized as one
simultaneous `+` token with the two numeric note durations. Boundary markers
continue to provide explicit source-event limits to the Java renderer.

The generic humanizer may add timing and velocity variation after scheduled
onsets, but it must preserve source-event boundaries and intentional relative
accent levels.

## 6. Rhythm and tempo

### 6.1 Supported grids

The first implementation supports:

- sixteenth-note grid: 4 onsets per beat;
- eighth-note grid: 2 onsets per beat.

Every currently generated source duration is representable by both grids.
Unknown durations or non-integral grid counts must produce a clear
`ValueError`.

For a playable event, the scheduler creates one onset group per single or
paired traversal position:

```text
onset_count <= voice_count
attack_count = voice_count
pair_count = number of onset groups containing two attacks
```

Every selected voicing note is attacked exactly once. A pair consumes one grid
position, so an eighth-note pass is allowed when the chord can fit all of its
onset groups and still leave the minimum eighth-note sustain tail. If neither
grid can satisfy that constraint, the event emits one simultaneous
source-voicing pad token and zero arpeggio attacks. Rhythmic rests and
syncopated onsets are outside this spec. No-chord events continue to emit a
single source-duration rest in score text and zero arpeggio attacks.

### 6.2 Source-boundary duration

For every attack:

```text
actual_note_off = source_event_end
```

Every attack therefore carries to the end of its source chord and never leaks
into the next chord or a no-chord event. Because each source index is emitted
once, no repeated-pitch retrigger rule is needed at the scheduler layer. Pad
fallback tokens use the normal source duration and attack all selected source
pitches simultaneously.

## 7. Pattern state and traversal

### 7.1 Continuous phrase position

Choose one pattern family and one initial phase when a playable phrase starts.
Maintain a monotonically increasing phrase step across chord changes. Each
source event consumes one pass through its selected voicing; the phrase step
only rotates the starting point of that pass.

For event-local cycle `cycle`:

```text
source_index = cycle[(phrase_step + pass_position) % len(cycle)]
phrase_step += 1
```

The cycle is a permutation of the selected source indices. It must not contain
return notes or omissions. When voice count changes, recompute the event's
cycle and map the continuing phrase step modulo its new length.

A no-chord event resets:

- pattern family;
- motif family;
- initial phase;
- phrase step;
- metric-change countdown.

### 7.2 Pitch and string order

For piano and synth profiles, base order is ascending `VoicedChord.midi`.

For guitar profiles, base order is physical string order using:

```text
VoicedChord.diagnostics["sounding_strings"]
```

The engine already records these string numbers aligned with the selected
MIDI list. The scheduler must validate that the list exists, has the same
length as `VoicedChord.midi`, contains distinct valid string numbers `1..6`,
and corresponds one-to-one with the selected pitches.

Guitar ascending order means string 6 toward string 1. Descending order means
string 1 toward string 6. If guitar provenance is invalid, fail clearly; do
not silently fall back to pitch order.

No change to the guitar shape library or voicer selection is required.

### 7.3 Pattern families

Retain the four pattern families from spec 12:

- `up`;
- `down`;
- `up_down`; and
- `outside_in`.

Choose them using the active profile's weights. Weights must be finite,
non-negative, and contain at least one positive value.

For guitar profiles, `outside_in` must default to weight zero because repeated
wide string skips are less idiomatic. It remains available to profiles that
explicitly opt in.

### 7.4 Motif families

Motif labels remain available for manifest compatibility, but the simplified
performance model does not insert returns or omit voices. Every motif uses the
same one-pass source-index set, so each selected note is still attacked
exactly once:

| Motif | Behavior | Minimum voices |
|---|---|---:|
| `straight` | consume the base cycle unchanged | 1 |
| `bass_return` | use the same one-pass cycle | 3 |
| `top_return` | use the same one-pass cycle | 3 |
| `light_alternate` | use the same one-pass cycle | 4 |

For one- and two-voice chords, unsupported motifs must collapse to `straight`
without an additional random draw.

When two adjacent source indices are paired, they share one onset and are
serialized as a simultaneous `+` token. Pairing is controlled by
`pair_probability`, except that pairs may be forced when needed to fit every
source index in the selected grid.

## 8. Controlled pattern changes

Patterns must not remain fixed indefinitely, and they must not change at every
chord.

Assume a four-beat bar for this feature, matching the current percussion
groove's eight eighth-note positions. Time-signature generalization is outside
this spec.

At phrase start, draw a change interval from the profile's
`change_interval_bars`. After that many complete bars, change pattern and/or
motif at the first source event beginning on or after the boundary.

Rules:

- never change within a source chord event;
- do not redraw a family if only one family has positive weight;
- when alternatives exist, a pattern change must select a different pattern;
- motif may remain unchanged;
- reset the interval after each eligible change; and
- use stable weighted selection with the arpeggio child RNG.

The default interval choices must be `(2, 4)`. Instrument profiles may narrow
that set but must not permit zero or negative values.

## 9. Metric accents

### 9.1 Accent map

Use absolute score position in fixed 4/4. Accent intent is:

| Position | Velocity adjustment |
|---|---:|
| bar downbeat | `+10` |
| beats 2, 3, and 4 | `+4` |
| eighth-note offbeat | `0` |
| remaining sixteenth positions | `-4` |

Add the adjustment to `profile.base_velocity` and clamp the result to
`1..127`.

The scheduler produces intended velocities before generic Java humanization.
Humanization may jitter them but must not flatten all attacks to a common base
velocity. Tests must compare accent classes before random jitter and verify
that their intended ordering is preserved by the renderer on average.

### 9.2 Boundary behavior

Metric position continues through playable chords and no-chord events. A
no-chord event resets phrase pattern state but does not reset bar position.
The first playable attack after a rest receives the accent corresponding to
its actual score position.

## 10. Determinism

Continue using stable child seeds derived without Python's randomized
`hash()`. Split arpeggio randomness into explicit streams:

1. `arpeggio-profile-pattern`;
2. `arpeggio-profile-motif`;
3. `arpeggio-profile-change`.

Velocity accents and gate calculation are deterministic and require no random
draws. Adding a new pattern or motif must not perturb voicing, instrument,
bass, or percussion selection.

Equivalent runs with the same source, seed, selected instrument, and code
revision must produce identical score text and arpeggio provenance.

## 11. Manifest

Retain `source_voicing_midis` as the harmonic authority for corpus validation.
Replace the fixed-subdivision assumptions with:

```json
"render_mode": "arpeggios",
"arpeggio": {
  "performance_profile": "guitar-ringing",
  "meter": "4/4",
  "event_subdivisions": ["sixteenth", "sixteenth", null],
  "event_onset_counts": [16, 8, 0],
  "event_pad_fallbacks": [false, false, false],
  "event_pattern_families": ["up", "down", null],
  "event_motif_families": ["straight", "bass_return", null],
  "event_start_phases": [2, 0, null],
  "gate_ratio": 1.75,
  "source_voicing_midis": [[48, 55, 60], [50, 57, 62], []]
}
```

For backward compatibility:

- the validator must continue accepting spec 12 manifests with
  `"subdivision": "sixteenth"` and `event_slot_counts`;
- new renders must write the spec 13 fields;
- pad records continue to use a missing or null `arpeggio` value consistently;
  and
- manifest version must be bumped only if the existing top-level versioning
  policy requires consumers to reject new fields.

Per-event pattern and motif arrays are authoritative provenance. A singular
top-level `pattern_family` is no longer sufficient.

## 12. Validator

Generalize `eda/validate_rendered_corpus.py` to validate timed attacks rather
than assuming one sequential sixteenth token per onset.

For each source event, validate:

1. declared onset count is sufficient for one attack per selected voicing
   note, allowing at most two adjacent notes per onset;
2. every arpeggiated attack retains at least two sixteenth-note units before
   the event boundary;
3. pad-fallback events contain one simultaneous source-voicing token and zero
   arpeggio attacks;
4. every attack onset lies within the event;
5. the first attack is at the event start;
6. every attack MIDI is an exact member of `source_voicing_midis`;
7. every selected voicing MIDI occurs exactly once;
8. note-off equals the source-event boundary;
9. velocity is in `1..127`;
10. no-chord events contain one duration-sized V0 rest and zero attacks;
11. guitar string-order provenance is structurally valid when a guitar profile
   is selected; and
12. total V0, V1, and V9 timelines agree.

Continue applying active-degree, DCT, rootless, omission, and unrequested-pitch
checks to `source_voicing_midis`, not to every individual attack.

## 13. Required tests

### 13.1 Scheduler unit tests

1. **Tempo grid:** verify each profile on both sides of its fast threshold,
   including exactly at the threshold.
2. **Duration conservation:** exercise every supported source duration on
   both eighth and sixteenth grids.
3. **Continuous phase:** verify phrase step continues across chord changes
   with different voice counts and resets after `N`.
4. **Guitar string order:** use a voicing whose sorted MIDI and declared
   string traversal are independently observable; verify both directions.
5. **Invalid guitar provenance:** mismatched or duplicate string metadata
   must fail clearly.
6. **Pattern changes:** verify changes happen only at eligible source-event
   boundaries after two or four complete bars.
7. **One-pass duration:** verify every selected source note is attacked once,
   all note-offs reach the source-event boundary, and every attack retains at
   least an eighth note.
8. **Eighth-grid fit:** verify eighths are selected only when the pass and
   sustain tail fit.
9. **Pad fallback:** verify dense short events render as one simultaneous pad
   chord with no arpeggio attacks.
10. **Paired attacks:** verify the pair probability produces a simultaneous
    token without duplicating or omitting a source note.

### 13.2 Integration tests

1. **Mode-invariant voicing:** pads and arpeggios retain identical selected
   voicer, MIDI, roles, and voicing diagnostics.
2. **Renderer sustain:** generated MIDI proves each arpeggio note reaches its
   source-event boundary.
3. **Renderer pairing:** generated MIDI preserves simultaneous paired attacks.
4. **Bass and percussion isolation:** V1 and V9 remain byte-identical between
   pad and arpeggio runs under a fixed source and seed.
5. **Manifest round trip:** new provenance validates successfully, and a
   spec 12 manifest remains accepted.
6. **Hard failures:** out-of-voicing pitch, incorrect onset count, onset
   outside the event, note-off beyond the event, and malformed guitar string
   provenance each produce a specific validator failure.
7. **Determinism:** repeated renders with the same seed are byte-identical.

### 13.3 Musical acceptance corpus

Render a fixed diagnostic corpus containing:

- BPM values `60`, `90`, `91`, `120`, `121`, `150`, `151`, and `180`;
- one- through six-voice voicings;
- piano, dry keyboard, ringing guitar, muted guitar, and synth profiles;
- mixed quarter, dotted-quarter, half, whole, and double-whole events; and
- no-chord boundaries.

Machine checks must confirm:

- no guitar profile exceeds its configured attack grid;
- no pitch falls outside its selected source voicing;
- every selected source pitch is attacked exactly once;
- every attack reaches its source-event boundary;
- every arpeggiated attack retains at least an eighth note of sustain, or the
  event is represented by the pad fallback;
- eighth-note passes occur only when their onset groups fit;
- paired attacks preserve simultaneous adjacent traversal positions;
- pattern changes occur at the documented boundaries; and
- every track ends on the same harmonic timeline.

## 14. Implementation sequence

Implement in independently testable stages:

1. **One-pass scheduler:** source order, guitar string order, and source-end
   durations.
2. **Grid selection:** fit-aware eighth/sixteenth selection and pair
   probability.
3. **Serialization:** grouped absolute-time tokens, manifest, validation, and
   renderer integration.

Each stage must preserve the spec 12 pitch-membership and track-isolation
invariants. Do not defer those invariants until the final stage.

## 15. Non-goals

- changing voicer candidate generation or selection;
- introducing non-chord tones, chromatic approaches, or passing tones;
- changing source chord durations or progression structure;
- introducing rhythmic rests or syncopated onset grids;
- swing interpretation;
- tempo automation or time signatures other than fixed 4/4;
- modeling individual right-hand fingers or pick direction;
- changing bass or percussion arrangement; and
- adding a third simultaneous note or an onset outside the supported grids.

## 16. Acceptance criteria

The enhancement is complete when:

1. each selected voicing note is attacked exactly once per playable source
   event;
2. arpeggio attack rate adapts to profile/BPM preferences but falls back when
   an eighth-note pass cannot fit, while preserving an eighth-note sustain
   tail or using the pad fallback;
3. guitar traversal follows physical sounding-string order;
4. adjacent traversal positions can be paired with the configured probability;
5. all arpeggio note-offs equal their source chord event boundary and retain
   at least an eighth note of sustain;
6. no note onset or note-off escapes its source chord event;
7. pad and arpeggio modes retain identical source voicings, bass, and
   percussion for equivalent runs;
8. old spec 12 manifests remain valid; and
9. all scheduler, renderer, determinism, manifest, and corpus checks pass.
