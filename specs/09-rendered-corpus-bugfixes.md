# Spec 09 - Rendered Corpus Fidelity and Voicing Bugfixes

- **Status:** required bugfix specification
- **Applies to:** `gen/`, `render.py`, `chord_module.py`, `bass_module.py`, and
  `voicing/`
- **Parents:** specs 07 and 08
**Purpose:** make the rendered score corpus a trustworthy realization of its
target labels without reducing the existing voicing diversity.

## 1. Problem statement

The generated labels are sufficiently varied for the 250,000-event target
corpus, and the voicing policies produce useful register, spacing, voice-leading,
and shape variation. The current rendered artifacts are nevertheless not safe
training data because the render boundary contains four correctness defects:

1. `root_interval` is tonic-relative in the labels but is treated as an
   absolute pitch class by the chord voicing path. The bass path adds
   `song.tonic_pc`, so the two tracks can disagree about the chord root.
2. Guitar shape lookup is coarse and can select a shape containing a degree
   that is not active in the requested chord.
3. The rendered `Engine` does not receive the bass-active and jazz root-omission
   context required by the jazz policies.
4. Score blocks are numbered in lexicographic filename order without a durable
   source-file mapping.

These are label-fidelity bugs, not reasons to redesign the learned harmonic
distributions or remove the existing voicing policies.

## 2. Measured baseline

The implementation must preserve the following corpus inputs while fixing the
rendered outputs:

| Genre | Label files | Chord events | Score blocks |
|---|---:|---:|---:|
| Jazz | 1,250 | 125,000 | 1,250 |
| Pop/rock | 1,250 | 125,000 | 1,250 |

The current artifacts show:

- `tonic_pc != 0` for 1,158 jazz songs and 1,151 pop/rock songs.
- The declared label root occurs in the chord track for only 33.5% of jazz
  events and 29.4% of pop/rock events.
- The bass pitch class agrees with the label formula for 100% of events, which
  confirms that the mismatch is in the chord voicing path.
- 29,913 jazz events (23.9%) and 27,684 pop/rock events (22.1%) contain a
  pitch class outside the engine's own requested chord set. These extras occur
  in approximately 72.6% of jazz-guitar and 69.6% of pop-guitar events.
- The rendered chord track contains the engine-relative root in 100% of events,
  indicating that the documented jazz rootless policy is not active.

## 3. Normative pitch-coordinate contract

### 3.1 Label coordinates

`ChordEvent.root_interval` remains a relative label field:

```text
absolute_root_pc = (active_tonic_pc + chord.root_interval) % 12
bass_pc = (absolute_root_pc + chord.bass_interval) % 12
```

For the current `Song` schema, `active_tonic_pc` is `song.tonic_pc`. If
event-level modulation is added later, the event's active tonic must be supplied
by the song/context adapter. It must never be inferred from `root`, `bass`, or
`harte`.

The human-readable fields remain diagnostic derivatives and are not voicer
inputs.

### 3.2 Voicing coordinates

The voicing layer must receive the absolute root pitch class explicitly while
retaining the relative label in `ChordEvent`. The preferred implementation is:

1. Add `root_pc` (or an equivalently named absolute-root field) to the runtime
   `CandidateGenParams`.
2. Set it once per event from the active tonic and `root_interval`.
3. Expose the same value in the engine context for policy hooks and diagnostics.
4. Use it for every pitch-class calculation that produces or validates MIDI
   pitches, including:
   - shared engine role pitch classes;
   - DCT pitch-class conversion;
   - piano and synth candidate generation;
   - guitar shape realization;
   - doubling pitch classes;
   - emitted role/pitch validation.

No voicer may use `chord.root_interval` directly as an absolute pitch class.
Relative `root_interval` use remains valid for harmonic-model state,
root-invariant chord types, and rootless-collision vocabulary lookup.

The engine must assert or raise a clear error if the runtime absolute root is
missing or outside `0..11`. It must not silently fall back to
`root_interval`.

### 3.3 Label-to-output validation

For each event, derive the active degree pitch classes from the absolute root
using the tables in spec 07. Apply the existing collision-merge and omission
policies before validating the result.

The emitted chord candidate must satisfy:

```text
emitted_chord_pcs
    subset of
active_degree_pcs plus permitted duplicate/omission roles
```

The combined texture must satisfy:

```text
emitted_chord_pcs union {rendered_bass_pc}
    contains every non-omitted active degree
```

The fifth remains omittable only under the existing major/minor policy. A
rootless jazz chord may omit the root from the chord track only when the
rootless gate in section 4 passes and the bass track supplies the root.
Extensions and DCTs must remain subject to specs 07 and 08; a bass note does
not count as extension retention.

