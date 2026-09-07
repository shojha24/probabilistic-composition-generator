# Spec 14 - Chord-Aware Melody Generation

- **Status:** implementation plan; not yet implemented
- **Applies to:** a new `melody_module.py`, `render.py`, `instruments.py`,
  `eda/validate_rendered_corpus.py`, `HumanizedMidiRenderer.java`, tests,
  target-corpus curation, and documentation
- **Parents:** specs 07, 08, 11, 12, and 13
- **Research basis:** `melody_docs/melody_research_report.md` and
  `specs/14.5-melody-research-justification.md`

This document turns the melody research report into an incremental
implementation plan. It focuses the implementation target on naturalistic
full-mix data: chord-conditioned melodies with varied instruments, rhythms,
registers, and melodic behavior. The target is intentionally beyond a
minimum viable melody generator: phrase-level planning, confidence-aware
local-key context, and corpus calibration are required before the melody
defaults are treated as production-ready. The synchronized multitrack
renderer gives downstream users the option to select or remix V0, V1, V2,
and V9 without requiring the generator to create matched control variants.

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
the source of harmonic truth. Melody is a naturalistic full-mix augmentation
of that label.

The first implementation should use:

1. the active `ChordEvent` and `resolve_degrees()` as the primary pitch
   vocabulary;
2. the song tonic and an optional explicit mode or scale as a weak prior;
3. a sixteenth-note scheduling grid shared with the existing renderer;
4. a seeded, bounded chord-centered random walk as a transparent baseline,
   followed by a phrase-level sequence scorer/beam decoder as the target
   production path;
5. seeded child random streams that cannot perturb voicing, bass, percussion,
   or arpeggio decisions;
6. one naturalistic melody policy whose sampled instruments, rhythms, registers,
   and melodic behaviors are recorded, including an optional collapse to the
   selected chord instrument; and
7. a separate `V2` track with machine-readable source-event and role
   provenance, exported through the synchronized multitrack output.

Per-chord key estimation is not allowed as a hard reset. The target decoder
must support confidence-aware local-key inference as a phrase-level latent
context with a persistence cost. When the progression is ambiguous, the
decoder must fall back to the global tonic/chord-centered candidates rather
than forcing a local key.

When melody is requested for a corpus or directory render, apply one
deterministic **corpus-wide inclusion quota** with a default target of
**70%**. For `N` eligible source songs and a requested `melody_percent`, select
exactly the nearest whole-song count:

```text
min(N, max(0, floor(N * melody_percent / 100 + 0.5)))
```

This matches the existing percussion and mixed pad/arpeggio allocation
behavior. Select the quota using stable numeric source ordering and a
dedicated deterministic `melody-inclusion` stream so worker scheduling cannot
change which songs are included. Selected songs receive generated melody
events; unselected songs receive synchronized V2 rest tracks and an auditable
`omission_reason=corpus_quota`. This is an exact quota, not an independent
per-song Bernoulli probability. An explicit `melody_condition=none` remains a
hard disable and bypasses the quota. `melody_percent` must be configurable
from `0.0` through `100.0`, with `70.0` as the default.

Direct single-song rendering has no corpus quota. It must accept the
corpus-planner's resolved `melody_included` decision (defaulting to included
for an explicit naturalistic single-song request) rather than silently
claiming that a corpus percentage was enforced.

The research supplement separates evidence-backed design principles from
repository engineering defaults. In particular, it does not justify a
universal CT/NCT ratio, melody density, phrase length, or GM-instrument range.
Initial values must be configurable, included in the manifest, and treated as
naturalistic generation defaults rather than universal realism targets.

This specification now resolves most of the previously open implementation
details: data contracts, candidate roles and provenance, tonal-context
behavior, range/tessitura policy, rhythm and phrase defaults, bounded search,
resolution lookahead, deterministic streams, naturalistic behavior profiles,
manifests, validation, and phased integration. It remains an implementation
contract and plan; the melody runtime is not yet implemented.

### 1.1 Beyond-MVP implementation target

The implementation must progress beyond a note-by-note greedy/random-walk
generator before the naturalistic melody corpus is considered complete. The
random walk remains a deterministic baseline and regression reference, but the
main implementation target includes the following three capabilities.

#### Phrase-level sequence planning

Generate and score complete phrases or configured phrase windows rather than
committing permanently to each onset in isolation. A bounded beam (or an
equivalent top-k phrase search) should retain multiple partial candidates and
score:

- active chord-degree and extension compatibility;
- metric stability and duration choices;
- interval continuity, contour, and tessitura;
- rests, holds, density, and phrase-level repetition;
- approach and departure intervals for supported non-chord tones; and
- phrase endings, cadence direction, and the next harmonic context.

