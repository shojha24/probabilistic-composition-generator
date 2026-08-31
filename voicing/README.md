# Voicing system

## Purpose

The `voicing` package creates MIDI chord voicings.

The package supports six voicers:

| ID | Genre | Instrument | Main realization |
|---|---|---|---|
| `pop-piano` | Pop/rock | Piano | Two-hand piano placement |
| `pop-guitar` | Pop/rock | Guitar | Fretboard shape library |
| `pop-synth` | Pop/rock | Synth | Free pad placement |
| `jazz-piano` | Jazz | Piano | Rootless shell and two-hand placement |
| `jazz-guitar` | Jazz | Guitar | Rootless shell and derived drop shapes |
| `jazz-synth` | Jazz | Synth | Jazz tone set and free pad placement |

Each voicer uses the same engine. A `VoicerPolicy` supplies the instrument and
genre rules.

## Basic process

For each chord, the engine performs these steps:

1. Read the chord event.
2. Resolve the active chord degrees.
3. Select required degrees.
4. Select a DCT, when the chord has one.
5. Create candidate voicings.
6. Reject candidates that fail hard rules.
7. Apply the relaxation ladder when no candidate remains.
8. Score the remaining candidates.
9. Select one candidate with a temperature-controlled softmax.
10. Store the result as a `VoicedChord`.

The previous chord affects the next choice in two main ways:

- The previous MIDI pitches affect voice-leading cost.
- The previous chord type can enable PLR, Cube-Dance, and common-tone bonuses.

The current chord affects tone selection, DCT selection, register, spacing, and
candidate generation.

The engine does not always select the lowest-cost candidate. The softmax keeps
nearby choices possible. A low `tau` value gives more repeatable output. A high
`tau` value gives more variation.

## Chord degrees

`voicing.types.resolve_degrees()` creates the active degrees.

The normal degree names are:

- `root`
- `3rd`
- `5th`
- `7th`
- `9th`
- `11th`
- `13th`

The function applies the collision rules from the specification. A collision
does not remove the required pitch class. It merges the lower role into the
higher role with `merged_from`.

Example:

- A `sus4` chord with an active `11` can store the sus tone as `11th`.
- The resulting degree still records that it carries the `3rd` role.

Code that checks shell integrity must check `merged_from`. It must not check
only the visible role name.

## Pitch-coordinate contract

`ChordEvent.root_interval` is relative to the active song tonic. The engine
converts it once per event:

```text
absolute_root_pc = (song.tonic_pc + chord.root_interval) % 12
bass_pc = (absolute_root_pc + chord.bass_interval) % 12
```

The absolute root is stored in the engine context and passed to every
candidate generator through `CandidateGenParams.root_pc`. Piano, synth, and
guitar candidates, doubling, DCT conversion, and emitted-pitch validation
must use that value. Human-readable `root`, `bass`, and `harte` fields are
diagnostic only. Root-relative intervals remain valid for harmonic state and
root-invariant chord-type calculations.

The engine raises a clear error when the song tonic or runtime absolute root
context is missing or outside `0..11`; it never treats `root_interval` as an
absolute pitch class.

## Shared hard rules

These rules apply to all voicers.

### DCT protection

The DCT is the main differentiating tone of a chord.

- Tone selection must not remove the DCT.
- The omission ladder must stop before it removes the DCT.
- A candidate must contain the DCT.
- The candidate must expose the DCT with the selected exposure predicate.

The available predicates are:

- `top`
- `isolated`
- `octave`

### Spacing

The engine applies the low-interval limit and semitone-cluster rules.

The low-interval limit prevents dense low-register voicings.

The semitone-cluster rule separates pitch classes that are one semitone apart.
Some policies use a documented lower threshold for a controlled exemption.

Policies also cap local density at four notes inside a 12-semitone window.
This allows standard four-note 7th chords while limiting denser stacks.

Each policy also prefers an adjacent spacing floor of 3 semitones. Pairs with
a lower note below MIDI 48 prefer 6 semitones. These are soft scoring costs,
not eligibility gates: realistic 1–2 semitone upper-voice intervals remain
available, especially around labeled extensions.

Voice count is also softly regularized. The first optional doubling is free;
each further extra voice receives a multiplicatively larger cost. Policies
can tune this with `extra["voice_excess_penalty"]` and
`extra["voice_excess_growth"]`. The policy maximum remains a real ceiling, so
unusual dense voicings are still possible when explicitly supported.

The jazz-synth pad uses a more permissive close-cluster setting because its
wide free-placement register must retain all labeled tensions. It still uses
the shared soft spacing preference and DCT checks.

### Register templates

Piano and synth candidate generation uses explicit register templates rather
than moving a selected chord after the fact. The shared families are:

- `balanced`: the original anchor-centered placement;
- `closed`: compact core and upper tones;
- `open` and `shell_spread`: separated foundation and color tones;
- `tension_top`: a clear upper extension landmark;
- `wide`: broad pad spacing;
- `root_spread`: separated root/fifth foundation;
- `rare_feature`: a more distinctive layout for rare triads and dense labels.

Jazz piano adds `jazz_shell`, `jazz_tension_top`, `jazz_open`, and `jazz_rare`
so its third/seventh shell remains in the lower hand while extensions stay in
the upper hand. Guitar uses shape-family metadata instead of free-placement
templates, including compact and rare-feature shape families.

For each chord, a deterministic target rotates with the song seed and chord
index. The target profile gets a bounded generation slice placed ahead of the
full balanced fallback; this prevents deduplication from making alternate
profiles unreachable without sacrificing difficult-chord coverage. Every
candidate still passes the normal window, spacing, DCT, drift, hand, and
fretboard gates, and the target is only a soft selection preference.

### Register anchoring

The engine uses an anchor center for each chord.

The anchor comes from the song and the active section profile. The engine uses
the anchor to:

- select octave assignments;
- limit centroid drift;
- score register distance;
- apply section register shifts.

The whole song is the unit for drift checks. A chord-by-chord check is not a
replacement for a whole-song range check.

### Relaxation ladder

The ladder tries strict rules first. It can then relax permitted constraints,
such as:

- additional doublings;
- additional voices;
- selected spacing limits;
- register bounds;
- DCT branch selection.

The ladder does not remove an extension as a normal generic action. Guitar
shape lookup has a separate extension-drop mechanism.

## Tone selection and root omission

`voicing.select.select_tones()` applies the shared tone-selection rules.

Fifth omission is probabilistic for major and minor triads. The following
triad types keep the fifth because the fifth carries chord quality:

- diminished;
- augmented;
- sus2;
- sus4;
- `5`;
- `1`.

Jazz piano, jazz guitar, and jazz synth use the strict root-omission gate.
Root omission requires all three conditions:

1. The bass module is active.
2. `bass_interval == 0`.
3. The rootless pitch-class set does not form another vocabulary chord under
   another root.

If a condition fails, the engine must not use ordinary root omission.
The score renderer passes `bass_module_active=True` whenever it emits the
bass track, including pad-collapse renders. Chord-only callers may leave it
false. Voicing diagnostics record retained roots, omitted roots, and the gate
failure reason when applicable.

## Voicer policies

### Pop piano

Module: `voicing.voicers.pop_piano`

Pop piano uses two-hand placement. The left hand anchors the lower chord
content. The right hand carries upper chord tones and extensions.

Typical behavior:

- moderate root retention;
- moderate fifth omission;
- wider pop register;
- section-based density;
- piano hand-span limits;
- root and fifth doubling.

Use this voicer for a conventional pop keyboard texture.

### Pop guitar

Module: `voicing.voicers.pop_guitar`

Pop guitar uses the fretboard shape library in `voicing.guitar`.

The library contains:

- hand-authored movable shapes;
- power-chord shapes;
- root shapes;
- derived coverage shapes;
- derived extended shapes.

The candidate must be playable on the configured tuning. The engine checks
fret range, fret span, muted strings, register, spacing, and DCT hazards.

The library keeps a coarse `(triad, seventh)` index for lookup, then performs
exact post-realization validation. Every sounding role must be active (or
explicitly allowed by the omission ladder), every realized pitch class must
belong to the requested chord, duplicate MIDI values are rejected before
deduplication, and inactive extensions cannot be hidden by a coarse shape
match. Selected extension drops are recorded in `extensions_dropped`; the DCT
can never be dropped.

Extension drops are allowed only in the guitar library omission path. Each
selected drop is recorded as `EXTENSION_DROPPED`. The drop tracker supports
dataset-level rate checks.

### Pop synth

Module: `voicing.voicers.pop_synth`

Pop synth uses free placement with no hand or fretboard constraint.

Its section profiles control:

- spread class;
- root doubling;
- root omission;
- maximum voice count;
- anchor shift.

The spread classes are `sparse`, `open`, and `wide`. The policy records the
realized spread class after each committed chord. This feedback helps keep the
dataset distribution near the target proportions.

The lowest voice has an octave-band anchor. This prevents the pad from moving
out of the bass area while its upper voices move upward.

### Jazz piano

Module: `voicing.voicers.jazz_piano`

Jazz piano uses a shell-first texture.

For a chord with a seventh, the shell is the third and seventh. For a chord
without a seventh, the shell is the third and fifth. If the fifth is omitted,
the shell can use the root with the third.

For `sus4` and other collision cases, the shell can use a merged degree. The
merged degree is a valid replacement for the third.

Typical behavior:

- rootless voicings when the strict gate permits them;
- compact left-hand shell;
- extensions in the right hand;
- tight register;
- high voice-leading weight;
- PLR and common-tone preference.

