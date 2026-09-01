# Spec 10 - Voicing Candidate-Coverage Mitigations

**Status:** proposed implementation specification
**Applies to:** `voicing/engine.py`, `voicing/candidates.py`, `voicing/guitar/`,
and the six voicer policies
**Parents:** spec 07 (shared engine), specs 01-06 (voicer policies), spec 09
(rendered-corpus fidelity)

This specification defines changes that make valid voicings easier to find. It
does not redefine what counts as a valid voicing. The governing principle is:

> Expand candidate coverage and search completeness; do not weaken musical,
> acoustic, or physical hard gates to make warnings disappear.

When this specification and a voicer-specific specification appear to
conflict, the existing hard invariant wins. A mitigation is not accepted if it
reduces warnings by admitting a candidate that would previously have been
invalid.

## 1. Problem statement

The seed-7 render used for the current investigation completed all 200 songs
without a terminal render failure, but 161 complete policy attempts failed
before a later policy succeeded. Those failures occurred across 129 songs:

| Policy | Failed attempts | Share |
|---|---:|---:|
| `pop-guitar` | 76 | 47.2% |
| `jazz-guitar` | 45 | 28.0% |
| `jazz-synth` | 32 | 19.9% |
| `pop-piano` | 6 | 3.7% |
| `pop-synth` | 2 | 1.2% |
| `jazz-piano` | 0 | - |
| **Total** | **161** | **100%** |

The warning count is a count of failed complete-progression policy attempts,
not a count of unique impossible chords. The chord recorded in a warning is
the first chord at which that policy ran out of candidates. A later policy may
voice that same chord successfully.

The dominant profiles were:

| Chord profile | Failed attempts |
|---|---:|
| `min7(9,11)` | 40 |
| `dom7(9,11,13)` | 31 |
| `dom7(9,11)` | 17 |
| `dom7(#9)` | 10 |
| plain `dom7` | 10 |

The strongest structural signal was extension density: 118/161 failures had
at least three active extension slots, 40/161 had all four, and 91/161
contained `b7 + 9 + 11`. Guitar policies accounted for 121/161 failures, but
the synth and piano probes showed that dense free-placement failures also
occur when the search topology or requested DCT branch is incomplete.

The measurements also show that representative failing labels can have valid
realizations under the existing rules:

- A guarded DCT-repair ablation changed isolated jazz-synth
  `dom7(9,11,13)` trials from 10/96 failures to 0/96.
- An exhaustive role-aware pop-piano probe found a valid full-stack layout for
  every root under the current window, hand-span, LIL, cluster, DCT, and
  post-filter rules.
- Movable guitar shapes have additional realizable `-12` and `+12` register
  variants that the ordinary source does not currently enumerate.

These are search and coverage opportunities, not evidence that the hard gates
are too strict.

## 2. Goals

The implementation must:

1. Reduce recoverable policy failures by finding candidates that already satisfy
   the current contract.
2. Preserve the sound-set, DCT, extension, rootless-shell, spacing, register,
   hand, and guitar-playability invariants.
3. Keep the existing successful-path behavior and random stream stable wherever
   no fallback is needed.
4. Make fallback provenance visible so a reduction can be distinguished from
   an accidental relaxation.
5. Provide a reproducible comparison against the exact seed-7 corpus before
   and after each mitigation phase.
6. Keep instrument-specific realization models intact: free-placement repair
   may be used for synths, but not as a substitute for piano hand placement or
   guitar fingering.

The intended outcome is a higher probability that the selected voicer
successfully voices the requested chord progression when at least one valid
candidate exists. It is not a guarantee that every progression-context conflict
will become realizable.

## 3. Non-goals

The following are explicitly out of scope for warning reduction:

- lowering the low-interval limit;
- lowering or bypassing semitone-cluster spacing;
- lowering piano hand spans or hand windows;
- dropping the DCT;
- dropping an active extension for piano or synth;
- relaxing secondary-differentiator exposure;
- allowing rootless pop-guitar voicings globally;
- bypassing guitar fret, string, mute, or fret-span legality;
- replacing a guitar fingering with an arbitrary pitch-set relocation;
- increasing density without a voice-budget and selection-cost check;
- changing chord-generation probabilities or extension labels;
- treating a bass note as a substitute for a required chord-track extension;
- changing the warning definition to hide failed policy attempts.

The latent `cluster_cap` enforcement defect in `Engine._cluster_ok()` is a
separate correctness change. It must be measured independently and must not be
credited as a candidate-coverage improvement.

## 4. Normative invariants

Every candidate returned by a new or existing source remains subject to the
contracts in specs 07 and 09. The following are repeated here because every
mitigation must preserve them.

### 4.1 Chord content

1. Every active degree remains present after collision merging, except for the
   omissions explicitly permitted by the existing voicer specification.
2. The DCT is never omitted.
3. The DCT and every secondary differentiator satisfy the selected exposure
   predicate after all repair, deduplication, and post-filter operations.
4. A repaired or derived candidate may not introduce an inactive degree.
5. A candidate that loses a required role during pitch deduplication is
   rejected, not silently accepted.
6. Guitar extension omission remains limited to the existing omission ladder.
   It may never remove the DCT, add a role, or exceed the existing dataset
   drop budgets.

### 4.2 Acoustic and register safety

1. The candidate remains inside the active policy window, except for the
   existing explicitly logged window-relaxation ladder step.
2. The low-interval limit remains hard.
3. The active semitone-cluster rule remains hard.
4. The configured cluster cap is not intentionally bypassed by a mitigation.
   Its current enforcement bug is handled in the separate audit described in
   section 11.
5. Centroid and song-level drift rules remain unchanged.
6. The selected DCT branch may change only through the existing relaxation
   ordering or an explicitly tagged DCT repair. Exposure is never removed.

### 4.3 Instrument legality

1. Piano candidates satisfy both hand windows, hand voice caps, hand spans,
   and piano post-filters.
2. Guitar candidates are realizations of a legal shape. Every sounding string,
   fret, mute, root assignment, and fret span remains valid.
3. Synth candidates remain within the synth policy's voice, register, spread,
   cluster, and post-filter rules.
4. Shape metadata, hand metadata, role metadata, and pitch ordering remain
   internally consistent after any transformation.

### 4.4 Root, inversion, and bass coordination

1. Inversions retain the root when required by the current rootless gate.
2. Root omission remains gated by bass activity, root position, collision
   checks, and the policy-specific rootless rules.
3. No mitigation changes the pop-guitar root-presence contract.
4. An active bass track does not make an omitted chord extension valid.

## 5. Terminology and diagnostics

### 5.1 Candidate stages

The engine should distinguish the following stages in diagnostics:

| Name | Meaning |
|---|---|
| `generated` | Candidate emitted by a source or fallback constructor |
| `sound_set` | Candidate satisfies required roles and permitted omissions |
| `window` | Candidate satisfies the active register window |
| `lil` | Candidate satisfies the low-interval limit |
| `cluster` | Candidate satisfies semitone-cluster and cap checks |
| `drift` | Candidate satisfies centroid/register drift checks |
| `dct` | Candidate satisfies DCT and secondary exposure |
| `post_filter` | Candidate satisfies the policy-specific post-filter |
| `selected` | Candidate was passed to normal scoring and selected |
| `repaired` | Candidate was constructed by a tagged repair path |
| `retained` | Candidate came from an earlier hard-clean pool |

`hard_clean` means that a candidate has passed sound-set, window, LIL,
cluster, and drift checks at the current ladder level. It does not imply that
the candidate satisfies the sampled DCT branch or policy post-filter.

### 5.2 Required optional diagnostics

When analysis diagnostics are enabled, each policy attempt should record a
structured record rather than relying on multiprocessing log order:

```json
{
  "song_id": 7,
  "chord_index": 18,
  "policy": "jazz-synth",
  "ladder_level": 0,
  "generated": 412,
  "sound_set": 412,
  "window": 389,
  "lil": 371,
  "cluster": 344,
  "drift": 291,
  "dct": 0,
  "post_filter": 0,
  "repair_inputs": 64,
  "repair_generated": 128,
  "repair_accepted": 3,
  "retained_candidates": 0,
  "dct_role": "13th",
  "sampled_dct_branch": "isolated",
  "octave_offset": 0,
  "extensions_dropped": []
}
```

The fields are diagnostic only and must not consume random draws or alter
candidate ordering when diagnostics are disabled. Counts may be aggregated
per chord or per policy attempt, but the first impossible chord must remain
identifiable.

## 6. Shared candidate-search changes

### 6.1 Candidate provenance

`Candidate.meta` must carry enough information to audit a fallback without
changing the public `Candidate` shape. New metadata keys are:

```python
{
    "candidate_source": "free_placement" | "shape_library" | "...",
    "generation_phase": "normal" | "dct_repair" | "dense_hand" | "guitar_octave",
    "source_rank": int,
    "dct_repair": bool,
    "forced_dct_copy": bool,
    "base_shape_id": str | None,
    "octave_offset": int,
    "extensions_dropped": tuple[str, ...],
}
```

Existing metadata must be preserved when candidates are copied or
deduplicated. A candidate transformation must update metadata rather than
leaving it to describe the pre-transformation candidate.

The engine should use one validation path for ordinary and repaired
candidates. At minimum, the validation path must re-run:

- sorted, unique MIDI validation;
- role-to-pitch-class validation;
- required-role and permitted-omission validation;
- DCT and secondary-role presence;
- active window;
- low-interval limit;
- cluster and cluster-cap checks;
- centroid drift;
- policy post-filter.

### 6.2 Forced DCT octave exposure

The octave DCT branch needs a special case because an exposure copy of the DCT
is not the same thing as an ordinary root or extension doubling.

The implementation must:

1. Represent a forced exposure copy separately from ordinary
   `doubling_roles`, for example with `forced_dct_role` or an equivalent
   `forced_dct_copy` request in `CandidateGenParams`.
2. Allow at most the required DCT copy for the sampled `octave` branch.
3. Count that copy against `max_doublings`, `max_voices`, and all policy voice
   budgets.
4. Mark the copy as `forced_dct_copy=True`; it is not a root-double choice and
   is not evidence that arbitrary extension duplication is allowed.
5. Validate both the original DCT role and its copy after deduplication. If
   either required role is lost, reject the candidate.
6. Preserve the existing restriction against ordinary duplicate copies of
   active extension roles during random doubling exploration.
7. Make the deterministic fallback choose only legal ordinary target roles
   plus the explicitly requested DCT copy. It must not greedily add arbitrary
   extension copies simply because they have an available octave.
8. If no legal forced DCT copy fits, continue through the existing DCT branch
   relaxation. Do not drop the DCT or silently change the branch.

The forced DCT copy may be an octave-exposure copy under spec 07 section 5.2,
but it remains a single required exposure mechanism rather than a general
permission to double the DCT for density.

### 6.3 Retaining hard-clean candidate pools

The relaxation ladder currently regenerates or selects from the pool for the
current level. It must retain earlier candidates that already passed the
current hard gates instead of throwing them away when the ladder later changes
the DCT branch or widens the window.

The required behavior is:

1. After each source call, retain a bounded, stable-order pool of
   `hard_clean` candidates together with the degree signature, omission state,
   window, and voice-budget signature under which they were generated.
2. At a later ladder level, consider a retained candidate only if it is still
   legal under that level's active degrees, window, voice count, cluster rule,
   drift rule, and omission mode.
3. Re-run DCT and post-filter validation at the current level. A candidate
   is not retained merely because it was DCT-clean at an earlier level.