Phrase planning is still symbolic and deterministic. It does not imply a
learned neural decoder. The effective beam width, candidate cap, phrase
window, and tie-breaking seed must be recorded in the manifest. The planner
must preserve all hard source-event and monophonic constraints.

#### Confidence-aware local-key inference

Infer a temporary tonal center and scale context over a phrase or sliding
window when the surrounding progression provides sufficient evidence. Local
key context may contribute soft candidate and phrase scores for scale tones,
passing motion, non-chord tones, and cadential direction. It must not:

- relabel or reinterpret a `ChordEvent`;
- override an explicit `scale_pcs` value without an explicit policy;
- force a key decision at every chord change; or
- make a scale tone outrank an active chord degree solely because it is
  diatonic.

The inference must expose confidence and persistence/transition costs. Low
confidence or unstable windows must use the global tonic and active chord as
the fallback context. The selected context, confidence, and fallback reason
must be included in melody provenance so local-key behavior is auditable.

#### Extensive corpus calibration

Before finalizing naturalistic defaults, render a fixed, representative
calibration corpus across genres, chord strata, melody profiles, instruments,
seeds, and inclusion states. Use symbolic metrics, score inspection, and
listening to tune the profile weights and decoder defaults. Calibration must
inspect at least:

- note, rest, hold, and duration density;
- chord-tone, extension, scale-tone, and supported NCT rates;
- NCT resolution success and unresolved-tension fallbacks;
- leap, repeated-note, contour, cadence, and phrase-reuse distributions;
- hard-range, tessitura, and register coverage;
- instrument/profile coverage and realized melody inclusion;
- no-chord violations, source-event mapping, and fallback rates; and
- representative full-mix and independently selected sidecar renders.

Calibration is generator tuning, not model training, source separation,
masking analysis, or the deferred Billboard comparison. Its purpose is to
establish a controlled naturalistic behavior envelope and to prevent the
default profiles from being accepted solely because the score parses. The
calibration corpus, effective configuration, summary metrics, and any manual
adjustments must be reproducible and documented.

The implementation may land these capabilities in phases, but they are
committed scope for the beyond-MVP melody target. A melody implementation
that only provides the random walk is an intermediate milestone, not the
finished naturalistic generator.

### 1.2 ACR label hierarchy and non-interference invariant

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

Melody may naturally expose chord tones, extensions, passing tones, neighbors,
and other supported behaviors, but those choices are downstream
audio/symbolic content only. The generated melody must retain the source chord
labels, event boundaries, and durations. The multitrack output makes the
resulting roles independently selectable without requiring separate matched
renders.

### 1.3 Per-timepoint label safety

There are two different guarantees:

1. **Symbolic label preservation is hard.** For every timeline position, the
   active source interval and its complete `ChordEvent` tuple are unchanged.
   A melody event must reference the source event(s) it covers, and a held
   note may not silently become a new chord event.
2. **Audio ACR stability is not guaranteed.** A MIDI-level rule cannot
   guarantee the same recognizer output across soundfonts, mixes, instruments,
   and model front ends. Naturalistic output should be musically plausible and
   chord-conditioned, but it must not be described as audio-level label
   transparency.

The default `naturalistic` label-safety policy applies these symbolic proxies
at every source event:

- strong-position melody onsets use an active source degree or stable
  extension, unless they are a short, explicitly resolved NCT;
- every NCT has a role, target, and resolution within the configured
  `nct_resolution_horizon`; an unsupported sustained chromatic pitch is not
  naturalistic;
- a held note that becomes non-chordal after a playable boundary is labeled
  as a suspension/hold and must resolve within the same horizon;
- no melody pitch is emitted during a default no-chord interval; and
- melody remains on V2 and never contributes its pitch to V0, V1, V9, or the
  source label tuple.

The naturalistic policy may include chromatic, dense, or same-register
material when those behaviors are musically supported by the local chord,
phrase, register, and resolution context. The selected behavior, any
fallback, and source-event mapping must be recorded in the manifest. The
policy preserves the source label and timeline but does not claim that every
full mix will be equally easy for ACR.

### 1.4 Full-mix output and part selection

The naturalistic training artifact is a full mix containing the existing
accompaniment plus the generated V2 melody. Spec 13.5 exports the same render
as synchronized mixed, chords, bass, percussion, and, once V2 is enabled,
melody tracks. Consumers may train on the mixed file or choose any subset of
the exported parts and remix them for a particular experiment.

The melody implementation therefore does not need to generate a separate
accompaniment-only control or matched melody variants. It must instead:

- preserve the immutable `ChordEvent` label and source-event timeline;
- keep V0, V1, and V9 decisions unchanged when V2 is enabled;
- record the selected melody instrument, rhythm, register, behavior counts,
  and generation seed; and
- keep V2 independently inspectable and mechanically pairable with the other
  tracks.

Track selection is an output-consumer decision, not a second melody-generation
path. No audio-level ACR stability claim may be inferred from the symbolic
label-preservation checks.

### 1.5 Naturalistic coverage

Increasing the corpus beyond 250,000 chord events can improve long-tail
coverage, but raw event count is not a substitute for varied melody content.
The corpus should accumulate independent variation across melody instrument,
rhythm, register, density, contour, rests, holds, chord-tone roles,
extensions, and supported non-chord behaviors.

The 70% melody inclusion target is an exact nearest-whole-song corpus quota,
not an independent probability. Record the requested percentage, eligible
song count, target count, and realized count in the corpus manifest. Do not
require matched controls or a factorial cross-condition balance for the first
implementation. Additional generation is most useful when it adds new chord
progressions and naturalistic melody realizations rather than duplicating one
melody pattern.

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
- plan phrases with a bounded sequence scorer/beam rather than relying only
  on note-by-note decisions;
- use confidence-aware local-key context as a soft phrase-level prior with a
  safe global/chord-centered fallback;
- vary chord-tone, extension, scale-tone, chromatic, rhythmic, register, and
  contour behavior without creating separate matched conditions;
- provide sparse, moderate, active, high-register, and mid-register
  realizations within one naturalistic distribution;
- preserve exact source-event timing and label provenance;
- keep accompaniment behavior unchanged when melody is disabled or enabled
  with the same source seed; and
- provide a separately inspectable melody stem and symbolic event map; and
- calibrate decoder and profile defaults against a reproducible representative
  corpus before declaring the naturalistic path production-ready.

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

Source separation, masking models, and audio recognizers are outside the first
implementation. If used later, they must consume the original mix and explicit
stems rather than overwrite symbolic labels.

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
omitted by the voicer, the melody may still target it as a naturalistic
behavior, but the manifest must distinguish:

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
    rhythm_weights: Mapping[str, float]
    behavior_weights: Mapping[str, float]
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
    local_key_policy: str = "confidence_aware"
    local_key_window_bars: int = 8
    local_key_persistence_cost: float = 1.0
    local_key_confidence_floor: float = 0.5
    label_safety_policy: str = "naturalistic"
    masking_cost_enabled: bool = False
    melody_collapse_probability: float = 0.25
```

These defaults are repository-level engineering choices, not universal
musical measurements. Validate positive integer limits at construction and
restrict `decoder` to `chord_centered_random_walk` or `sequence_beam`.
Restrict `local_key_policy` to `none` or `confidence_aware`; the latter is
the required policy for the calibrated naturalistic corpus. The random-walk
decoder is the transparent bootstrap default, while the calibrated
naturalistic path must use `sequence_beam` with the configured local-key
policy unless a manifest explicitly identifies an intermediate baseline.
Restrict `label_safety_policy` to `naturalistic`. Record the effective
configuration in the render manifest. Validate
`melody_collapse_probability` as a finite value from `0.0` through `1.0`.
This probability is evaluated once per included melody song after the V0 chord
instrument has been selected; `0.25` matches the existing bass pad-collapse
default.

The initial catalog should contain at least:

| Profile | Intended register | Initial use |
|---|---|---|
| `lead-high-sparse` | above most chord voicings | sparse, singing melody |
| `lead-mid-neutral` | overlaps the upper accompaniment range | ordinary augmentation |
| `lead-mid-active` | chord-register overlap | more active rhythms and contour |

Program numbers and exact weights should be centralized in
`instruments.py`, validated at startup, and selected with a deterministic
child RNG. A profile must never modify chord instrument selection.

When melody is included, the selected melody instrument may collapse to the
already-selected V0 chord instrument with
`melody_collapse_probability`. This is a post-selection timbre decision, not a
change to chord voicing or instrument eligibility. A collapse draw of `0.0`
never collapses; `1.0` always collapses. When collapse is not selected, use
the separate melody-only catalog.

### 4.4 Melody-only instrument catalog

The melody catalog is additive and must remain separate from
`CHORD_INSTRUMENTS` and `BASS_INSTRUMENTS`. Do not add the `melody` role to an
existing chord or bass entry, change an existing program number, or change the
weights or eligibility of any non-melody module. A future implementation may
define a separate `MELODY_INSTRUMENTS` tuple or mapping in `instruments.py`;
the existing catalogs remain unchanged.

The catalog separation does not prohibit the explicit collapse path above. A
collapsed melody may reuse the selected V0 chord instrument, including a
program outside `MELODY_INSTRUMENTS`, but must record
`instrument_source=chord_collapse` rather than counting that program as
melody-catalog coverage. The collapse path must preserve the melody profile's
symbolic range, tessitura, timing, and event constraints.

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
| `lead-mid-active` | `overdriven-guitar`, `distortion-guitar`, `trombone`, `tenor-sax`, `synth-lead-square`, `viola` |

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
is allowed when it resolves promptly and the naturalistic profile permits it.

### 5.3 Rare and altered events

The source chord must remain visible in melody diagnostics. For an altered
event, for example `A7(b9)` in a C-tonic progression:

```text
active source pitch classes = A, C#, E, G, Bb
global key scaffold          = C-major, if supplied
```

`C#` and `Bb` may be favored as naturalistic extension or tension targets
without declaring a new D-major key for that event. Neighboring context and
phrase resolution should determine whether the tension is musically
plausible.

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

