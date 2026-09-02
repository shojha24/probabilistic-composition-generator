# Spec 11 - Generated No-Chord / Harmonic-Silence Events

- **Status:** proposed implementation specification
- **Applies to:** `chord_gen.py`, `target_corpus_gen.py`, `voicing/`,
  `chord_module.py`, `bass_module.py`, `render.py`, and corpus validation
- **Parents:** specs 07, 08, and 09

This specification adds generated no-chord events for automatic chord
recognition (ACR) training. In the generated audio, an `N` event is a
**harmonic rest**: the chord and bass tracks are silent for the event's
duration, while the existing percussion track continues normally.

The implementation must be a post-processing step after ordinary chord
sampling and timing assignment. It must not add a learned silence state to the
Stage 1, Stage 2, or Stage 3 distributions.

## 1. Decision summary

The first implementation uses these defaults:

1. Use the exact uppercase Harte label `"N"`.
2. Add an explicit `is_no_chord` discriminator to every generated event.
3. Represent the absent root, triad, and bass with `null`; keep all four
   extension slots as `"N"`.
4. Replace only common, bare, root-position major/minor events by default.
   This protects rare triad, extension, dense-extension, and inversion
   coverage.
5. Apply the filter after all raw events and their timing metadata have been
   generated.
6. Make the quota-aware 250,000-event corpus contain exactly 2,500 no-chord
   events: 1,250 jazz and 1,250 pop/rock.
7. Use a separate post-processing random stream so harmonic sampling is
   unchanged when the no-chord feature is enabled or disabled.
8. Treat a no-chord event as a hard voicing-continuity boundary. The first
   chord after the boundary is voiced without voice-leading state from before
   the rest.
9. Keep percussion independent of harmonic content. It must use the same
   duration timeline and the same seeded groove decisions whether an event is
   playable or `N`.

The exact target is an **event-count** target because specs 08 and 09 define
the generated corpus in chord events. The implementation must also report the
duration-weighted no-chord rate. A future ACR experiment may choose a
duration-based target, but it must not silently change the meaning of the
current 1% corpus requirement.

## 2. Research findings

### 2.1 Harte and JAMS semantics

The JAMS `chord_harte` namespace defines `N` as a no-chord observation. It is
an ordinary timed annotation with a `time`, `duration`, and string `value`;
`N` is not an unknown chord and is not a chord rooted on the note N.

The relevant references are:

- Harte, Sandler, Abdallah, and Gomez, *Symbolic Representation of Musical
  Chords: A Proposed Syntax for Text Annotations* (ISMIR 2005):
  <https://ismir2005.ismir.net/proceedings/1080.pdf>
- JAMS chord namespace documentation, including the `chord_harte` `N`
  definition and timed observation schema:
  <https://jams.readthedocs.io/en/stable/namespaces/chord.html>

The standard meaning is broader than physical silence: an `N` segment can
also cover a non-harmonic or unannotated region. This project deliberately
narrows the generated realization to a harmonic rest by silencing only the
chord and bass modules. Percussion remains audible and therefore an `N`
event must not be described as a full mix silence.

### 2.2 Existing repository behavior

The current code provides several important precedents and constraints:

- `extract_distributions.py::parse_harte()` maps `N`, `X`, empty values, and
  `None` to `None`.
- `extract_distributions.py::split_into_runs()` terminates a training run at
  each such value. Stage 1, Stage 2, and Stage 3 context therefore never
  crosses a source silence.
- `ChordGenerator.generate()` currently emits a playable event at every
  timestep, then updates `GenState` from that event.
- `TargetChordGenerator.generate()` records every generated event in
  `GenerationQuota`, including feature and spacing state used by later target
  decisions.
- `ChordEvent`, `resolve_degrees()`, `voicing.dct`, and all six voicer
  policies assume a playable triad and integer root/bass intervals.
- `ChordModule.render()` and `BassModule.render()` already produce one
  duration token per source event. `chord_token([], duration)` already has the
  required JFugue rest form, `R{duration}`.
- `PercussionModule` reads event durations, not chord roots, triads, bass
  intervals, or extensions. Its groove can therefore continue across an `N`
  event without a harmonic-module change.
- `eda/validate_rendered_corpus.py` currently parses rests as empty MIDI event
  lists but applies chord invariants to every event. It needs an explicit
  no-chord branch before calling `resolve_degrees()` or calculating root and
  DCT pitch classes.

