# Spec 14 - Chord-Aware Melody Generation

- **Status:** implementation plan; not yet implemented
- **Applies to:** a new `melody_module.py`, `render.py`, `instruments.py`,
  `eda/validate_rendered_corpus.py`, `HumanizedMidiRenderer.java`, tests,
  target-corpus curation, and documentation
- **Parents:** specs 07, 08, 11, 12, and 13
- **Research basis:** `melody_docs/melody_research_report.md` and
  `specs/14.5-melody-research-justification.md`

This document turns the melody research report into an incremental
implementation plan. It deliberately separates decisions that are required for
a first symbolic prototype from later audio experiments.

## 1. Decision summary

Add melody as an **opt-in, downstream augmentation** of an already generated
and voiced chord progression:

```text
ChordEvent labels
    -> existing voicing engine
    -> VoicedChord sequence
    -> chord-aware melody candidates
    -> sequence-scored melody
    -> V2 JFugue melody track
    -> MIDI and manifest
```

The melody stage must never resample, remove, relabel, or shorten a chord
event. The existing `ChordEvent` sequence remains the recognition label and
the source of harmonic truth. Melody is a controlled nuisance or exposure
variable for that label.

The first implementation should use:

1. the active `ChordEvent` and `resolve_degrees()` as the primary pitch
   vocabulary;
2. the song tonic and an optional explicit mode or scale as a weak prior;
3. a sixteenth-note scheduling grid shared with the existing renderer;
4. a seeded, bounded chord-centered random walk for the symbolic MVP, with a
   phrase-level sequence scorer or beam search available as a later decoder;
5. seeded child random streams that cannot perturb voicing, bass, percussion,
   or arpeggio decisions;
6. a default neutral melody condition plus explicit DCT/extension exposure
   conditions; and
7. a separate `V2` track with machine-readable source-event and role
   provenance.

Per-chord key estimation is not the default. Optional local-key inference may
be added later as a phrase-level latent context with a persistence cost.

When melody is requested, apply one song-level inclusion gate with a default
probability of **70%**. Thus, 70% of eligible songs receive a generated melody
and 30% receive no V2 melody track, matching the existing percussion-style
song-level inclusion behavior. The gate is evaluated once per song, not once
per chord or note, and uses its own deterministic random stream. An explicit
`melody_condition=none` remains a hard disable and bypasses the gate. The
inclusion probability must be configurable for tests and corpus experiments,
with `0.70` as the default.

The research supplement separates evidence-backed design principles from
repository engineering defaults. In particular, it does not justify a
universal CT/NCT ratio, melody density, phrase length, or GM-instrument range.
Initial values must be configurable, included in the manifest, and calibrated
against a clearly identified melody reference corpus before they are described
as realism targets.

This specification now resolves most of the previously open implementation
details: data contracts, candidate roles and provenance, tonal-context
behavior, range/tessitura policy, rhythm and phrase defaults, bounded search,
resolution lookahead, deterministic streams, exposure conditions, manifests,
validation, and phased integration. It remains an implementation contract and
plan; the melody runtime is not yet implemented.

### 1.1 ACR label hierarchy and non-interference invariant

The chord hierarchy is authoritative and immutable for automatic chord
recognition (ACR):

```text
source ChordEvent and its full chord tuple
    -> VoicedChord realization and diagnostics
    -> V0 chord/arpeggio, V1 bass, and V9 percussion tracks
    -> optional V2 melody augmentation
```

Melody generation must not:

- resample, remove, relabel, split, merge, or shorten a `ChordEvent`;
- replace a source degree with a melody pitch or promote a melody pitch to
  chord-label evidence;
- reinterpret a rare, altered, or extension-rich chord as a different local
  key;
- treat a realized-voicing omission as permission to change the source label;
  or
- change V0, V1, V9, voicer selection, arpeggio decisions, or their timing.

Melody may expose, withhold, or intentionally distract from chord tones and
extensions as an explicit experimental condition, but those choices are
downstream audio/symbolic conditions only. Every variant must retain the
identical source chord labels, event boundaries, durations, and split
identity so ACR performance changes can be attributed to the condition rather
than to label drift.

### 1.2 Per-timepoint label safety

There are two different guarantees:

1. **Symbolic label preservation is hard.** For every timeline position, the
   active source interval and its complete `ChordEvent` tuple are unchanged.
   A melody event must reference the source event(s) it covers, and a held
   note may not silently become a new chord event.
2. **Audio ACR stability is measurable, not absolute.** A MIDI-level rule
   cannot guarantee the same recognizer output across soundfonts, mixes,
   instruments, and model front ends. Melody conditions must therefore state
   whether they are intended to be transparent or a labeled stress case.

The default `transparent` label-safety policy applies these symbolic proxies
at every source event:

- strong-position melody onsets use an active source degree or stable
  extension, unless they are a short, explicitly resolved NCT;
- every NCT has a role, target, and resolution within the configured
  `nct_resolution_horizon`; an unsupported sustained chromatic pitch is not
  transparent;
- a held note that becomes non-chordal after a playable boundary is labeled
  as a suspension/hold and must resolve within the same horizon;
- no melody pitch is emitted during a default no-chord interval; and
- melody remains on V2 and never contributes its pitch to V0, V1, V9, or the
  source label tuple.