### 5.5 Local-key context

When `local_key_policy=confidence_aware`, infer tonal context over a phrase
window rather than independently at every chord. Candidate contexts may use:

- the song tonic and explicit mode or `scale_pcs`;
- recurring chord roots and qualities;
- dominant-to-tonic or other cadence evidence;
- phrase boundaries and metric emphasis; and
- persistence from the preceding phrase context.

The inference should produce a bounded context record such as:

```python
@dataclass(frozen=True)
class LocalKeyContext:
    start_sixteenths: int
    end_sixteenths: int
    tonic_pc: int
    scale_pcs: tuple[int, ...]
    confidence: float
    transition_cost: float
    source: str                 # explicit, inferred, global_fallback
```

Use a persistence/transition cost so a local key changes only when the
surrounding progression provides enough evidence. A low-confidence or unstable
window must use `global_fallback`; it must not force scale-based candidates.
An explicit `scale_pcs` remains the configured global scaffold unless an
explicit future policy permits a locally inferred replacement.

Local-key context is a soft scoring feature only. At every onset, an active
source chord degree remains eligible and authoritative even when it is
non-diatonic in the inferred context. A scale tone may supplement the
candidate pool, but may not displace an active chord degree solely because it
belongs to the inferred scale. Record the context, confidence, transition
cost, and fallback reason in the melody manifest and event diagnostics.

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
- active profiles may activate adjacent sixteenth slots;
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
   naturalistic profile behavior;
4. applies a distance/maximum-leap transition weight so nearby candidates are
   preferred without forcing a fixed contour; and
5. samples one candidate, hold, or rest from the seeded melody RNG.

In naturalistic output, chord-tone candidates are preferred and unsupported
non-chord tones are not proposed by default. A supported NCT may be enabled
when it has an explicit target and resolves within the configured horizon.
Profiles may vary how often these behaviors occur, but the generator does not
create a separate robustness condition.

The random walk is the transparent bootstrap decoder because its transition
choices, candidate pool, and failure cases are straightforward to inspect.
It remains selectable as a baseline and regression reference. It is not the
finished naturalistic corpus decoder.

The required beyond-MVP path is a bounded phrase-level `sequence_beam`
decoder, or an equivalent top-k phrase search. Partition the timeline into
configured phrase windows, retain the best partial paths at each onset, and
score complete paths using the terms below. The decoder must look ahead far
enough to evaluate NCT resolution, cadence direction, phrase contour, and the
next harmonic context rather than choosing every note independently.

The sequence score should combine:

```text
+ active chord-degree compatibility
+ DCT/extension preference according to profile behavior
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
2. active chord-degree compatibility, profile behavior, and short-horizon NCT
   resolution;
3. interval/contour continuity, held-note continuity, phrase repetition,
   cadence, and soft tessitura;
4. global-scale fit, realized-voicing overlap, density, and optional masking.

The `sequence_beam` decoder uses a configurable beam width of 32 and keeps at
most 24 octave-resolved candidates per onset by default. These are
performance defaults rather than research measurements; record effective
values in the manifest. The random-walk baseline does not need a beam, but
the same candidate and scoring machinery should be reused so the two paths
can be compared without changing source labels or track integration.

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
    -> melody-instrument
    -> melody-collapse
    -> melody-rhythm
    -> melody-candidate
    -> melody-tie-break
```

The melody streams must not consume the voicing, bass, percussion, instrument,
or arpeggio RNGs. For directory rendering, the `melody-inclusion` stream
drives the deterministic corpus quota; per-song melody streams then derive
from the selected source seed. The `melody-collapse` stream must be consumed
only for the one per-song collapse decision and must not perturb melody-only
instrument selection or note generation. With the same source set, progression
order, render seed, voicing order, profile, condition, inclusion percentage,
and collapse probability, the selected set, melody output, and metadata must
be byte-reproducible.