### 2.3 Replacement-pool feasibility

The current target label artifacts provide enough safe replacement candidates
for the default target:

| Corpus | Events | Bare root-position major/minor candidates | Required `N` events |
|---|---:|---:|---:|
| Jazz | 125,000 | 17,614 | 1,250 |
| Pop/rock | 125,000 | 67,314 | 1,250 |

These counts justify a conservative replacement predicate, but they are not
a permanent guarantee for arbitrary distribution settings. Every exact-mode
run must preflight its eligible pool and fail clearly if the requested count
cannot be met.

## 3. Terminology and counting

- A **raw event** is an event emitted by the harmonic generator before the
  no-chord filter.
- A **playable event** is a raw event with a valid root, triad, bass interval,
  and extension tuple.
- A **no-chord event** is a generated event with `is_no_chord == true` and
  `harte == "N"`.
- An **event budget** counts both playable and no-chord events. Replacing an
  event never changes `num_chords`.
- The **event no-chord rate** is:

  ```text
  no_chord_events / total_events
  ```

- The **duration no-chord rate** is:

  ```text
  sum(no_chord duration_seconds) / sum(all duration_seconds)
  ```

The normative 1% target is the event rate. Both rates must be present in
generation and render reports so ACR experiments can choose the appropriate
exposure measure explicitly.

## 4. Canonical event contract

### 4.1 Playable events

Every generated JSON event must include `is_no_chord: false` once this
specification is implemented. Existing playable fields retain their current
meaning:

```json
{
  "is_no_chord": false,
  "root_interval": 0,
  "triad": "major",
  "bass_interval": 0,
  "seventh": "N",
  "ninth": "N",
  "eleventh": "N",
  "thirteenth": "N",
  "root": "C",
  "bass": "C",
  "harte": "C:maj",
  "duration_token": "w",
  "duration_beats": 4.0,
  "duration_seconds": 2.0,
  "time_start": 0.0,
  "time_end": 2.0
}
```

`root_interval` and `bass_interval` remain tonic- and root-relative for
playable events. `root`, `bass`, and `harte` remain derived/readability fields
and are not voicer inputs.

### 4.2 No-chord events

The canonical no-chord record is:

```json
{
  "is_no_chord": true,
  "root_interval": null,
  "triad": null,
  "bass_interval": null,
  "seventh": "N",
  "ninth": "N",
  "eleventh": "N",
  "thirteenth": "N",
  "root": null,
  "bass": null,
  "harte": "N",
  "duration_token": "w",
  "duration_beats": 4.0,
  "duration_seconds": 2.0,
  "time_start": 0.0,
  "time_end": 2.0
}
```

The following rules are hard:

1. `is_no_chord` must be a JSON boolean.
2. If `is_no_chord` is true, `harte` must be exactly `"N"`.
3. If `is_no_chord` is true, `root_interval`, `triad`, and
   `bass_interval` must be `null`.
4. If `is_no_chord` is true, `root` and `bass` must be `null`.
5. All extension slots must be `"N"`; `null` is not a valid extension token.
6. All duration and timing fields must remain valid and unchanged from the
   event being replaced.
7. If `is_no_chord` is false, `harte` must not be `"N"` and all playable
   harmonic fields must satisfy the existing event contract.

`ChordEvent.from_dict()` must validate this branch and expose an
`is_no_chord` property. It may accept legacy input that lacks the boolean by
deriving it from `harte == "N"`, but newly written generated labels must
always include the boolean. Conflicting values, such as
`is_no_chord: false` with `harte: "N"`, must raise a clear `ValueError`.

`ChordEvent` harmonic fields become optional at the type boundary. Methods
that require a playable event, including `chord_type()` and
`resolve_degrees()`, must reject a no-chord event explicitly instead of
coercing `null` to zero or looking up a fake triad. All callers must branch on
`is_no_chord` before invoking those methods.

The implementation must not use any of these ambiguous alternatives:

- `triad: "N"` with `root_interval: 0`;
- a zero-valued root/bass interval to mean no bass;
- an all-zero playable chord whose `harte` happens to be `"N"`;
- a special `N` triad added to `TRIAD_THIRD_FIFTH`.

Those forms allow an omitted guard to generate a tonic chord or to enter
ordinary voicing logic accidentally.

### 4.3 Timing preservation