The `stress` label-safety policy may permit chromatic, dense, or
same-register material that can change ACR predictions. It still preserves
the source label and event timeline, but its purpose is to measure robustness,
not to claim transparent recognition. The selected policy, per-event
violations/fallbacks, and source-event mapping must be recorded in the
manifest. Both policies must retain the same source label hash.

### 1.3 ACR training integrity and false-signal control

Adding melody creates a composite acoustic example. Preserving the
`ChordEvent` label does not, by itself, prove that a model will learn the
chord rather than a melody shortcut. The data protocol must distinguish:

```text
source ChordEvent -> accompaniment and label
source ChordEvent -> optional melody condition
```

The following rules apply when generated audio is used to train or evaluate
ACR:

- keep an accompaniment-only control for every source progression;
- create melody-on variants from the identical source label, voicing,
  accompaniment decisions, event timing, and source split;
- balance melody presence, instrument, register, density, exposure condition,
  and label-safety policy within each label/genre/rare-feature stratum;
- never use a melody note to replace missing or omitted chord evidence;
- keep `transparent` variants in the core ACR distribution and place
  `stress`, dense, chromatic, and intentionally exposed variants in a
  separately identified robustness distribution; and
- split by source progression before creating variants so matched versions
  cannot cross train/validation/test boundaries.

The melody-on examples are therefore an augmentation of the chord corpus, not
a new definition of what the chord sounds like. A model trained only on
melody-on data, or on an imbalanced exposure condition, may learn the
instrument, register, or a melody pitch correlated with a chord label. Such
data must not be used as evidence that the melody is label-neutral.

ACR evaluation must include at least:

- accompaniment-only training/testing;
- accompaniment-only training with transparent melody-on testing;
- balanced mixed-condition training with held-out melody conditions; and
- separate robustness reporting for stress conditions.

Report condition-conditional accuracy and confusion, not only aggregate
accuracy. A substantial cross-condition change is a dataset confound or an
intentional robustness result to investigate; it must not be silently
attributed to a change in the underlying chord label.

### 1.4 Data volume is not a substitute for balance

Increasing the corpus beyond 250,000 chord events can reduce estimation
variance and improve long-tail coverage, but it cannot remove a systematic
melody confound. If an instrument, register, density, or melody pitch remains
correlated with a label, more examples teach that shortcut more reliably.

Corpus sizing must therefore be evaluated by independent coverage of
`label x genre x accompaniment condition x melody condition`, including
instrument, register, density, and exposure strata. Raw event count is not
enough, and variants copied from one source progression do not count as
independent source evidence.

The 70% melody inclusion probability is a rendering behavior, not a guarantee
of training balance. The corpus builder must inspect realized counts and
stratify, resample, or weight examples so that melody presence and its
attributes are not predictive of the ACR label. More data is warranted for a
rare stratum only when it adds independent chord, voicing, timing, and
accompaniment variation; duplicating the same progression or melody pattern
does not provide equivalent coverage.

Before treating a larger corpus as a remedy, compare a melody-only predictor
with the intended ACR baseline and run the cross-condition tests in section
1.3. A larger dataset is a successful remedy only when it improves coverage
without increasing label predictability from melody-only features.

## 2. Goals and non-goals

### 2.1 Goals

The implementation should:

- generate reproducible symbolic melody lines over major, minor, borrowed,
  altered, extended, and otherwise non-diatonic chord events;
- favor active chord members and differentiating extensions on metrically
  important positions without making every note a chord tone;
- allow controlled passing, neighbor, approach, enclosure, suspension, and
  held tones;
- preserve bounded contour, register, density, phrase repetition, and
  cadence behavior;
- expose or withhold DCT and active extensions independently from chord
  rarity;
- provide sparse, dense, high-register, same-register, and chromatic
  conditions for recognition and masking experiments;
- preserve exact source-event timing and label provenance;
- keep accompaniment behavior unchanged when melody is disabled or enabled
  with the same source seed; and
- provide a separately inspectable melody stem and symbolic event map.

### 2.2 Non-goals

The first implementation must not:

- generate or revise chord labels;
- replace the Stage 1-3 chord generator;
- reject a rare chord because melody generation is difficult;
- make a new hard key decision at every chord change;
- require every melody note to be diatonic;
- infer melody quality from a monophonic audio extractor alone;
- make source separation a runtime dependency;
- claim that a high melody is always perceptually transparent; or
- change the existing pad, arpeggio, bass, or percussion algorithms.

Source separation, masking models, and audio recognizers belong to the
experiment phase. They must consume the original mix and explicit stems rather
than overwrite symbolic labels.

## 3. Current repository seam

### 3.1 Harmonic input

The normative input is the existing `ChordEvent` contract in
`voicing/types.py`:

- `root_interval`;
- `triad`;
- `bass_interval`;
- `seventh`;
- `ninth`;
- `eleventh`; and
- `thirteenth`.

`root`, `bass`, and `harte` remain display and diagnostic fields. Melody code
must not parse those fields to infer pitch content.

Use `resolve_degrees()` to obtain the active root, third, fifth, seventh,
ninth, eleventh, and thirteenth after collision merging. A collision-merged
degree is one source degree for candidate generation and must retain its
merged-role provenance.

### 3.2 Optional realized-voicing context

`VoicedChord` may be passed to melody generation as optional context:

- realized MIDI pitches and register;
- realized roles and DCT pitch;
- omissions and collision diagnostics;
- guitar shape or piano/synth hand information; and
- source-to-realized extension mismatch.

The source `ChordEvent` remains authoritative. If a requested extension was
omitted by the voicer, the melody may still target it in an exposure variant,
but the manifest must distinguish:

```text
source_degree_evidence
realized_voicing_evidence
```

The melody generator must not silently treat an omitted voiced extension as
audible accompaniment evidence.

### 3.3 Existing timing and tracks

The current renderer already provides the required timing foundation:

- source duration tokens are represented on a sixteenth-note grid;
- `V0` is the chord or arpeggio track;
- `V1` is the bass track;
- `V9` is the percussion track; and
- render manifests pair each score block with its source label and seed.

Reserve `V2` for melody. No current output uses `V2`, so adding an opt-in
melody track does not collide with existing tracks. The Java renderer's
channel-2 comment and timing bias should be renamed from its unused
"Extensions" description to "Melody" when the track is implemented.

## 4. Backward-compatible data contracts

### 4.1 Optional tonal context

The current `Song` contract has `tonic_pc` but no explicit mode. Add optional
tonal context without making it a hard legality filter:

```jsonc
{
  "genre": "jazz",
  "tonic_pc": 0,
  "mode": "major",                  // optional
  "scale_pcs": [0, 2, 4, 5, 7, 9, 11], // optional, tonic-relative
  "bpm": 120,
  "num_chords": 48,
  "chords": [ ... ]
}
```

Rules:

1. `scale_pcs`, when present, wins over `mode`.
2. `mode` is resolved against `tonic_pc` through a small named-scale table.
3. If neither is present, no global scale is imposed; tonic information may
   still be used as a weak continuity feature.
4. A scale candidate is never allowed to displace a feasible active chord
   candidate solely because it is diatonic.
5. Adding these optional fields must not change existing chord generation or
   rendering when melody is disabled.

The first implementation can accept a render-time melody mode/profile instead
of changing every existing label file. Generated labels should preserve any
future mode metadata when available.

### 4.2 Melody event

Add an immutable intermediate record. The exact class name may vary, but it
must carry the following information:

```python
@dataclass(frozen=True)
class MelodyEvent:
    source_event_indices: tuple[int, ...]
    onset_sixteenths: int
    duration_sixteenths: float
    midi: int | None              # None for a rest
    velocity: int | None
    role: str                     # chord_tone, extension, scale_tone, ...
    degree_role: str | None       # root, 3rd, 5th, 7th, 9th, 11th, 13th
    pitch_source: str             # source_degree, realized_voicing, ...
    metric_strength: str          # strong, medium, weak, offbeat
    arrival_interval: int | None
    departure_interval: int | None
    resolution_target: int | None
```

`source_event_indices` contains the source event at the onset and any
additional playable events covered by a held note. It is required because a
melody note may span playable chord boundaries.

Supported `role` values are:

```text
chord_tone
extension
scale_tone
approach
passing
neighbor
enclosure
suspension
appoggiatura
held
rest
fallback
```

Supported `pitch_source` values are:

```text
source_degree
realized_voicing
global_scale
chromatic_neighbor
previous_melody
rest
```

The serializer must not infer role metadata from the final MIDI token. The
candidate and sequence stages must assign it before serialization.

### 4.3 Melody render profile

Keep melody performance configuration separate from chord voicer policies:

```python
@dataclass(frozen=True)
class MelodyProfile:
    name: str
    instrument_program: int
    hard_min: int
    hard_max: int
    tessitura_min: int
    tessitura_max: int
    preferred_center: int
    base_velocity: int
    max_leap_semitones: int
    density_weights: Mapping[str, float]
    rest_probability: float
    hold_probability: float
```

The first symbolic implementation should also expose its bounded search
settings as a separate immutable configuration:

```python
@dataclass(frozen=True)
class MelodyGenerationConfig:
    time_grid_sixteenths: int = 1
    subphrase_bars: int = 2
    phrase_context_bars: int = 8
    cadence_context_bars: int = 2
    nct_resolution_horizon: int = 2
    beam_width: int = 32
    candidate_limit_per_onset: int = 24
    decoder: str = "chord_centered_random_walk"
    label_safety_policy: str = "transparent"
    masking_cost_enabled: bool = False
```

These defaults are repository-level engineering choices, not universal
musical measurements. Validate positive integer limits at construction and
restrict `decoder` to `chord_centered_random_walk` or `sequence_beam`.
Restrict `label_safety_policy` to `transparent` or `stress`. Record the
effective configuration in the render manifest.

The initial catalog should contain at least:

| Profile | Intended register | Initial use |
|---|---|---|
| `lead-high-sparse` | above most chord voicings | neutral, transparent melody |
| `lead-mid-neutral` | overlaps the upper accompaniment range | ordinary augmentation |
| `lead-mid-dense` | chord-register overlap | masking stress condition |

Program numbers and exact weights should be centralized in
`instruments.py`, validated at startup, and selected with a deterministic
child RNG. A profile must never modify chord instrument selection.

### 4.4 Melody-only instrument catalog

The melody catalog is additive and must remain separate from
`CHORD_INSTRUMENTS` and `BASS_INSTRUMENTS`. Do not add the `melody` role to an
existing chord or bass entry, change an existing program number, or change the
weights or eligibility of any non-melody module. A future implementation may
define a separate `MELODY_INSTRUMENTS` tuple or mapping in `instruments.py`;
the existing catalogs remain unchanged.