## 7. Naturalistic melody variation

The required melody condition is `naturalistic`. It is a distribution of
musically supported behaviors rather than a matrix of matched experimental
variants. Sample the following dimensions independently where feasible, while
allowing the active chord and phrase context to constrain the result:

| Dimension | Required variation |
|---|---|
| Instrument | multiple melody-only GM programs selected from the profile catalog, plus controlled reuse of the V0 chord instrument through the collapse policy |
| Rhythm | rests, holds, eighth-note phrases, sixteenth-note activity, and longer tones |
| Register | high, mid, and profile-centered realizations within hard range |
| Harmonic behavior | chord tones, stable extensions, scale tones, passing tones, neighbors, approaches, enclosures, suspensions, and appoggiaturas |
| Contour | repeated tones, stepwise motion, bounded leaps, phrase arcs, and cadential motion |
| Density | sparse, moderate, and active passages controlled by profile weights |

`rhythm_weights` and `behavior_weights` are configurable profile inputs.
Their effective values and realized counts must be recorded in the manifest.
Naturalistic generation may produce a dense or chromatic passage when it is
supported by its profile and local resolution; that passage is not a separate
robustness dataset.

The only required condition values are:

```text
none
naturalistic
```

`none` disables V2. `naturalistic` enables one chord-conditioned melody path.
Future experiments may add named conditions, but they are not part of the
initial implementation contract.

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
    melody_included=True,
    melody_collapse_probability=0.25,
)
```

For directory rendering, add equivalent options:

```text
--melody-condition
    none | naturalistic
--melody-profile
    lead-high-sparse | lead-mid-neutral | lead-mid-active
--melody-percent
    floating-point percentage from 0.0 to 100.0; default 70.0
--melody-collapse-probability
    floating-point value from 0.0 to 1.0; default 0.25