The post-processor must copy the selected event and change only the
no-chord discriminator and harmonic fields. It must preserve exactly:

- `duration_token`;
- `duration_beats`;
- `duration_seconds`;
- `time_start`;
- `time_end`;
- event position in the song.

`duration_total_seconds`, `num_chords`, and the final song end time must be
unchanged by no-chord insertion. The filter must not recalculate a timeline
from a modified duration or reorder events.

The current JSON event dictionaries carry the timing fields while
`ChordEvent` is primarily a harmonic runtime view. Any renderer entry point
that accepts a `Song` rather than the original dictionary must nevertheless
retain or receive the corresponding duration token for every event. It must
not silently fall back to `w` for a no-chord event whose source duration is
different.

## 5. Post-processing algorithm

### 5.1 Pipeline boundary

The harmonic generator must be split conceptually into:

```text
generate_playable_events()
    -> assign durations and contiguous timing
    -> return raw event lists

apply_no_chord_filter(raw songs, NoChordConfig)
    -> select replacement indices
    -> copy selected events to canonical N records
    -> return final label event lists
```

The Stage 1-3 loop remains unchanged with respect to harmonic sampling. A
selected event is converted to `N` only after it has been fully generated and
timed. No `N` event is passed to the sampling state, extension history, or
target quota while the raw sequence is being generated.

The shared helper should have an explicit interface equivalent to:

```python
apply_no_chord_filter(
    songs,
    *,
    rate=0.01,
    mode="exact",
    seed=None,
    eligible_predicate=None,
)
```

It must validate the rate, accept either one song or a batch of songs, and
return event lists with the original grouping and ordering. It must be
idempotent: running it on already processed labels must never replace an
existing `N` event again.

### 5.2 Default eligible replacement events

The default predicate is intentionally conservative:

```text
event is playable
event.triad in {"major", "minor"}
event.bass_interval == 0
event.seventh == "N"
event.ninth == "N"
event.eleventh == "N"
event.thirteenth == "N"
```

This selects common bare root-position major/minor events. It does not remove:

- rare triads (`sus2`, `augmented`, `diminished`, `1`, `5`, or `sus4`);
- any upper extension;
- any dense extension tuple;
- any inversion;
- any event that is already `N`.

The predicate is a policy default, not a reason to add a second harmonic
distribution. A future configuration may broaden the pool, but it must provide
an explicit protected-event predicate and prove that all feature quotas remain
valid after replacement.

### 5.3 Exact mode

Exact mode is normative for corpus-producing entry points.

For `N` total events and a requested rate `r`, calculate:

```text
target = floor(r * N + 0.5)
```

At the standard target size:

```text
floor(0.01 * 250000 + 0.5) = 2500
```

Select exactly `target` eligible event indices without replacement. The
candidate list must be in stable `(song_index, event_index)` order before
sampling. A separate, domain-specific RNG must be used for selection; Python's
process-randomized `hash()` must not be used as a priority.

For `target_corpus_gen.py`, apply exact mode independently to each genre batch.
With the standard 125,000/125,000 split this produces exactly 1,250 events per
genre. For custom genre budgets, calculate each genre's target using the same
rounding rule and report the resulting total.

If `target > eligible_count`, raise an error containing the requested count,
the eligible count, the rate, and the affected group. Never consume a rare
event or lower a feature quota as an implicit fallback.

Exact mode does not impose an adjacency restriction in this version. A
replacement can be next to another replacement; the resulting consecutive
rest duration is valid. Any future anti-clustering rule must be explicit,
measured, and allowed to fail rather than silently reducing the exact count.

### 5.4 Probabilistic mode

Probabilistic mode is for a standalone song or an explicitly approximate
generation request. It produces an expected, not guaranteed, corpus rate.

If a batch contains `N` total events and `M` eligible events, sample each
eligible event independently with:

```text
p = rate * N / M
```

This makes the expected replacement count `rate * N`, rather than
accidentally applying the requested total-event rate only to the eligible
subset. If `M == 0` and `rate > 0`, or if `p > 1`, fail with a clear error.
`rate == 0` is a valid no-op.

The public single-song generator may use probabilistic mode because it has no
corpus-wide denominator. The `chord_gen.py` CLI and
`target_corpus_gen.generate_target_corpus()` must gather their raw batch before
applying exact mode. A single call to a low-level one-song API must not be
advertised as an exact 1% guarantee.