The policy includes a rootful fallback for chords that have no third. This
supports `5` and `1` chord forms.

### Jazz guitar

Module: `voicing.voicers.jazz_guitar`

Jazz guitar uses the shared guitar shape library with jazz parameters.

The implementation uses `drop2_derived` shapes for extended coverage. This is
an explicit construction choice. The reviewed sources do not provide a
complete canonical inventory for extended jazz-guitar shapes.

The policy uses:

- a 45-79 MIDI window;
- a maximum fret of 15;
- a maximum fret span of 4;
- rootless jazz tone selection;
- shape-distance scoring;
- position-continuity scoring;
- strict DCT filtering from the shared engine.

The shape library remains the source of fretboard legality. Do not replace
guitar shapes with free-placement candidates.

### Jazz synth

Module: `voicing.voicers.jazz_synth`

Jazz synth combines jazz tone selection with free pad placement.

The policy uses:

- a 40-91 MIDI window;
- jazz root omission;
- shell-aware scoring;
- wider pad ambitus;
- controlled doubling;
- a lower cluster threshold for the synth policy.

The policy gives the synth more voices than jazz piano. This helps dense
altered chords. The candidate pool is larger for the same reason.

## Guitar shape derivation

`voicing.guitar.derive` creates derived shapes when hand-authored coverage is
not sufficient.

The derivation process:

1. Resolve the chord degrees.
2. Order the degrees.
3. Assign degrees to a valid string set.
4. Try legal open and octave fret positions.
5. Check fret span.
6. Check semitone-cluster violations.
7. Apply the guitar omission ladder when required.
8. Store the result as a root-relative `Shape`.

The derived shape is an auditable construction. It is not a claim that every
shape is a standard published fingering.

## Candidate cost

The engine combines these terms:

- voice-leading distance from the previous candidate;
- register drift from the anchor;
- spacing penalty;
- diversity penalty;
- role and instrument penalty;
- transformation bonuses;
- common-tone bonuses.

The voice-leading distance uses minimum-cost pitch matching. It supports
different voice counts and charges an unmatched penalty.

The role penalty is policy-specific. Examples include:

- piano hand span;
- guitar shape distance;
- guitar fret span;
- synth ambitus;
- shell spacing;
- spread-template deviation.

## Programmatic use

Create an engine with a policy:

```python
from voicing.engine import Engine
from voicing.voicers import jazz_piano

engine = Engine(
    jazz_piano.POLICY,
    {"bass_module_active": True, "section": "verse", "seed": 7},
    root_omission_gate=True,
)
voiced_chords = engine.run(song)
```

Use `root_omission_gate=True` for jazz piano, jazz guitar, and jazz synth.

Use `branch_b_requires_octave_dct=True` for jazz synth when branch-B rootless
behavior is required by the specification.

Each result is a `VoicedChord` with:

- `midi`;
- `roles`;
- `dct_pitch`;
- `hands`;
- `shape_id`;
- `vl_distance`;
- `centroid`;
- `diagnostics`.

Diagnostics include the absolute root, active degree pitch classes, emitted
pitch classes, bass context, root-omission status, DCT exposure, relaxation
level, omitted roles, and any selected guitar extension drops. Directory
renders persist compact summaries of these diagnostics in the score manifest.

## Validation

Run the complete automated suite:

```bash
pytest -q -n 0
```

The suite checks shared engine behavior, pop voicers, jazz voicers, and corpus
smoke coverage.

For additional corpus checks, use the corpus files in
`gen/jazz-labels/`. A useful check must report:

- songs that fail to voice;
- voice-leading mean and standard deviation;
- whole-song centroid range;
- whole-song centroid trend;
- unique signatures;
- rootless and rooted proportions;
- spread and shell distributions;
- cross-voicer signature overlap.

Do not call a voicer complete from corpus generation alone. Calibration,
diversity, drift, and cross-voicer results need separate measurement.

For the target rendered corpora, use the manifest-paired validator:

```bash
python3 eda/validate_rendered_corpus.py \
  --json-out gen/rendered_corpus_validation.json
```

It reads `gen/target-jazz-labels/` and `gen/target-pop-rock-labels/`, uses
the manifest rather than filename assumptions, and reports metrics by genre,
voicer family, chord type, tonic, and relaxation level. It exits nonzero for
source-pairing, pitch-set, bass, DCT, extension, ordering, or spacing
violations.

## Known limits

The current system has these known limits:

- Extended jazz-guitar shapes use an auditable derivation because complete
  canonical coverage is not available in the reviewed sources.
- Statistical calibration targets are policy targets. They are not all fitted
  from a large performance corpus.
- Some dense pop-synth chords can fail in a narrow section and seed context.
  A higher voice limit fixes some cases, but not every case.
- A corpus pass does not prove that every section profile, random seed, and
  chord vocabulary combination will pass.

These limits must remain visible in validation reports.