```

`none` is the default and must preserve existing accompaniment behavior.
Melody configuration is independent of `--mode mixed` and its
`--arpeggio-percent`/`--pad-percent` allocation.

For directory rendering with `naturalistic`, compute the exact corpus quota
before dispatching source songs to workers. If a song is not selected, render
no melody events and record `omission_reason=corpus_quota`; do not partially
serialize a melody. Emit a synchronized V2 rest track for the song in the
multitrack output. A `melody_percent` of `100.0` selects every eligible song,
and `0.0` intentionally omits every song while retaining the requested
condition in provenance. The resolved inclusion decision is passed to
single-song workers through `melody_included`.

For an included song, draw melody collapse once after V0 instrument selection.
When the draw is below `melody_collapse_probability`, the V2 prefix uses the
same instrument program as V0 and records `instrument_source=chord_collapse`.
Otherwise, select from the profile's melody-only catalog and record
`instrument_source=melody_catalog`. This decision is independent of the V1
pad-collapse draw, V0 mode allocation, and all melody note-generation draws.

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

When `melody_condition=none`, no V2 track is emitted and existing score
blocks, V0/V1/V9 content, and single-mode/mixed-mode behavior remain
unchanged. For `naturalistic`, an omitted song has no melody events but keeps
its synchronized V2 rest track and block ordinal.

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
    "condition": "naturalistic",
    "profile": "lead-high-sparse",
    "inclusion_percent": 70.0,
    "selection_mode": "corpus_exact_quota",
    "omission_reason": null,
    "instrument": "flute",
    "instrument_program": 73,
    "instrument_source": "melody_catalog",
    "collapsed_to_chord": false,
    "chord_instrument": "acoustic-grand-piano",
    "chord_instrument_program": 0,
    "melody_collapse_probability": 0.25,
    "track": "V2",
    "seed": 12345,
    "note_count": 42,
    "rest_count": 18,
    "held_note_count": 7,
    "chord_tone_count": 24,
    "extension_count": 8,
    "chromatic_count": 4,
    "behavior_counts": {
      "scale_tone": 3,
      "passing": 2,
      "neighbor": 1,
      "approach": 2
    },
    "rhythm_counts": {
      "sixteenth": 8,
      "eighth": 24,
      "quarter_or_longer": 10
    },
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
      "decoder": "sequence_beam",
      "local_key_policy": "confidence_aware",
      "local_key_window_bars": 8,
      "local_key_persistence_cost": 1.0,
      "local_key_confidence_floor": 0.5,
      "label_safety_policy": "naturalistic",
      "masking_cost_enabled": false,
      "melody_collapse_probability": 0.25
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

`instrument_source` must be one of `melody_catalog`, `chord_collapse`, or
`omitted`. For an omitted song, `instrument`, `instrument_program`,
`chord_instrument`, `chord_instrument_program`, and `collapsed_to_chord` may
be `null`; no collapse draw is made.

When `melody_condition=none`, `melody` is `null` or an equivalent explicit
disabled record. When a requested melody is omitted by the corpus quota, retain
an explicit disabled record with `included=false`, the requested condition and
profile, the configured `inclusion_percent`, and
`omission_reason=corpus_quota`. The chosen representation must be consistent
across all manifests.

The corpus-level manifest must also contain an inclusion summary such as:

```jsonc
{
  "melody_inclusion": {
    "requested_percent": 70.0,
    "eligible_song_count": 10,
    "target_song_count": 7,
    "realized_song_count": 7,
    "realized_percent": 70.0,
    "selection_mode": "exact_quota"
  }
}
```

For a direct single-song render, record `selection_mode` as
`single_song_resolved` and do not report a corpus target or realized
percentage.

The manifest must also record:

- source label hash and source ordinal already used by the renderer;
- melody condition and profile;
- requested and realized inclusion state;
- configured inclusion percentage, quota counts, selection mode, and omission
  reason;
- deterministic seed derivation version;
- source-degree versus realized-voicing evidence;
- fallback reasons;
- requested versus realized density;
- hard range, soft tessitura, and preferred center;
- effective phrase, resolution, and search configuration;
- melody instrument source, collapse probability, collapse decision, and the
  selected V0 chord instrument/program;
- label-safety policy and per-event safety diagnostics;
- realized instrument, rhythm, register, density, and melodic behavior
  summaries;
- no-chord decisions; and
- local-key context, confidence, transition cost, and fallback diagnostics;
  and
- any optional masking diagnostic.

When multitrack rendering is enabled, the corpus manifest must also identify
the V2 melody sidecar and the mixed/chords/bass/percussion files according to
spec 13.5. The melody record must not require a separate matched render:
track selection and remixing are performed by the consumer.

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
- `instrument_source` and collapse metadata are valid; when
  `collapsed_to_chord=true`, the V2 program matches the selected V0 chord
  program, and otherwise the V2 program belongs to the melody catalog; and
- no-chord intervals contain no melody pitch under the default policy.

### 10.2 Harmonic checks

For each non-rest melody event:

- source-degree pitches must match the active `resolve_degrees()` pitch
  classes;
- extension and DCT claims must match the source chord tuple;
- realized-voicing claims must match the corresponding `VoicedChord` data;
- chromatic roles must have a permitted approach, passing, neighbor, or
  enclosure explanation;
- naturalistic non-chord roles must have an explicit target and resolution
  where required by the role; and
- melody validation must never mutate or re-interpret the chord label.

### 10.3 Regression and isolation checks

For the same source, seed, voicer order, and bass-active context:

- melody-disabled V0/V1/V9 output remains unchanged from the current behavior;
- enabling melody does not change V0 chord pitches or selected voicer;
- enabling melody does not change V1 pitch/program or V9 inclusion/feel;
- enabling melody does not change arpeggio scheduling or fallback diagnostics;
- separate melody RNG streams produce repeatable output; and
- source label hashes, event count, duration tokens, and chord boundaries are
  identical between melody-disabled and naturalistic-enabled renders.

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
10. naturalistic behavior weights for chord tones, extensions, rests, holds,
    and supported non-chord roles;
11. the exact corpus-wide melody quota, nearest-whole allocation, and its
    `0.0`/`100.0` boundaries;
12. deterministic quota selection and melody output for equal seeds; and
13. explicit fallback diagnostics when no legal candidate remains;
14. phrase-level beam retention, deterministic tie-breaking, cadence scoring,
    and parity of hard constraints with the random-walk baseline; and
15. local-key confidence, persistence costs, explicit-scale precedence, and
    global-context fallback for ambiguous progressions.
16. melody instrument selection, `0.0`/`1.0` collapse boundaries, V0 program
    pairing when collapsed, and RNG isolation from V0/V1/V9.

Include rare examples such as:

- altered dominant events with `b9`, `#9`, `#11`, or `b13`;
- `sus2` plus ninth collision;
- diminished plus `#11` collision;
- bare `1` and `5` triads;
- dense four-extension events; and
- explicit no-chord events.

### 11.2 Rendering integration tests

Extend `tests/test_rendered_corpus.py` to verify:

- a naturalistic melody render contains V2 and its manifest record;
- a naturalistic directory render records a corpus-quota omission and a
  synchronized V2 rest block when the deterministic 70% quota excludes a
  song;