### 5.5 RNG and reproducibility

No-chord selection must use a separate RNG stream derived from the generation
seed and a fixed domain tag such as `"no_chord"`. It must not draw from
`ChordGenerator.rng` after each event or interleave with Stage 1-3 sampling.

Consequences:

- raw playable events are identical for the same harmonic seed whether the
  feature is disabled, probabilistic, or exact;
- changing the no-chord rate does not change durations or harmonic samples
  that were not replaced;
- exact selected indices are reproducible for the same input ordering,
  configuration, and seed;
- the selected seed and mode can be audited from the output metadata.

## 6. Natural and quota-aware generation

### 6.1 `chord_gen.py`

The ordinary generator must expose a configurable no-chord rate with a default
of `0.01` and an explicit disable/no-op option. The CLI should provide
equivalents of:

```text
--no-chord-rate RATE       default 0.01
--no-chord-mode exact|probabilistic
--no-chord-off             equivalent to rate 0
```

For a multi-song CLI invocation, generate all raw songs first, group them by
genre when `--random-genre` is active, apply the configured filter to each
group, then write the final labels. The default batch mode is exact. A
single-song programmatic call may opt into probabilistic mode explicitly.

`reconstruct_harte()` must either receive only playable fields or return
`"N"` through an explicit no-chord branch. It must never attempt to construct a
rooted Harte label from nullable fields.

### 6.2 `target_corpus_gen.py`

The target generator must:

1. Generate and time all playable events using the existing target scheduler.
2. Record feature quotas against those raw playable events exactly as today.
3. Check the raw quota targets.
4. Apply the exact no-chord filter to each genre batch.
5. Recompute final feature counts and assert that every target still holds.
6. Write final labels and no-chord metadata.

No-chord insertion must not increment `GenerationQuota.position`, alter
`TargetGenState`, create a pending target state, or count as a triad,
extension, or dense-extension observation. `GenerationQuota.record()` should
reject a no-chord event if called accidentally, making an integration error
visible rather than silently recording `"None"` as a triad.

The safe default replacement predicate cannot remove any feature covered by
spec 08. If a future predicate is broadened, the filter must receive an
explicit protected set or a post-filter quota audit must reject the result.

### 6.3 Distribution extraction

Do not add `N` to any extracted Stage 1, Stage 2, or Stage 3 table. The
existing source behavior is correct: `N` terminates a context run. If a
generated corpus is later transformed into a source annotation corpus, its
`N` records must similarly split runs and must not create transitions such as
`playable -> N -> playable` in the learned harmonic tables.

## 7. Voicing continuity contract

### 7.1 Default boundary behavior

The default policy is **reset after silence**.

For a sequence such as:

```text
playable A, N, playable B
```

the first post-rest chord B is selected as a fresh voicing. The engine must
not use A's candidate, chord type, or voice-leading distance when selecting B.
This follows the same semantic boundary already used by distribution
extraction and reflects the fact that no audible voice-leading occurs during
the rest.

The initial implementation should not expose a second hidden behavior that
sometimes follows A. If a follow-through mode is required later, it must be a
separately named, explicitly configured policy with its own tests and
manifests.

### 7.2 Engine output and state

`Engine.run(song)` must preserve one output slot per input event:

```python
list[VoicedChord | None]
```

For a no-chord event:

- append `None`;
- do not call `Engine.step()`;
- do not run tone selection, DCT calculation, candidate generation, or
  policy post-filters;
- do not record diversity, voice-leading, extension drops, or a voicing
  relaxation;
- increment a separate `silence_count`;
- reset `prev_cand` and `prev_chord` before processing the next event.

`Engine.total_count` and relaxation rates must count playable voicing attempts.
No-chord events are reported separately and must not make a voicing success or
failure rate look better or worse.

Any voicer-specific continuity state must also obey the boundary. In
particular, the guitar home-fret state must be cleared/reset after `N`.
Dataset-level state that is not voice-leading state, such as the pop-synth
spread tally or a shared diversity counter, may continue across the event.

The first post-rest `VoicedChord.vl_distance` must be `0.0`, as it has no
previous emitted voicing. The no-chord slot must never be represented by a
fake `VoicedChord` with empty MIDI; that would pollute DCT, diagnostics, and
diversity consumers.

## 8. Track rendering behavior

### 8.1 Chord module