4. Consult retained candidates only after the normal current-level candidate
   set is empty. This preserves the existing successful-path selection and
   random stream.
5. Merge current and retained candidates with stable deduplication. Prefer the
   current-level source, then earlier levels by ascending ladder level, then
   source rank.
6. Do not retain candidates that were rejected for a hard reason, candidates
   with unapproved extension drops, or candidates from a different collision
   or degree signature.
7. Bound the retained pool per level and per chord. The initial implementation
   should use `max_retained_hard_clean=256`, configurable for probes.
8. Retention must not cause a new RNG draw. Candidate order and the normal
   path must remain unchanged when the first candidate set succeeds.

This is an additive search-completeness change. It is not permission to use a
candidate that fails a rule relaxed at the current level.

## 7. Bounded DCT and post-filter repair

### 7.1 Scope

The generic flat-window repair path is meaningful only for voicers whose
candidate pitches are genuinely free-placement pitches. It is enabled for:

- `pop-synth`;
- `jazz-synth` (new opt-in).

It is not enabled for:

- `pop-piano`;
- `jazz-piano`;
- `pop-guitar`;
- `jazz-guitar`.

Piano repair must preserve hand topology, and guitar repair must preserve a
realized fingering. Those instruments use the specialized fallbacks in
sections 8 and 9 instead.

### 7.2 Search completeness

`dct_expose_repair()` and the opt-in post-filter repair must no longer inspect
only `spacing_survivors[:8]`. The implementation must:

1. Inspect a bounded pool ordered by the existing stable candidate order.
2. Default to `max_repair_inputs=64`, configurable for stress tests.
3. Enumerate DCT placements in nearest-anchor order, with deterministic
   pitch and role tie-breakers.
4. For the `top` branch, place the DCT above all other pitches without
   violating the current window or voice budget.
5. For the `isolated` branch, enumerate all legal DCT pitch instances in the
   current window, not just the first isolated option.
6. For the `octave` branch, use the explicit forced-DCT-copy mechanism from
   section 6.2 where a copy is required. Repositioning an existing DCT is not
   a substitute when the selected predicate requires octave exposure.
7. Enumerate bounded combinations of secondary-differentiator placements
   instead of greedily selecting the first option for each role. The initial
   implementation should use `max_secondary_assignments=32`.
8. Stop at a bounded total repair budget, defaulting to 256 generated variants
   per chord and ladder level.
9. Pass every repaired candidate through the shared validation path before it
   enters the scoring pool.
10. Reject a repaired candidate when `dedup_sorted()` removed a required role,
    when a role's pitch class changed, or when the role metadata no longer
    matches the pitch.

Repair may rearrange only the DCT and secondary differentiators, except for
the explicitly required DCT exposure copy. It may not rewrite the entire
voicing or bypass the previous candidate's instrument-independent hard rules.

### 7.3 Repair ordering

Repair is lazy and fallback-only:

1. Generate and filter the ordinary free-placement pool.
2. If no candidate survives but at least one spacing/drift-clean candidate
   exists, run bounded DCT repair.
3. If DCT-clean candidates exist but all fail the synth post-filter, run the
   bounded post-filter repair.
4. If repair produces no valid candidate, continue the ordinary relaxation
   ladder.
5. A repaired candidate is scored and selected through the same normal engine
   path; repair is not an automatic choice.

The repair path must not run when the ordinary candidate pool already contains
a valid candidate. This keeps the common path and its RNG behavior unchanged.

### 7.4 Policy changes

`jazz-synth` must opt into the guarded repair path with an explicit policy
flag, for example:

```python
extra = {
    "dct_repair_ok": True,
    "max_repair_inputs": 64,
    "max_secondary_assignments": 32,
    "max_repair_variants": 256,
}
```

`pop-synth` retains its existing opt-in and adopts the same bounded controls.
No piano or guitar policy may receive this flag as a shortcut.