- a melody-disabled render remains backward compatible;
- V0/V1/V9 are invariant under melody enablement;
- mixed pad/arpeggio allocation is unchanged when melody is enabled;
- the V2 track is synchronized with the source timeline;
- the melody metadata and score tokens pair one-to-one; and
- collapsed melodies reuse the selected V0 chord instrument/program while
  non-collapsed melodies use the separate melody catalog;
- validation accepts both legacy records and melody records;
- the sequence-beam path preserves V0/V1/V9 and source-label invariants; and
- local-key context diagnostics are present for inferred and fallback windows.

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

## 12. Evaluation scope

The implementation is not complete when a melody string merely parses. The
initial evaluation is symbolic validation plus inspection of the naturalistic
full-mix output and its synchronized tracks. A full cross-condition ACR
ablation is deferred and is not an implementation acceptance criterion.

### 12.1 Symbolic and corpus metrics

Report by genre, chord type, melody profile, and rare-feature stratum:

- note/rest density;
- rhythm and duration distributions;
- range and leap distributions;
- repeated-note and phrase-reuse rates;
- chord-tone, extension, and supported non-chord behavior by metric strength;
- non-chord resolution rate within a short horizon;
- held-note and cross-boundary rate;
- cadence completion;
- no-chord violation rate;
- source-degree versus realized-voicing mismatch;
- fallback rate; and
- instrument, register, density, and behavior coverage.

Report metrics for both the seeded chord-centered random walk and the
phrase-level sequence decoder. The random walk is the transparent baseline
and regression reference; the calibrated naturalistic corpus must use the
sequence decoder with confidence-aware local-key context. A decoder
comparison is not a model ablation and does not change the source-label
contract.

Calibration is complete only when the selected profile defaults have a
reproducible report covering the metrics above, representative full-mix and
sidecar inspection, realized inclusion/profile/instrument coverage, and
documented adjustments to rhythm, behavior, register, contour, and decoder
weights. Do not accept defaults solely because generated scores parse.

### 12.2 Full-mix and multitrack inspection

Validate and inspect:

1. the full V0/V1/V2/V9 mix;
2. the synchronized V2 melody track;
3. the chords, bass, and percussion sidecars;
4. naturalistic instrument, rhythm, register, and behavior variation; and
5. melody-disabled output as a regression reference.

The mixed file and sidecars are the available experiment controls. A consumer
can select accompaniment, melody, or any other track combination without
requiring the generator to emit matched variants. Any later audio
recognition, listening, or masking study should record which tracks were
selected and how they were mixed.

### 12.3 Deferred model comparison

When model-level evaluation is undertaken, the planned comparison is:

1. a model trained on the preexisting Billboard data;
2. Billboard data plus the chord corpus; and
3. Billboard data plus the chord corpus with naturalistic melody included.

This comparison is separate from melody generation and does not require a
full cross-condition study. It should use the multitrack files to document
which parts were included, while preserving the source chord labels and
manifest provenance.

## 13. Phased implementation order

### Phase 0 - Contracts and isolation

- Add optional tonal-context fields and parsing rules.
- Add `MelodyEvent` and `MelodyProfile` data contracts.
- Add `MelodyGenerationConfig`, including decoder selection, phrase-search,
  local-key, and label-safety controls.
- Add stable child-seed helpers.
- Add unit tests for contracts, source-degree enumeration, and no-chord
  boundaries.
- Confirm that melody-disabled rendering is unchanged.

### Phase 1 - Naturalistic symbolic melody

- Implement `melody_module.py` candidate generation.
- Implement sixteenth-grid rhythm and phrase segmentation.
- Implement the seeded, bounded chord-centered random walk.
- Implement `naturalistic` with chord-tone, scale-tone, rest, hold, and
  supported approach/passing candidates.
- Add V2 serialization and compact manifest provenance.
- Add structural validation and rendering integration tests.

### Phase 2 - Phrase planning and tonal context

- Implement the bounded `sequence_beam` phrase decoder using the same
  candidate and scoring machinery as the random-walk baseline.
- Implement phrase-window local-key inference with confidence, persistence
  cost, and global-context fallback.
- Add deterministic beam tie-breaking and provenance for retained/selected
  phrase paths.
- Add unit and integration tests for phrase cadence, NCT lookahead, local-key
  transitions, ambiguous progressions, and source-label invariance.
- Keep `chord_centered_random_walk` available as a baseline and regression
  reference.

### Phase 3 - Naturalistic variation

- Add melody-only instrument selection across the curated catalog.
- Add deterministic per-song melody-to-chord-instrument collapse with the
  configured probability and provenance.