`ChordModule.render()` must recognize `None` voicing slots and emit a
duration-matched chord rest:

```text
V0 I<program> ... Rw ...
```

It must preserve event alignment in all cached output fields:

- `last_voiced_midis`: `[]` for `N`;
- `last_voiced_roles`: `[]` for `N`;
- `last_voicing_diagnostics`: an explicit `{"no_chord": true}` record for
  `N`.

The duration used for the rest must come from the same event-timing adapter
used for playable tokens, including when the caller supplied a `Song`.

An `N` event must never cause a voicer fallback or `VoicingImpossible`.
If every event in a song is `N`, the module may still select a compatible
instrument/program, but it must emit only rests.

### 8.2 Bass module

`BassModule.render()` must branch before calculating
`root_interval + bass_interval`. For `N`, emit:

```text
R{duration_token}
```

and do not calculate a bass pitch, low-register collision adjustment, or root
pitch class. The bass track must retain exactly one event token per source
event, including a rest for every `N`.

Pad-collapse and instrument selection remain unchanged. A no-chord event is a
rest even when the selected bass instrument is the chord pad instrument.

### 8.3 Percussion module

The percussion algorithm and its random decisions must remain unchanged. It
must continue to:

- read only duration/timing information;
- play the same groove through an `N` event when included;
- preserve its 30% omission behavior and BPM-conditioned feel selection;
- emit synchronized `V9` rests when the track is omitted.

For the same event duration sequence, replacing a playable harmonic event with
`N` must produce byte-for-byte identical percussion output and metadata for a
fixed percussion seed. Percussion must not be made conditional on
`is_no_chord`.

### 8.4 Combined score

For each source event, the rendered score must contain aligned tokens in V0
and V1. At an `N` index both tracks must contain rests and neither may contain
a MIDI note. V9 may contain hits at that same index.

The existing JFugue rest forms (`Rw`, `Rh`, `Rq`, and the other supported
duration tokens) are the canonical output. The Java MIDI renderer must be
smoke-tested with a score containing an `N` event to ensure the rests consume
time without creating notes.

## 9. Validation and provenance

### 9.1 Label validation

Add a no-chord validation pass that checks:

- the explicit discriminator and canonical nullable fields;
- exact `"N"` Harte label;
- valid duration/timing fields;
- contiguous event timing;
- unchanged event count and total duration;
- no duplicate or malformed no-chord records.

The validator must treat no-chord events as valid records, not as parse
failures.

### 9.2 Rendered-corpus validation

Before chord-specific checks, branch on `chord.is_no_chord`:

1. Require the parsed V0 event to be `[]`.
2. Require the parsed V1 event to be `[]`.
3. Do not call `resolve_degrees()`, `compute_dct()`, root checks, extension
   checks, or voicing-window checks for this event.
4. Count the event and its duration in no-chord metrics.

For playable events, retain all existing spec 09 checks unchanged. A note in
either V0 or V1 at an `N` index is a hard validation failure. An event-count
mismatch remains a hard failure even if the mismatch occurs at a rest.

### 9.3 Metadata

Each label song should include a no-chord summary when the feature is enabled:

```json
{
  "no_chord": {
    "enabled": true,
    "rate": 0.01,
    "mode": "exact",
    "event_count": 1,
    "event_rate": 0.0104166667,
    "duration_seconds": 2.0,
    "duration_rate": 0.0125
  }
}
```

The summary is descriptive; the event records remain authoritative. For a
legacy song with no `N` events, the summary may be absent or may report
`enabled: false`.

The render manifest must carry per-record no-chord event and duration counts,
and the top-level manifest/report must carry:

- configured rate and selection mode;
- total no-chord event count and event rate;
- total no-chord duration and duration rate;
- no-chord counts by genre;
- the post-processing seed/domain.

The source hash and event count pairing rules from spec 09 remain unchanged.
Rendering must not inject or remove `N` events; it renders the labels it is
given.

## 10. Required implementation changes by file