## 4. Runtime context and jazz root omission

### 4.1 Explicit bass-active state

The render pipeline must use one explicit `bass_module_active` value for both
modules:

- `True` when a bass track is emitted, including pad-collapse cases where the
  bass line is still rendered on the selected pad instrument.
- `False` for a deliberately chord-only render.

`ChordModule` must pass this value into the engine context. The context must
also carry the absolute root value defined in section 3.

The default for a chord-only API call may remain `False`, but the score
rendering path must not rely on an omitted/default context value.

### 4.2 Jazz gate

When `song.genre == "jazz"` and `bass_module_active` is true, the engine must
enable the jazz root-omission gate. For other cases, root omission must remain
disabled unless a policy explicitly defines an equivalent gate.

The existing three conditions remain hard:

1. A bass module is active.
2. `bass_interval == 0`.
3. The rootless pitch-class set does not create an exact alternate-root
   collision, except for the documented branch-B extended-chord path.

The implementation must preserve:

- no root omission on inversions;
- complete quality-bearing shell for rootless seventh chords;
- DCT exposure requirements for rootless voicings;
- policy-specific root-omission probabilities and section overrides.

The output diagnostics should record whether the root was omitted, retained, or
retained because a gate condition failed. This is required for corpus auditing,
not for changing the probability model.

## 5. Guitar candidate sound-set integrity

### 5.1 Exact candidate eligibility

The guitar shape library may keep a coarse index for performance, but coarse
lookup is not sufficient for admission. Before a candidate is returned to the
engine, it must pass all of the following:

1. Every sounding role is an active degree, an allowed duplicate of an active
   degree, or a role explicitly permitted by the current omission ladder.
2. Every realized pitch class is in the active requested pitch-class set.
3. A collision-merged degree is validated against the merged sounded pitch
   class, not treated as two required voices.
4. Any omitted role is recorded in `extensions_dropped` or the corresponding
   existing omission diagnostic.
5. No unrequested extension may be introduced by a derived or hand-authored
   shape.

The implementation may either:

- index shapes by the full chord-type tuple and then apply omission variants; or
- retain the `(triad, seventh)` index but require an exact post-realization
  role/pitch-class subset check.

The second check is mandatory in either design. A shape that merely contains
the requested roles is not valid if it also sounds an inactive role.

### 5.2 Shape-library audit

Run the exact candidate validator against every hand-authored and derived
shape at every supported root anchor. Invalid shapes must be removed or
corrected rather than hidden by a rendering fallback.

The existing guitar omission ladder remains valid, with these constraints:

- it may remove only roles allowed by specs 02 and 05;
- it may never add a role;
- it may never remove the DCT;
- every selected drop is logged with song, event, chord type, shape, and role;
- existing dataset-wide drop budgets remain in force: less than 2% for pop
  guitar and less than 3% for jazz guitar.

### 5.3 Regression example

An event with `(triad="major", seventh="7", ninth="N")` must not select a
shape whose realized roles include `9th` or whose pitch classes include the
major-seventh chord's unrequested ninth. In particular, a
`gapfill-('major', '7', 'b9', ...)` shape must not be eligible for that event.

## 6. Deterministic score ordering and provenance

### 6.1 Numeric ordering

`render_directory()` must sort `song_<integer>.json` files by the parsed
integer, not by the lexical filename string:

```text
song_0.json, song_1.json, song_2.json, ..., song_1249.json
```

Invalid filenames and duplicate numeric IDs must fail before rendering. The
source ordering must be identical for jazz and pop/rock generation commands.

### 6.2 Manifest

Each rendered score file must have a sidecar manifest, for example
`jazz_scores.txt.manifest.json`, containing one record per score block:

```json
{
  "ordinal": 0,
  "source_file": "song_0.json",
  "source_id": 0,
  "source_sha256": "...",
  "genre": "jazz",
  "tonic_pc": 1,
  "bpm": 94,
  "num_chords": 100,
  "voicer_family": "piano"
}
```

The manifest is the authoritative pairing mechanism. The score block ordinal
must equal the manifest ordinal and the manifest source hash must match the
input label file used for rendering. The score text must continue to contain
the existing `START_SONG_N` / `END_SONG` delimiters.

### 6.3 Reproducibility

Corpus rerendering must use an explicit seed. The command, seed, source
directory, output path, and generator revision must be recorded in the manifest
or an adjacent checked-in generation record. A failed event must fail the
render command; it must not be skipped or replaced with a success-shaped
fallback.

## 7. Analysis and validation tooling

Update the existing analysis utilities so that they:

1. Read `gen/target-jazz-labels/` and `gen/target-pop-rock-labels/`.
2. Pair events through the manifest rather than numeric assumptions.
3. Compute expected pitch classes from `absolute_root_pc`.
4. Analyze the chord track and combined chord-plus-bass texture separately.
5. Report root retention, extension retention, DCT exposure, unrequested
   pitch classes, permitted omissions, bass correctness, and mapping errors.
6. Report metrics by genre, voicer family, chord type, tonic, and relaxation
   level.

The validator must exit nonzero on any hard invariant failure and must print
the source filename, score ordinal, event index, label, rendered MIDI, and
failure category for each reported violation.

## 8. Required implementation tests

Add or update tests for the following cases:

### 8.1 Coordinate conversion

- A `Db:maj7` label with `tonic_pc=1` voices as an absolute Db-root chord,
  not a C-root chord.
- A non-C tonic with an inversion produces the expected absolute bass pitch
  class.
- A C-tonic event remains backward-compatible with existing relative-root
  unit fixtures.
- Every supported voicer family uses the same absolute root for candidate
  generation and doubling.

### 8.2 Rootless context

- Jazz with an active bass can select a rootless voicing when all three gate
  conditions pass.
- Jazz without an active bass retains the root.
- Inversions retain the root even when a bass module is active.
- Collision and branch-A/branch-B behavior remains consistent with spec 04.

### 8.3 Guitar integrity

- A major-seventh event cannot select a b9-bearing shape.
- Every realized guitar candidate has no unrequested pitch class.
- Allowed omission candidates contain no additional roles.
- DCT-bearing candidates never drop the DCT.
- Pop and jazz extension-drop budgets are counted from selected candidates,
  not from rejected candidates.

### 8.4 Ordering and provenance

- `song_10.json` follows `song_9.json`, not `song_1.json`.
- Every score block has exactly one manifest record.
- Manifest hashes and source metadata match the rendered input.
- Duplicate or malformed numeric filenames fail before writing output.

## 9. Rerender and acceptance criteria

After implementation, rerender both target directories without modifying the
label JSON files:

```text
gen/target-jazz-labels/     -> gen/jazz_scores.txt
gen/target-pop-rock-labels/ -> gen/pop_rock_scores.txt
```

The rerender is accepted only when all of the following hold:

### 9.1 Hard fidelity invariants

- 1,250 score blocks and 125,000 events per genre.
- Zero manifest pairing errors.
- 100% bass pitch-class correctness.
- Zero unrequested chord-track pitch classes for every voicer family.
- 100% absolute-root correctness in the combined chord-plus-bass texture,
  subject only to the documented rootless/inversion rules.
- 100% DCT presence and exposure for DCT-bearing events.
- 100% extension retention for free-placement voicers.
- Guitar extension drops remain below the existing 2% pop / 3% jazz budgets,
  with no DCT drops and complete logs for every drop.
- Zero low-interval-limit, duplicate-MIDI, or unsorted-MIDI violations.

### 9.2 Corpus and diversity invariants

- The label event counts, genre split, and label JSON hashes are unchanged.
- Triad, extension, and root distributions remain within the existing
  generation-target tolerances in spec 08.
- Every chord type with at least 20 occurrences retains at least 8 normalized
  shapes and normalized entropy of at least 0.6, unless the type has fewer
  than 8 physically legal realizations under its declared policy.
- Adjacent identical normalized shapes remain a reported metric and must not
  increase materially from the pre-fix baseline.
- Existing register, ambitus, spacing, and voice-leading summaries must be
  regenerated; any new policy-window or relaxation outlier requires an
  attributed diagnostic rather than silent acceptance.

### 9.3 Perceptual spot check

Before release, listen to a stratified sample containing:

- every voicer family;
- C and non-C tonics;
- root-position and inverted bass events;
- rootless and rooted jazz events;
- ordinary, altered, dense-extension, and rare-triad events;
- the lowest, highest, densest, and largest voice-leading outliers.

The spot check must confirm that the symbolic validation results survive MIDI
or audio rendering: the bass and chord pad agree harmonically, extensions are
audible when labeled, and no guitar grip sounds like an unintended added chord.

## 10. Deliverables

The bugfix implementation is complete only when it includes:

1. The runtime coordinate fix and regression tests.
2. The explicit bass/jazz context wiring and regression tests.
3. The guitar exact-sound-set validation and audited shape library.
4. Numeric score ordering and manifests.
5. Updated analysis/validation tooling.
6. Rerendered score artifacts and manifests for both 125,000-event genres.
7. A post-rerender validation report and the required perceptual spot check.

No training run may consume the pre-fix score files as supervised targets.