## 8. Pop-piano dense-layout fallback

### 8.1 Rationale and trigger

The normal contiguous or beam-based hand split can miss valid full-stack
layouts. This fallback addresses topology starvation, not an unrealistic
label. It is used only when:

- the policy is `pop-piano`;
- the normal source has no valid candidate at the current level; and
- the chord has at least three active extension slots, or the diagnostics show
  that ordinary hand partitioning exhausted without a candidate.

It is not added to the ordinary candidate pool when the normal source already
has a valid result.

### 8.2 Role-aware enumeration

The fallback must generate bounded hand partitions rather than assuming that
the sorted roles form one contiguous left-hand prefix and one contiguous
right-hand suffix.

The implementation must:

1. Keep collision groups together as one sounded pitch while retaining their
   merged role metadata.
2. Treat root/3rd-or-sus/7th as core roles and 9th/11th/13th plus secondary
   differentiators as tension roles for partition preference.
3. Enumerate bounded assignments of core and tension roles to LH/RH, including
   non-contiguous assignments. Preference for core roles in LH and exposed
   tensions in RH is a scoring bias, not a hard replacement for the hand rules.
4. Enumerate octave placements for each partition inside the existing
   `lh_window` and `rh_window`.
5. Enforce `lh_max_voices`, `rh_max_voices`, `max_hand_span`, total voice cap,
   active window, LIL, cluster, cluster cap, centroid drift, DCT exposure,
   secondary exposure, and the existing pop-piano post-filter.
6. Preserve the existing hand metadata in the resulting `Candidate`.
7. Use a bounded deterministic search, with an initial
   `max_dense_layout_candidates=512` before shared filtering.
8. Pass valid layouts to the normal cost and softmax selection. The fallback
   must not pick the first feasible layout unconditionally.

The fallback must not lower the 13-semitone semitone-cluster threshold, widen
hand spans, raise hand caps, omit an extension, or apply arbitrary flat-window
DCT repair. The measured role-aware probe found valid layouts under the
existing constraints, so those relaxations are unnecessary and unsafe.

### 8.3 Dense-layout acceptance fixture

The test fixture for `pop-piano` must include the seven-role
`dom7(9,11,13)` stack at all 12 roots. It must find at least one valid layout
for each root under the current hard rules. The fixture must separately record
whether the selected DCT branch is `top` or `isolated`; it must not accept a
candidate merely because a different branch was silently substituted.

## 9. Guitar coverage fallbacks

### 9.1 Octave-shifted movable realizations

`voicing/guitar/model.py::realize()` already accepts `octave_offset`. The
candidate source must expose the additional register realizations as a
fallback, not as peers to the normal shape source.

The required order is:

1. Existing movable and open shapes at `octave_offset=0`.
2. Existing generic relaxation levels for offset-zero, non-dropped shapes.
3. Movable shapes at `octave_offset=-12` and `octave_offset=+12`, in that
   order, through the same unchanged hard filters.
4. Only after these generic alternatives are exhausted, enter the existing
   guitar extension-omission ladder.

An implementation may use a phase-aware source cache instead of literally
running separate loops, but it must preserve this preference ordering.

For every offset candidate:

- open-only shapes are rejected for nonzero offsets;
- `realize()` must return a valid fingering;
- all fret, string, mute, window, span, low-interval, cluster, root, DCT,
  and exact sound-set checks remain active;
- the base shape ID and offset are retained in metadata;
- the public `shape_id` is stable and unambiguous, for example
  `E-barre-dom9@oct-12`;
- round-trip diagnostics retain root fret, muted strings, sounding strings,
  extension-drop state, and shape distance;
- offset-zero candidates remain preferred whenever they produce a valid
  candidate.

An octave offset is a coverage expansion only. It is not permission to shift a
shape outside the physical model or to treat arbitrary pitch transposition as
a playable guitar realization.

### 9.2 Alternate retained-root string assignments