| File | Required change |
|---|---|
| `chord_gen.py` | Add the no-chord configuration/filter and separate raw generation from post-processing. Preserve timing, add canonical event construction, and make Harte reconstruction explicitly `N`-aware. Update CLI options and generated metadata. |
| `target_corpus_gen.py` | Apply exact filtering after raw quota accounting and before writing. Keep quota state playable-only and verify final quotas after replacement. |
| `voicing/types.py` | Add the explicit no-chord runtime branch, optional harmonic fields, strict parsing, and playable-only guards. |
| `voicing/engine.py` | Return index-aligned `VoicedChord | None`, skip no-chord events, reset continuity state, and expose separate silence diagnostics/counters. |
| `voicing/voicers/*` | Ensure direct candidate/tone/DCT paths are never called for `N`; clear any voicer-local continuity state at the engine boundary. |
| `chord_module.py` | Render V0 rests for `N` and preserve aligned cached MIDI/role/diagnostic lists. |
| `bass_module.py` | Render V1 rests for `N` before accessing nullable harmonic fields. |
| `percussion_module.py` | No algorithm change. Add regression coverage proving identical timing/groove decisions across `N`. |
| `render.py` | Preserve the three-track score, add no-chord manifest summaries, and keep rendering separate from label post-processing. |
| `eda/validate_rendered_corpus.py` | Validate empty V0/V1 events at `N`, skip chord-only invariants there, and report event- and duration-based rates. |
| `tests/` | Add schema, rate, quota, timing, reset, rest-rendering, validation, reproducibility, and percussion-regression tests. |
| `README.md` | Remove the known-limit statement and document generated harmonic rests, exact target counts, and the distinction between V0/V1 silence and continuing V9 percussion. |
| `voicing/README.md` | Document the `VoicedChord | None` alignment contract and reset boundary. |

`extract_distributions.py` should not be changed for the first implementation;
its existing source-silence boundary behavior is the desired behavior.

## 11. Acceptance criteria

The implementation is complete only when all of the following hold:

1. A standard target generation produces exactly 250,000 events, with exactly
   2,500 `is_no_chord` events and exactly 1,250 in each genre.
2. Every generated `N` record satisfies the canonical schema and preserves
   the replaced event's duration and timing.
3. No selected event contains a rare triad, an active extension, a dense
   extension tuple, or an inversion under the default predicate.
4. All spec 08 rare-triad, rare-extension, dense-extension, root-balance, and
   event-count targets still hold after filtering.
5. Re-running generation with the same seed and configuration produces
   identical labels, selected `N` indices, and metadata.
6. Changing only the no-chord rate does not change the raw harmonic samples,
   durations, or timing of events that remain playable.
7. `Engine.run()` returns one output slot per event; `N` slots are `None`,
   omitted from voicing/DCT/diversity counts, and reset the next chord's
   voice-leading state.
8. The first post-`N` voicing has no pre-`N` voice-leading cost or guitar
   home-fret carry-over.
9. Chord and bass tracks contain duration-matched rests and no MIDI notes for
   every `N`; event positions remain aligned.
10. Percussion output is unchanged for a fixed duration sequence and seed,
    and audible percussion can occur during a harmonic rest.
11. The rendered-corpus validator accepts valid `N` records, rejects any V0 or
    V1 note at an `N` index, and reports both no-chord rates.
12. Exact-mode generation fails clearly when the eligible replacement pool is
    too small; it never silently replaces a protected rare event.

## 12. Rejected designs

### 12.1 Learning an explicit silence state

Rejected for the first release. The source extractor intentionally breaks
contexts at `N`, and adding a synthetic state to the learned transition tables
would change the harmonic model and create transitions whose statistics are
not present in the source corpus. Post-processing gives the ACR model `N`
examples without contaminating the chord distributions.

### 12.2 Per-song exact 1% as the corpus definition

Rejected. A 48-event song cannot represent 1% exactly, and rounding every song
would bias small corpora. Exact mode must select from the full generation
batch; standalone single-song calls may only promise an expected rate.

### 12.3 Duration-weighted selection as the default

Rejected for this release because spec 08 defines the target corpus by event
count. Duration-weighted rate must still be reported. If the ACR training
protocol later defines prevalence by audio frames or seconds, that should be a
new explicit configuration and acceptance target.

### 12.4 A fake empty `VoicedChord`

Rejected. An empty object with a normal voicer ID can be mistaken for a valid
voicing and can contaminate DCT, diversity, voice-leading, and diagnostics.
`None` is the explicit aligned representation of "no voicing produced."

### 12.5 Following voice-leading through the rest by default

Rejected as the initial default. It lets an inaudible pre-rest candidate
influence the first audible post-rest chord and conflicts with the existing
run-boundary semantics. A future follow-through mode must be explicit and
separately audited.