The repository stores `Instrument.program` as a **zero-based raw MIDI/GM
program ID**. The 1-based patch number is included below only to prevent
off-by-one mistakes when comparing with external GM tables. Runtime score
generation must use the zero-based ID, consistent with the current
`I{program}` output.

The first melody implementation should keep this curated set:

| AAM family | Melody instrument name | Program ID (0-based) | GM patch (1-based) | Current catalog |
|---|---|---:|---:|---|
| Bowed | `violin` | 40 | 41 | melody-only |
| Bowed | `viola` | 41 | 42 | melody-only |
| Bowed/world string | `fiddle` | 110 | 111 | already present |
| Brass | `trumpet` | 56 | 57 | melody-only |
| Brass | `trombone` | 57 | 58 | melody-only |
| Flute/Pipe | `flute` | 73 | 74 | melody-only |
| Flute/Pipe | `pan-flute` | 75 | 76 | melody-only |
| Flute/Pipe | `shakuhachi` | 77 | 78 | already present |
| Guitars | `electric-guitar-clean` | 27 | 28 | already present |
| Guitars | `overdriven-guitar` | 29 | 30 | already present |
| Guitars | `distortion-guitar` | 30 | 31 | already present |
| Sax/Reed | `alto-sax` | 65 | 66 | melody-only |
| Sax/Reed | `tenor-sax` | 66 | 67 | melody-only |
| Sax/Reed | `clarinet` | 71 | 72 | melody-only |
| Synth lead | `synth-lead-square` | 80 | 81 | already present |

Use the catalog with the following initial profile guidance:

| Profile | Preferred instruments |
|---|---|
| `lead-high-sparse` | `violin`, `fiddle`, `flute`, `pan-flute`, `alto-sax`, `clarinet`, `shakuhachi` |
| `lead-mid-neutral` | `viola`, `trumpet`, `electric-guitar-clean`, `clarinet`, `tenor-sax`, `fiddle` |
| `lead-mid-dense` | `overdriven-guitar`, `distortion-guitar`, `trombone`, `tenor-sax`, `synth-lead-square`, `viola` |

The profile is allowed to reject a preferred patch when its configured MIDI
range or tessitura would make the requested melody infeasible. Such a choice
must be recorded as an instrument-selection fallback; it must not mutate the
other module catalogs.

The following reference instruments are intentionally not in the first melody
catalog:

- `Erhu`, `Jinghu`, `Morin Khuur`, `Fujara`, `Flugelhorn`, and `Ukulele` do not
  have dedicated General MIDI 1 programs in the supplied table. Do not invent
  program IDs; add them only behind an explicit non-GM soundfont mapping.
- `Concert Flute` is represented by the standard GM `flute` entry above.
- `Cello` and `Contrabass` remain chord/bass resources in the current role
  design and are not selected for the initial melody range.
- `Sitar`, `Banjo`, `Shamisen`, and `Koto` remain chord/arpeggio resources in
  the current design, despite being capable of carrying a melodic line.
- Piano, electric-piano, organ, pad, choir, and sound-effect patches remain
  outside the initial melody catalog so harmonic and melodic roles stay
  distinguishable.

## 5. Tonal and harmonic candidate model

### 5.1 Candidate sources

At each eligible onset, build a bounded candidate set from these sources:

1. **Active chord degrees.** Use `resolve_degrees()` and retain role,
   semitone, token, and merged-role information.
2. **Realized chord pitches.** Use only as optional register/provenance
   candidates; do not reconstruct omitted degrees from them.
3. **Global scale notes.** Use `scale_pcs` or the resolved mode as a soft
   prior.
4. **Held notes.** Include the previous melody pitch when its duration,
   range, and new harmonic context make a hold plausible.
5. **Chromatic neighbors.** Generate only controlled semitone or whole-tone
   approaches, passing tones, neighbors, enclosures, suspensions, and
   appoggiaturas tied to a nearby chord or scale target.
6. **Rest.** Always include a rest candidate subject to density and phrase
   weights.

Do not enumerate every MIDI pitch in the profile range for every slot. First
enumerate pitch classes and roles, then choose the nearest legal octave(s)
within the instrument hard range. Penalize, rather than reject, candidates
outside the soft tessitura. GM program numbers do not define these ranges.

For each candidate, retain `metric_strength`, `duration_sixteenths`,
`arrival_interval`, `departure_interval` when a successor is available,
`resolution_target`, and role metadata. These features support the
metric/duration/interval evidence summarized in spec 14.5 and make a
chromatic choice auditable.

### 5.2 Metric weighting

The candidate scorer should distinguish strong and weak positions. Until a
meter field is added to `Song`, use 4/4 with sixteen sixteenth units per bar:

| Position | Default preference |
|---|---|
| beat 1 and beat 3 | root, third, fifth, stable extension, held tone |
| beat 2 and beat 4 | chord tone, extension, held tone, controlled motion |
| offbeats | scale, approach, passing, neighbor, rest |

These are weights, not hard rules. A chromatic approach on a strong position
is allowed when it resolves promptly and the selected condition permits it.

### 5.3 Rare and altered events

The source chord must remain visible in melody diagnostics. For an altered
event, for example `A7(b9)` in a C-tonic progression:

```text
active source pitch classes = A, C#, E, G, Bb
global key scaffold          = C-major, if supplied
```

`C#` and `Bb` may be favored as explicit exposure targets without declaring a
new D-major key for that event. Neighboring context and phrase resolution
should determine whether the tension is musically plausible.

### 5.4 No-chord handling

An `is_no_chord` event is a harmonic boundary:

- emit no melody pitch by default;
- terminate or clip a preceding held note at the boundary;
- emit a synchronized rest for the no-chord duration;
- reset phrase-local target and contour state; and
- record `no_chord_policy` and any fallback in the manifest.

A later profile may allow a conservative held-note policy across a no-chord
event, but it must be explicit and separately labeled. The default should
protect chord-recognition interpretation.

## 6. Sequence generation

### 6.1 Phrase segmentation

Partition the melody timeline at:

- the start and end of the song;
- no-chord events;
- optional bar boundaries; and
- optional configured section or phrase boundaries.

Playable chord boundaries are not necessarily melody boundaries. A held note
may span adjacent playable events if the sequence scorer accepts the
horizontal and harmonic cost.

Retain the predecessor's useful proposal mechanisms:

- bounded contour;
- explicit rests;
- copied prefixes or motifs; and
- a final cadence proposal.

Every copied or cadential proposal must be rescored against the active chord
sequence before acceptance.

### 6.2 Rhythm and density

Use absolute sixteenth-note units for all melody onsets and durations. The
profile controls density rather than forcing one fixed rhythm:

- sparse profiles should prefer eighth-note or longer durations;
- neutral profiles may use eighths with occasional sixteenths;
- dense profiles may activate adjacent sixteenth slots;
- rests are explicit events, not missing data; and
- a held note may continue through playable chord changes.

Every event must remain inside the song timeline. If a generated note would
extend beyond the song, clip it and record the clip. If a candidate cannot
meet range, leap, or density constraints, emit a rest or a conservative held
note and record the fallback instead of changing the chord sequence.

Generate a phrase/rhythm plan before pitch realization. The initial symbolic
defaults are two-bar subphrases, an eight-bar phrase context window, a
two-bar cadence window, and a two-attack lookahead for NCT resolution. These
values are bounded engineering defaults inspired by hierarchical-generation
research, not universal musical constants. Keep them in the melody
configuration and manifest so later corpus calibration can change them without
changing the source chord labels.

### 6.3 Sequence scorer

The symbolic MVP should use a seeded, bounded **chord-centered random walk**.
This is not a random walk over all MIDI pitches. At each onset it:

1. enumerates active `resolve_degrees()` pitch classes and their roles;
2. resolves those candidates to the nearest legal octave(s) around the
   previous melody pitch;
3. weights active chord degrees and stable extensions by metric position and
   exposure condition;
4. applies a distance/maximum-leap transition weight so nearby candidates are
   preferred without forcing a fixed contour; and
5. samples one candidate, hold, or rest from the seeded melody RNG.

In `transparent` core output, chord-tone candidates are preferred and
unsupported non-chord tones are not proposed by default. A controlled NCT may
be enabled only when it has an explicit target and resolves within the
configured horizon. `stress` output may relax this policy, but must remain
separately identified.

The random walk is the production MVP because its transition choices,
candidate pool, and failure cases are straightforward to inspect. A bounded
beam search or equivalent phrase-level decoder remains a later
`sequence_beam` option and an ablation; it is not required to implement the
first symbolic melody path.

The sequence score should combine:

```text
+ active chord-degree compatibility
+ DCT/extension exposure according to condition
+ global scale or phrase-key fit
+ metrical stability
+ short-horizon tension followed by resolution
+ interval and contour continuity
+ held-note and voice-leading continuity
+ phrase repetition and cadence fit
+ profile range and tessitura fit
+ intended density and rest preference
+ optional realized-voicing/register context
- excessive leap and register correction
- unresolved chromatic tension
- excessive repeated pitches or notes
- optional spectral-overlap/masking proxy
```

The masking term is disabled for the symbolic MVP. It must remain an optional
term so a symbolic harmonic score cannot be silently replaced by an audio
heuristic.

Apply scoring in this priority order:

1. hard validity and label-preserving constraints;
2. active chord-degree compatibility, exposure condition, and short-horizon
   NCT resolution;
3. interval/contour continuity, held-note continuity, phrase repetition,
   cadence, and soft tessitura;
4. global-scale fit, realized-voicing overlap, density, and optional masking.

The later `sequence_beam` decoder uses a configurable beam width of 32 and
keeps at most 24 octave-resolved candidates per onset by default. These are
performance defaults rather than research measurements; record effective
values in the manifest. The random-walk MVP does not need a beam, but the
configuration remains available for the later decoder.

Hard constraints are limited to:

- MIDI range and valid velocity;
- positive duration and in-range onset;
- no overlapping monophonic melody notes;
- configured density and maximum-leap limits, except where a scored octave
  option is explicitly accepted;
- valid source-event indices;
- no pitch during default no-chord events; and
- exact preservation of chord-event count, boundaries, durations, and labels.

All other musical preferences are weighted costs. A candidate that violates a
soft preference may still win when it gives a better phrase resolution.

### 6.4 Deterministic random streams

Derive melody randomness from the existing song/render seed using stable
domain labels, never Python's process-randomized `hash()`:

```text
render seed
    -> melody-inclusion
    -> melody-profile
    -> melody-rhythm
    -> melody-candidate
    -> melody-tie-break
```

The melody streams must not consume the voicing, bass, percussion, instrument,
or arpeggio RNGs. With the same progression, render seed, voicing order,
profile, condition, and inclusion probability, the inclusion decision and
melody output must be byte-reproducible.

## 7. Exposure conditions

The condition is an explicit parameter independent of chord rarity:

| Condition | Intended behavior |
|---|---|
| `neutral` | ordinary chord-aware melody with moderate DCT/extension preference |
| `dct_exposed` | favor the active DCT or differentiating extension on strong positions when feasible |
| `dct_withheld` | avoid intentionally targeting the DCT/differentiating extension, especially on strong positions |
| `chromatic` | permit a controlled, measurable increase in approach/passing/enclosure tones |
| `dense_overlap` | increase activity and move the melody toward accompaniment register |
| `oracle_stem` | not a pitch policy; evaluate separately separated melody/accompaniment stems |

`dct_exposed` and `dct_withheld` must record whether the requested condition
was feasible for each event. A withheld condition may still contain a
non-chord tone or a weak incidental extension when required by contour; it
must not deliberately target the withheld differentiator.

The initial implementation order is:

1. `neutral`;
2. `dct_exposed`;
3. `dct_withheld`; and
4. `chromatic` and `dense_overlap`.

Condition selection must not be inferred from a chord's rarity. The same base
progression should be renderable under multiple conditions, and all variants
must stay in the same train/validation/test split.

## 8. Rendering integration

### 8.1 Python API and CLI

Add an opt-in argument while preserving existing defaults:

```python
render_song(
    progression,
    seed=seed,
    mode="pads",
    melody_condition="none",
    melody_profile="lead-high-sparse",
    melody_inclusion_probability=0.70,
)
```

For directory rendering, add equivalent options:

```text
--melody-condition
    none | neutral | dct_exposed | dct_withheld | chromatic | dense_overlap
--melody-profile
    lead-high-sparse | lead-mid-neutral | lead-mid-dense
--melody-inclusion-probability
    floating-point value from 0.0 to 1.0; default 0.70
```

`none` is the default and must preserve existing accompaniment behavior.
Melody configuration is independent of `--mode mixed` and its
`--arpeggio-percent`/`--pad-percent` allocation.

For any condition other than `none`, the inclusion probability is evaluated
once for each source song. If the song is not selected, render no V2 track and
record `omission_reason=probability_gate`; do not generate or partially
serialize a melody. A probability of `1.0` is the deterministic way to
request melody for every song, and `0.0` is equivalent to an intentional
omission while retaining the requested condition in provenance.

### 8.2 Score tracks

When enabled, `render.py` combines tracks in this order:

```text
V0 chord/arpeggio
V1 bass
V2 melody
V9 percussion
```

The V2 prefix must contain the song tempo and melody instrument program. The
melody serializer should reuse the renderer's absolute-time and numeric
duration conventions so notes can span source chord boundaries without
recovering timing from audio.

When disabled, no V2 track is emitted. Existing score blocks, V0/V1/V9
content, and single-mode/mixed-mode behavior remain unchanged.

### 8.3 MIDI humanization

Update `HumanizedMidiRenderer.java` only as needed to:

- recognize V2 as the melody channel;
- apply a documented melody timing/velocity bias;
- preserve melody note pairing and durations; and
- avoid applying arpeggio source-boundary logic to melody notes unless a
  distinct melody marker is introduced.

The symbolic melody event map, not humanized MIDI timing, is authoritative for
source-event alignment. Humanization must not add or remove melody notes.

## 9. Manifest and provenance

Add a `melody` field to each render manifest record:

```jsonc
{
  "melody": {
    "enabled": true,
    "included": true,
    "condition": "neutral",
    "profile": "lead-high-sparse",
    "acr_dataset_role": "core_acr",
    "inclusion_probability": 0.7,
    "omission_reason": null,
    "instrument": "flute",
    "instrument_program": 73,
    "track": "V2",
    "seed": 12345,
    "note_count": 42,
    "rest_count": 18,
    "held_note_count": 7,
    "chord_tone_count": 24,
    "extension_count": 8,
    "chromatic_count": 4,
    "dct_exposure_count": 3,
    "range": [67, 88],
    "hard_range": [55, 105],
    "tessitura": [67, 96],
    "preferred_center": 82,
    "max_leap_semitones": 9,
    "generation_config": {
      "time_grid_sixteenths": 1,
      "subphrase_bars": 2,
      "phrase_context_bars": 8,
      "cadence_context_bars": 2,
      "nct_resolution_horizon": 2,
      "beam_width": 32,
      "candidate_limit_per_onset": 24,
      "decoder": "chord_centered_random_walk",
      "label_safety_policy": "transparent",
      "masking_cost_enabled": false
    },
    "fallback_count": 0,
    "no_chord_policy": "rest_and_reset",
    "events": [
      {
        "source_event_indices": [12],
        "onset_sixteenths": 96,
        "duration_sixteenths": 4.0,
        "midi": 74,
        "velocity": 88,
        "role": "extension",
        "degree_role": "b9",
        "pitch_source": "source_degree"
      }
    ]
  }
}
```