`voicing/guitar/derive.py::_try_assignment()` currently reserves the first
available sounding string for the root. The derivation search must add bounded
alternate assignments:

1. When the root is required, try each eligible sounding root string allowed
   by the shape family before assigning non-root roles.
2. Preserve the existing non-root string-subset and role-permutation search.
3. Prefer the current lowest-root assignment and existing shape IDs, then
   alternate root strings by stable string order.
4. Generate alternates at library-build time or in an explicitly cached
   derivation step, not as an unbounded per-chord brute-force search.
5. Tag alternate shapes with their retained-root string and derivation rank.
6. When root omission is active, do not invent a retained-root assignment;
   preserve the existing rootless shell rules.
7. Run exact role/pitch-class sound-set validation after realization.

This coverage change is especially valuable for inversions and dense shapes
where a lower non-root string is valid but the first available string cannot
carry the retained root without consuming the useful register. It must not
be implemented by weakening fret-span, string-legality, root-retention, or
rootless-shell rules.

### 9.3 Guitar omission accounting

Offset and alternate-assignment candidates are generic coverage candidates and
must be exhausted before extension drops. The existing omission priority and
budgets remain unchanged:

- `pop-guitar`: less than 2% of emitted chords;
- `jazz-guitar`: less than 3% of emitted chords.

Every drop remains attributable to song, chord index, policy, shape, and role.
The addition of coverage candidates must not make an unrequested extension
appear in a shape or make a dropped role disappear from diagnostics.

## 10. Reproducibility and RNG safety

The new search paths must be deterministic for a fixed seed and input.

### 10.1 Normal path

- Do not add RNG calls before a normal successful candidate set is selected.
- Do not change the order of existing candidates on the normal path.
- Do not consult fallback candidates while a normal candidate is available.
- Diagnostics must be observational and must not consume RNG state.

### 10.2 Fallback path

Fallback-only enumeration should be deterministic and preferably use no RNG.
If a fallback source requires randomized sampling, use an isolated RNG derived
from stable inputs:

```text
fallback_seed = stable_hash(
    generator_seed, song_id, chord_index, policy_id, phase, ladder_level
)
```

The derivation must not use Python's process-randomized `hash()`. The fallback
RNG must not be the main engine RNG. Candidate selection after a valid fallback
pool exists still uses the engine's normal selection mechanism.

### 10.3 Stable ordering

All bounded searches must use stable tie-breakers: source phase, source rank,
role order, pitch tuple, shape ID, and metadata rank. Re-running the same
seed with the same revision must produce byte-identical voicing output and
diagnostics.

## 11. Separate `cluster_cap` correctness audit

`Engine._cluster_ok()` currently returns through `semitone_cluster_ok()` for
ordinary candidates before applying the configured `cluster_cap`. As a result,
the configured cap may not be enforced for candidates without exemption
metadata.

This is not a warning-reduction technique. It must be handled as a separate
change with its own baseline:

1. Capture seed-7 metrics before changing the effective cap.
2. Add a regression test showing that an ordinary candidate over the cap is
   rejected and an explicitly exempt candidate follows only its policy
   exemption.
3. Make direct clash-aware constructors use the same cap-aware validation.
4. Re-run the exact corpus with all candidate-coverage mitigations disabled
   and the cap fix enabled.
5. Report any warning increase or distribution shift separately.
6. Do not accept a candidate-coverage phase based on warnings that disappear
   only because the cap was bypassed.

The cap fix may increase warnings while improving density realism. That is an
expected measurement possibility, not a reason to lower the cap.

## 12. Rollout phases

Each phase is feature-gated or independently revertible and must be measured
against the same seed-7 manifest.

### Phase 0 - Baseline and instrumentation

- Preserve the current implementation.
- Record policy-attempt failures, first-failure chord, ladder level, candidate
  stage counts, DCT branch, extension drops, and fallback source.
- Confirm that diagnostics do not alter output or RNG state.