- Add profile-specific rhythm, register, density, contour, and behavior
  weights.
- Add source-versus-realized extension diagnostics.
- Add fallback counts and naturalistic coverage metrics.

### Phase 4 - Multitrack integration

- Add `lead-mid-neutral`, `lead-mid-active`, and high sparse profiles.
- Update Java melody humanization behavior.
- Extend spec 13.5 sidecar output for V2.
- Keep masking costs disabled by default until an audio baseline exists.

### Phase 5 - Corpus calibration and curation

- Generate naturalistic full-mix songs without changing the base labels.
- Extend `tools/curate_target_corpus.py` to preserve V2 and melody metadata.
- Add curation coverage for profiles, instrument source, collapse state,
  rhythm, register, density, behavior, and fallback cases.
- Validate source hashes and score/MIDI/manifest mappings for curated samples.
- Render a fixed calibration corpus across genres, chord strata, profiles,
  instruments, seeds, and inclusion states.
- Produce a reproducible calibration report with symbolic metrics, sidecar
  checks, representative score/audio inspection, and documented weight
  adjustments.
- Do not designate the naturalistic defaults as production-ready until the
  sequence-beam/local-key path and calibration report are complete.

### Phase 6 - Deferred model evaluation

- Train the planned Billboard baseline.
- Add the chord corpus, then the naturalistic melody corpus, as separate
  incremental training inputs.
- Use the multitrack manifest to record selected parts and render settings.

## 14. Acceptance criteria

### 14.1 Baseline and integration acceptance

The first implementation phase is accepted only when:

1. `melody_condition=none` preserves existing rendering behavior.
2. A requested naturalistic melody condition applies one deterministic
   corpus-wide nearest-whole quota, defaulting to 70% inclusion.
3. A fixed seed produces byte-identical inclusion, melody score, and metadata.
4. Melody enablement preserves the same chord labels, source hashes, timing,
   voicer choices, V0, V1, and V9 decisions.
5. Every included melody note has valid range, timing, role, pitch-source, and source
   event provenance.
6. Omitted naturalistic songs contain no melody events, retain a synchronized
   V2 rest block, and retain an auditable omission reason.
7. No-chord events are synchronized rests and phrase resets by default.
8. The MVP decoder is the seeded chord-centered random walk, and its effective
    decoder is recorded in the manifest.
9. Naturalistic events satisfy per-timepoint source-degree/NCT resolution
    checks and record any supported non-chord behavior.
10. Non-diatonic and extension-rich events can produce legal candidates without
    a per-chord hard key reset.
11. A melody note may cross playable chord boundaries only with explicit
    source-event mapping and valid duration.
12. Instrument, rhythm, register, density, contour, and melodic behavior
    variation are recorded in the manifest rather than inferred from final
    audio.
13. The mixed score and V2 sidecar are synchronized with the V0/V1/V9
    multitrack output.
14. The validator reports melody-specific failures without weakening existing
    chord, arpeggio, bass, percussion, or no-chord checks.
15. Focused unit and integration tests cover both new behavior and regressions.
16. Included melodies have a deterministic, manifest-recorded collapse decision;
    collapsed V2 uses the selected V0 chord program, while non-collapsed V2
    uses the separate melody catalog, without changing V0/V1/V9.

### 14.2 Beyond-MVP completion acceptance

The naturalistic melody implementation is not considered complete until:

1. `sequence_beam` or an equivalent bounded phrase-level decoder is
   implemented, deterministic, selectable, and used for calibrated
   naturalistic output.
2. The random walk remains available as a transparent baseline and regression
   reference, using the same source-event and label-safety contracts.
3. Confidence-aware local-key inference is implemented over phrase windows,
   includes persistence and confidence handling, and falls back safely for
   ambiguous or unstable progressions.
4. Local-key context never relabels a source chord, overrides explicit
   `scale_pcs` without policy, or displaces an active chord degree solely
   because it is diatonic.
5. Manifests record decoder, beam/search settings, local-key context and
   confidence, fallback diagnostics, and deterministic seed derivation.
6. A reproducible calibration corpus and report demonstrate coverage across
   genres, chord strata, profiles, instruments, collapse states, seeds, and
   melody inclusion states.
7. Calibration includes symbolic distribution checks, phrase/cadence and NCT
   resolution checks, source-event/sidecar validation, and representative
   full-mix inspection.
8. Profile and decoder defaults have documented adjustments based on the
   calibration evidence; parsing success alone is not used as the realism
   acceptance criterion.

Realism and recognition utility remain empirical outcomes. The generator should
ship with a controlled realism envelope and explicit profile/behavior
metadata, not with a claim that one melody policy is universally correct.