When `melody_condition=none`, `melody` is `null` or an equivalent explicit
disabled record. When a requested melody is omitted by the 70% gate, retain an
explicit disabled record with `included=false`, the requested condition and
profile, the configured `inclusion_probability`, and
`omission_reason=probability_gate`. The chosen representation must be
consistent across all manifests.

The manifest must also record:

- source label hash and source ordinal already used by the renderer;
- melody condition and profile;
- ACR dataset role (`core_acr` or `acr_robustness`);
- requested and realized inclusion state;
- configured inclusion probability and omission reason;
- deterministic seed derivation version;
- source-degree versus realized-voicing evidence;
- fallback reasons;
- requested versus realized density;
- hard range, soft tessitura, and preferred center;
- effective phrase, resolution, and search configuration;
- label-safety policy and per-event safety diagnostics;
- DCT/extension availability and exposure;
- no-chord decisions; and
- any optional local-key posterior or masking condition.

If manifest size becomes a practical problem, move full `events` arrays to a
line-addressable `.melody.jsonl` sidecar while retaining a hash and summary in
the main manifest. The first implementation should prefer one explicit,
machine-readable source of truth over reconstructing alignment from score
tokens.

The target-corpus curation tool must copy the V2 track and melody metadata
when present, while remaining compatible with pre-melody target artifacts.

## 10. Validation

Extend `eda/validate_rendered_corpus.py` with an optional V2 branch. Existing
pad, arpeggio, and no-chord validation must remain valid when no melody field
is present.

### 10.1 Structural checks

For enabled melody output, validate:

- V2 is present exactly once per score block;
- every melody token is a valid note or rest;
- all MIDI values and velocities are valid;
- all onsets and durations are finite and within the song timeline;
- notes do not overlap in the monophonic V2 track;
- source-event indices are valid and monotonically cover any held span;
- note roles and pitch-source values are from the supported vocabularies;
- score events match manifest melody events;
- the melody instrument program matches the manifest; and
- no-chord intervals contain no melody pitch under the default policy.

### 10.2 Harmonic checks

For each non-rest melody event:

- source-degree pitches must match the active `resolve_degrees()` pitch
  classes;
- extension and DCT claims must match the source chord tuple;
- realized-voicing claims must match the corresponding `VoicedChord` data;
- chromatic roles must have a permitted approach, passing, neighbor, or
  enclosure explanation;
- a withheld condition must not intentionally target its withheld
  differentiator on a strong position; and
- melody validation must never mutate or re-interpret the chord label.

### 10.3 Regression and isolation checks

For the same source, seed, voicer order, and bass-active context:

- melody-disabled V0/V1/V9 output remains unchanged from the current behavior;
- enabling melody does not change V0 chord pitches or selected voicer;
- enabling melody does not change V1 pitch/program or V9 inclusion/feel;
- enabling melody does not change arpeggio scheduling or fallback diagnostics;
- separate melody RNG streams produce repeatable output; and
- source label hashes, event count, duration tokens, and chord boundaries are
  identical across melody conditions.

## 11. Test plan

Add focused tests rather than making the full corpus depend on melody output.

### 11.1 Unit tests

Create `tests/test_melody.py` covering:

1. candidate enumeration from `resolve_degrees()` for all triads and active
   extension slots;
2. collision-merged degrees and merged-role provenance;
3. absolute tonic/root pitch-class handling for non-C tonics;
4. optional mode and explicit `scale_pcs` precedence;
5. controlled chromatic approach, passing, neighbor, and enclosure proposals;
6. profile range and maximum-leap enforcement;
7. rest and hold behavior;
8. phrase reset at no-chord events;
9. source-event mapping for notes held across playable chord boundaries;
10. DCT/extension exposed and withheld conditions;
11. the 70% song-level inclusion gate and its `0.0`/`1.0` boundaries;
12. deterministic inclusion and melody output for equal seeds; and
13. explicit fallback diagnostics when no legal candidate remains.

Include rare examples such as:

- altered dominant events with `b9`, `#9`, `#11`, or `b13`;
- `sus2` plus ninth collision;
- diminished plus `#11` collision;
- bare `1` and `5` triads;
- dense four-extension events; and
- explicit no-chord events.

### 11.2 Rendering integration tests

Extend `tests/test_rendered_corpus.py` to verify:

- a melody-enabled render contains V2 and its manifest record;
- a requested melody render omits V2 and records a probability-gate omission
  when the seeded 70% inclusion decision rejects a song;
- a melody-disabled render remains backward compatible;
- V0/V1/V9 are invariant under melody enablement;
- mixed pad/arpeggio allocation is unchanged when melody is enabled;
- the V2 track is synchronized with the source timeline;
- the melody metadata and score tokens pair one-to-one; and
- validation accepts both legacy records and melody records.

### 11.3 Property-style checks

For generated test progressions, assert:

```text
chord labels before melody == chord labels after melody
source durations before melody == source durations after melody
all melody MIDI values are in profile range
all note spans are inside the song timeline
all non-rest events have a valid role and source mapping
```

Use fixed seeds in tests. Do not use audio analysis as the only test oracle.

## 12. Evaluation and ablations

The implementation is not complete when a melody string merely parses. Report
symbolic and acoustic results separately.

### 12.1 Symbolic metrics

Report by genre, chord type, render mode, and rare-feature stratum:

- note/rest density;
- range and leap distributions;
- repeated-note and phrase-reuse rates;
- chord-tone rate by metric strength;
- stable-extension and DCT exposure rate;
- non-chord resolution rate within a short horizon;
- unresolved chromatic rate;
- held-note and cross-boundary rate;
- cadence completion;
- no-chord violation rate;
- source-degree versus realized-voicing mismatch;
- fallback rate; and
- condition-specific density and exposure.

Compare at least:

1. chord-aware candidate sampling;
2. independent slot sampling;
3. chord tones only;
4. chord tones plus global scale;
5. controlled chromatic candidates; and
6. sequence scoring with phrase/resolution terms.

### 12.2 Recognition and masking experiment

For matched chord labels and timing, render:

1. accompaniment only;
2. sparse high-register melody;
3. neutral melody;
4. dense same-register melody;
5. controlled chromatic melody;
6. oracle accompaniment and melody stems; and
7. estimated separated stems from an explicitly recorded baseline.

Measure independently:

- root, triad, seventh, and full-extension chord accuracy;
- frame and event boundary errors;
- key/context stability;
- melody F0 or multi-F0 quality;
- spectral-overlap or masking proxies;
- source-separation quality; and
- human ratings of salience, clarity, tension, and plausibility.

The original unseparated mix must remain a baseline. Separation results must
record the model, version, and stems and must not overwrite symbolic labels.

### 12.3 Leakage controls

All variants made from the same base progression must remain in one dataset
split. Exposure condition, melody density, and register must be sampled
independently of rare-chord quotas. Otherwise a recognizer could learn that a
rare label always has a particular melody pitch or density instead of learning
the chord evidence.

## 13. Phased implementation order

### Phase 0 - Contracts and isolation

- Add optional tonal-context fields and parsing rules.
- Add `MelodyEvent` and `MelodyProfile` data contracts.
- Add `MelodyGenerationConfig`, including the random-walk decoder and
  label-safety controls.
- Add stable child-seed helpers.
- Add unit tests for contracts, source-degree enumeration, and no-chord
  boundaries.
- Confirm that melody-disabled rendering is unchanged.

### Phase 1 - Neutral symbolic melody

- Implement `melody_module.py` candidate generation.
- Implement sixteenth-grid rhythm and phrase segmentation.
- Implement the seeded, bounded chord-centered random walk.
- Implement `neutral` with chord-tone, scale-tone, rest, hold, and controlled
  approach/passing candidates.
- Add V2 serialization and compact manifest provenance.
- Add structural validation and rendering integration tests.

### Phase 2 - Exposure conditions

- Add `dct_exposed` and `dct_withheld`.
- Add source-versus-realized extension diagnostics.
- Add fallback and feasibility counts.
- Add rare-chord stratified symbolic metrics.

### Phase 3 - Instrument and interference conditions

- Add `lead-mid-neutral`, `lead-mid-dense`, and high sparse profiles.
- Add `chromatic` and `dense_overlap`.
- Update Java melody humanization behavior.
- Add matched-stem export or sidecar support.
- Keep masking costs disabled by default until an audio baseline exists.

### Phase 4 - Corpus and curation integration

- Generate matched melody variants without changing the base labels.
- Keep all variants in the same split.
- Extend `tools/curate_target_corpus.py` to preserve V2 and melody metadata.
- Add curation coverage for conditions, profiles, density, exposure, and
  fallback cases.
- Validate source hashes and score/MIDI/manifest mappings for curated samples.

### Phase 5 - Audio experiments

- Render accompaniment-only and full-mix controls.
- Add oracle-stem measurements.
- Evaluate one or more source-separation baselines as optional experiments.
- Report recognition, melody extraction, masking, and separation metrics as
  separate outcomes.

## 14. Acceptance criteria

The first implementation phase is accepted only when:

1. `melody_condition=none` preserves existing rendering behavior.
2. A requested melody condition applies exactly one seeded song-level inclusion
   decision, defaulting to 70% inclusion.
3. A fixed seed produces byte-identical inclusion, melody score, and metadata.
4. Different conditions preserve the same chord labels, source hashes, timing,
   voicer choices, V0, V1, and V9 decisions.
5. Every included melody note has valid range, timing, role, pitch-source, and source
   event provenance.
6. Omitted songs contain no V2 track and retain an auditable omission reason.
7. No-chord events are synchronized rests and phrase resets by default.
8. The MVP decoder is the seeded chord-centered random walk, and its effective
    decoder is recorded in the manifest.
9. Transparent-policy events satisfy per-timepoint source-degree/NCT resolution
    checks, while stress-policy events are explicitly marked as robustness
    conditions.
10. Non-diatonic and extension-rich events can produce legal candidates without
    a per-chord hard key reset.
11. A melody note may cross playable chord boundaries only with explicit
    source-event mapping and valid duration.
12. DCT/extension exposure and withholding are measurable rather than inferred
    from final audio.
13. Core and stress melody variants are distinguishable in manifests and
    evaluation, with matched accompaniment-only controls.
14. The validator reports melody-specific failures without weakening existing
    chord, arpeggio, bass, percussion, or no-chord checks.
15. Focused unit and integration tests cover both new behavior and regressions.

Realism and recognition utility remain empirical outcomes. The generator should
ship with a controlled realism envelope and explicit variants, not with a
claim that one melody policy is universally correct.