### Phase 1 - Shared DCT and pool hardening

- Implement explicit forced DCT-copy handling.
- Remove arbitrary extension duplication from the deterministic doubling
  fallback.
- Add sound-set revalidation after every deduplication and repair.
- Add bounded hard-clean pool retention.
- Expand synth DCT/post-filter repair beyond eight inputs.
- Enable guarded repair for `jazz-synth`.

### Phase 2 - Pop-piano topology fallback

- Add the role-aware dense hand-layout source.
- Keep it fallback-only and preserve every current piano hard rule.
- Validate the all-root dense-stack fixture.

### Phase 3 - Guitar source coverage

- Add movable-shape octave offsets as fallback candidates.
- Add alternate retained-root string assignments.
- Audit shape IDs, round trips, exact sound sets, and omission budgets.

### Phase 4 - Independent cluster-cap audit

- Fix and measure effective `cluster_cap` enforcement separately.
- Reconcile direct constructors and policy exemptions.

### Phase 5 - Corpus decision

- Re-run the exact 200-song seed-7 corpus.
- Compare warning reduction and all realness metrics.
- Enable only phases that pass the acceptance criteria in section 13.

## 13. Tests and acceptance criteria

### 13.1 Unit and regression tests

Add or extend tests in the existing test modules:

| Area | Required coverage |
|---|---|
| `tests/test_engine_unit.py` | forced DCT copy; no arbitrary extension duplication; repair pool larger than eight; secondary combinations; deduplication rejection; pool retention; stable fallback ordering |
| `tests/test_pop_synth.py` | repaired candidates retain DCT, extensions, spread, and post-filter validity |
| `tests/test_jazz_synth.py` | guarded repair is enabled; dense `dom7(9,11,13)` fixtures recover without dropping roles |
| `tests/test_pop_piano.py` | all-root dense-stack hand feasibility; hand caps/spans; DCT and cluster preservation |
| `tests/test_pop_guitar.py` | offset metadata and round trip; open-shape exclusion; exact sound-set validation; omission budget |
| `tests/test_jazz_guitar.py` | alternate root-string assignments; rootless shell and inversion integrity |
| `tests/test_rendered_corpus.py` | seed reproducibility, no new terminal failures, output contract and diagnostics |

Tests must use the existing test runner and fixtures. Do not add a new testing
framework solely for this specification.

### 13.2 Hard acceptance gates

An implementation phase fails if any of the following occurs:

- a non-guitar voicer drops an active extension;
- any voicer drops the DCT;
- a repaired candidate fails exact role/pitch-class validation;
- a rootless inversion violates root retention;
- a candidate violates LIL, cluster, cap, hand, fret, string, or window rules;
- a guitar extension-drop budget is exceeded;
- the same input and seed produce different output across repeated runs;
- terminal rendering failures are introduced;
- normal-path output changes solely because diagnostics or an unused fallback
  was enabled.

### 13.3 Warning and realness gates

On the exact seed-7 corpus, enabled mitigation phases must satisfy:

1. Total failed policy attempts are strictly below the 161 baseline, or the
   phase is rejected as ineffective.
2. Songs requiring a fallback do not increase above the 129 baseline.
3. Each phase reduces failures in its targeted fixture or provides a documented
   progression-context explanation for no reduction in the full corpus.
4. The first-policy success count does not decrease unless the change is
   explicitly accepted as a policy-order change. This specification does not
   propose such a change.
5. Extension retention, DCT branch distribution, rootless-shell integrity,
   inversion integrity, voice count, ambitus, centroid/register drift, and
   template diversity are reported against the baseline.
6. Guitar fret span, root-fret distribution, fret drift, shape diversity, and
   offset usage are reported separately for each guitar policy.
7. A warning reduction achieved by a hard-gate violation is a failure even if
   the total warning count improves.

The initial project target is a 20-30% reduction in failed policy attempts
without a hard-gate regression. This is a target for tuning and prioritization,
not permission to relax a gate when the target is missed. The minimum pass
criterion for a phase remains a strict reduction against the same baseline
with all invariants intact.

### 13.4 Reporting format

Every corpus comparison must include:

| Category | Metrics |
|---|---|
| Reliability | failed policy attempts; songs needing fallback; attempts per song; first-policy successes |
| DCT | exposure branch counts; DCT failures by policy; repair inputs and accepted repairs |
| Content | active-extension retention; DCT retention; rootless-shell and inversion integrity; guitar drops |
| Spacing/register | LIL violations; semitone clusters; effective cluster-cap violations; window relaxation; centroid range |
| Piano | hand-window occupancy; hand spans; hand voice caps; dense-layout fallback usage |
| Guitar | offset usage; root-string assignment usage; fret span; root fret; fret drift; shape diversity |
| Voice leading | VL distance distribution; transition distance; template/signature diversity |
| Reproducibility | seed, generator revision, manifest hash, and repeated-run equality |

The report must distinguish:

- an ordinary candidate;
- a retained candidate;
- a DCT/post-filter repair;
- a dense-hand fallback;
- a guitar octave or alternate-assignment candidate;
- a guitar extension-drop candidate.

## 14. Implementation map

The expected implementation surfaces are:

| File | Change |
|---|---|
| `voicing/engine.py` | candidate provenance, hard-clean retention, forced DCT-copy parameters, bounded repair orchestration, diagnostics |
| `voicing/candidates.py` | explicit DCT doubling, bounded DCT/secondary search, deduplication-safe repair, role-aware piano fallback |
| `voicing/policy.py` | explicit bounded repair and fallback controls in `extra` |
| `voicing/voicers/jazz_synth.py` | opt into guarded DCT repair |
| `voicing/voicers/pop_synth.py` | adopt bounded repair controls without changing hard rules |
| `voicing/voicers/pop_piano.py` | expose dense-layout fallback configuration |
| `voicing/guitar/model.py` | preserve and validate octave-offset metadata |
| `voicing/guitar/library.py` | fallback offset candidate phase, exact validation, stable shape metadata |
| `voicing/guitar/derive.py` | alternate retained-root string assignments |
| `voicing/spacing.py` / `voicing/engine.py` | separate cluster-cap correctness fix and tests |
| `tests/test_engine_unit.py` | shared-engine regression coverage |
| `tests/test_pop_piano.py` | dense-layout regression coverage |
| `tests/test_pop_guitar.py`, `tests/test_jazz_guitar.py` | guitar coverage and legality |
| `tests/test_jazz_synth.py`, `tests/test_pop_synth.py` | synth repair and content contracts |
| `tests/test_rendered_corpus.py` | exact-corpus comparison and reproducibility |

## 15. Decision summary

The safe mitigation set is:

1. Correct forced DCT doubling so an explicit DCT exposure request does not
   collide with the ordinary extension-deduplication rule or trigger arbitrary
   extension duplication.
2. Search a larger, bounded DCT-repair pool and bounded secondary-role
   combinations, then revalidate the complete sound set.
3. Enable that guarded repair for `jazz-synth`, where isolated measurements
   show the strongest direct opportunity, and retain it for `pop-synth`.
4. Retain earlier hard-clean candidates when later ladder levels change only
   branch or register conditions.
5. Add a role-aware, fallback-only pop-piano hand-layout search.
6. Add fallback-only movable-guitar octave/register realizations.
7. Add bounded alternate retained-root string assignments to the guitar shape
   library.
8. Instrument rejection stages and measure the `cluster_cap` correctness fix
   independently.

The unsafe mitigation set is any change that makes a warning disappear by
removing DCT exposure, dropping required extensions, admitting low-register
clusters, bypassing piano or guitar physical constraints, or globally changing
root omission. Those changes would lower the warning count by reducing label
fidelity and voicing realism rather than by making the search more complete.
