# Spec 07 — `Voicer` Interface and Shared Engine

**Status:** authoritative. Specs 01–06 are *modules* that plug into this interface and
override only the parameters and hooks defined here.
**Parent document:** `voicing_algorithm_spec.md` (§0–§8). Where this spec and the parent
disagree, the parent wins on *intent*, this spec wins on *mechanism*.

---

## 1. Purpose

`voicing_algorithm_spec.md` specifies six voicers (2 genres × 3 instruments). Building
six independent implementations would guarantee six different register-drift bugs and
six different extension-exposure bugs. Instead:

- **One engine.** Candidate enumeration, voice-leading distance, register anchoring,
  disambiguation-tone enforcement, diversity accounting, and softmax selection are
  implemented exactly once, here.
- **Six policy modules.** Each voicer supplies a `VoicerPolicy` — a parameter block plus
  three optional hooks (`candidate_source`, `tone_selection_bias`, `post_filter`).

This mirrors the AAM-paper's own element-generator design, where the chord-pad generator
and the arpeggiation generator differ in temporal pattern but *share the same underlying
voicing method* (AAM-paper §4.3).

---

## 2. Input contract

### 2.1 Song object

Produced by `chord_gen.py::ChordGenerator.write_song`; see `gen/jazz-labels/*.json` and
`gen/pop-rock-labels/*.json`.

```jsonc
{
  "genre": "jazz" | "pop_rock",
  "tonic_pc": 0,          // 0-11, absolute pitch class of the key center
  "bpm": 120,
  "num_chords": 48,
  "chords": [ ChordEvent, ... ]
}
```

### 2.2 `ChordEvent`

```jsonc
{
  "root_interval": 0,     // 0-11, semitones above tonic_pc
  "triad": "major",       // closed 8-way vocabulary, see §2.3
  "bass_interval": 0,     // 0-11, semitones above the *root* (inversion, pre-decided)
  "seventh":    "N" | "7" | "b7" | "bb7",
  "ninth":      "N" | "9" | "b9" | "#9",
  "eleventh":   "N" | "11" | "#11",
  "thirteenth": "N" | "13" | "b13",
  "root": "C", "bass": "C", "harte": "C:maj"
}
```

`root_interval` and `bass_interval` are the normative fields. `root`, `bass`, and `harte`
are human-readable derivatives and **must not** be parsed by a voicer.

### 2.3 Degree → semitone table (normative)

Triad members, from `extract_distributions_new.py::QUALITY_TO_TRIAD`:

| `triad` | third | fifth |
|---|---|---|
| `major` | 4 | 7 |
| `minor` | 3 | 7 |
| `diminished` | 3 | 6 |
| `augmented` | 4 | 8 |
| `sus4` | 5 | 7 |
| `sus2` | 2 | 7 |
| `5` | — | 7 |
| `1` | — | — |

Extension slots, from `extract_distributions_new.py::INTERVAL_TO_SEMI`:

| slot | token → semitones above root |
|---|---|
| `seventh` | `7`→11, `b7`→10, `bb7`→9 |
| `ninth` | `9`→2, `b9`→1, `#9`→3 |
| `eleventh` | `11`→5, `#11`→6 |
| `thirteenth` | `13`→9, `b13`→8 |

`N` = slot inactive.

### 2.4 Corpus shape (measured over `gen/*-labels/`, 9600 chord events)

These numbers set realistic expectations for what the voicers actually receive:

- Triads: `major` 6322, `minor` 2377, `diminished` 321, `sus4` 288, `augmented` 123,
  `5` 93, `1` 54, `sus2` 22.
- `bass_interval == 0` in 8819/9600 events (91.9%); inversions are real but a minority.
- Extension profiles: no extension 4773, `b7` only 3325, `7` only 557, `bb7` only 345,
  and a long tail of ~500 events carrying a 9th, 11th, or 13th — including full
  `b7/9/11/13` stacks.

**Consequence:** the 9th–13th cases this whole spec exists to serve are ~5% of events.
Diversity accounting (§8) must therefore be conditioned *per chord type*, not globally,
or the rare types will be swamped and receive only one or two distinct voicing shapes
across the entire dataset — precisely the failure N19 measured (F-score 0.97 → 0.49–0.58
when a model is tested on voicing families it never saw at training time).

### 2.5 Degree collisions (normative)

Two active degrees can map to the same semitone offset. All possible collisions:

| condition | colliding degrees | semitone |
|---|---|---|
| `seventh == "bb7"` and `thirteenth == "13"` | 7th, 13th | 9 |
| `triad == "sus4"` and `eleventh == "11"` | 3rd, 11th | 5 |
| `triad == "sus2"` and `ninth == "9"` | 3rd, 9th | 2 |
| `triad == "diminished"` and `eleventh == "#11"` | 5th, 11th | 6 |
| `triad == "augmented"` and `thirteenth == "b13"` | 5th, 13th | 8 |

**Rule:** collided degrees merge into a single *sounded* pitch class occupying one voice.
The merged voice inherits the register treatment of the **higher-numbered degree** (so a
sus4/11 merge is voiced as an 11th, in the upper structure, not as a low 3rd). The merged
voice may not simultaneously satisfy the disambiguation requirement (§5) for both
degrees; if the merge would erase the sole differentiator, the merged voice is placed
per the disambiguation rule and the lower degree is considered represented.

### 2.6 Runtime context

```jsonc
{
  "bass_module_active": true,   // if true, the bass module sounds bass_pc below the voicer
  "section": "verse" | "prechorus" | "chorus" | "bridge" | "head" | "solo" | "outro",
  "seed": 12345
}
```

`bass_module_active` drives the root-doubling policy table (parent §5). `section` is
consumed only by voicers that declare section sensitivity (pop synth, pop piano).
`seed` must make the whole pipeline reproducible, as in `chord_gen.py` (`--seed`).

---

## 3. Output contract

```jsonc
{
  "index": 7,
  "voicer": "jazz-piano",
  "midi": [50, 57, 64, 68, 74],          // ascending, absolute MIDI, deduplicated
  "roles": ["root","7th","3rd","13th","9th"],  // parallel to `midi`
  "dct_pitch": 74,                        // disambiguation-critical tone, §5; null if none
  "hands": {"lh": [50, 57], "rh": [64, 68, 74]}, // piano/synth only; null for guitar
  "shape_id": null,                       // guitar only; null otherwise
  "vl_distance": 5,                       // to the previous emitted voicing, §6
  "centroid": 62.6,
  "diagnostics": { "candidates": 214, "rank_chosen": 2, "tau": 2.5 }
}
```

Invariants, checked by assertion in debug builds:

1. `midi` is strictly ascending and contains no duplicate MIDI numbers.
2. Every active degree of the `ChordEvent` (after §2.5 merging) appears as at least one
   pitch class in `midi`, **except** degrees explicitly released by the voicer's omission
   policy — and the 5th is the only degree any voicer may omit unless the policy's
   `root_omission_p` fires (parent §5).
3. If `dct_pitch is not None`, it satisfies the exposure predicate of §5.
4. All pitches lie inside the voicer's `[window_lo, window_hi]`.
5. `centroid` lies inside `[anchor_center - drift_tol, anchor_center + drift_tol]` (§7).

The voicer never emits the bass note *as the bass module's note*. When
`bass_module_active` is true, the bass module independently sounds `bass_pc`; the
voicer's lowest pitch is free and need not be `bass_pc` (parent dev notes). When it is
false, the voicer is responsible for putting `bass_pc` in its lowest voice.

---

## 4. Pipeline

```
for t, chord in enumerate(song.chords):
    tones     = select_tones(chord, policy, ctx, rng)          # Stage 1, parent §3
    dct       = compute_dct(chord, tones)                      # §5
    cands     = policy.candidate_source(tones, chord, policy)  # Stage 2 enumeration
    cands     = [c for c in cands if window_ok(c) and drift_ok(c) and dct_ok(c, dct)]
    if not cands:
        cands = relax(...)                                     # §9 relaxation ladder
    cands     = policy.post_filter(cands, prev, ctx)           # playability etc.
    cost      = [total_cost(c, prev, policy, diversity) for c in cands]
    chosen    = softmax_sample(cands, cost, policy.tau, rng)   # §6.3
    diversity.record(chord_type(chord), shape_signature(chosen))
    emit(chosen); prev = chosen
```

### 4.1 Stage 1 — tone selection

Build the active degree set from the `ChordEvent`, then apply the policy:

```
required = {root, third, fifth} ∩ available(triad)  ∪  {active extension slots}
```

- **Extensions are never dropped for idiomatic simplicity** (parent §0, §7). Every active
  extension slot is sounded by every voicer, in every genre. This is the single hardest
  rule in the system and it exists because the dataset's entire value is that rare
  extensions are reliably present *and audible*.
- The **5th** is the only freely thinnable degree, and only when the triad is `major` or
  `minor` (a perfect 5th carries no quality information) — never for `diminished`,
  `augmented`, `sus2`, `sus4`, `5`, or `1`, where the 5th *is* the quality. Thinning
  probability is `p_omit_fifth`, per voicer.
- The **11th** against a `major` triad is voiced as `#11` only if the event says `#11`;
  a natural `11` over a major 3rd is a real semitone-cluster (4 vs 5) and is retained,
  but the two are forced at least a minor 9th apart in register (§7.3), which is the
  standard remedy and preserves both tones' audibility.
- **Root** is included/doubled per `root_double_p` / `root_omission_p` from the parent §5
  table, and these are *probabilities*, not hard rules, so the dataset varies.
- `triad == "1"` and `triad == "5"` events carry no extensions by construction
  (`chord_gen.py::generate` forces all four slots to `N`); voicers emit root(+5th)
  octave-doublings and skip Stages 5–8 scoring beyond register anchoring.

### 4.2 Stage 2 — register realization

Each candidate is an assignment of octaves to the selected degrees, plus optional
doublings, drawn from `policy.candidate_source`. Two candidate sources exist:

- `free_placement` (piano, synth): enumerate all octave assignments within
  `[window_lo, window_hi]` subject to `max_voices`, spacing rules (§7.3), and doubling
  budget. Pruned by branch-and-bound on partial cost.
- `shape_library` (guitar): enumerate concrete fingered shapes from a curated library,
  transposed to the root; free pitch placement is not available (parent §6).

---

## 5. Disambiguation-critical tone (DCT)

This implements parent §1, the spec's hardest constraint, derived from N19's measured
confusion matrix (maj7→maj, dom7→maj, m7♭5→min/dom7 on mixed audio, N19 Fig. 6).

### 5.1 Computing the DCT

Let `Q` be the chord's full active degree set. For each active degree `d`, form
`Q \ {d}` and ask whether `Q \ {d}` is a **member of the corpus chord-type vocabulary**
(the set of `(triad, seventh, ninth, eleventh, thirteenth)` tuples observed in
`gen/*-labels/`, §2.4). If it is, `d` is a *differentiator*. The DCT is the
highest-numbered differentiator; ties are impossible since degrees are ordered.

Worked cases:

- `C:maj7` — removing the 7th yields `C:maj`, a vocabulary member ⇒ **DCT = the 7th**.
  This is N19's single worst confusion.
- `C:7` (dom7) — removing `b7` yields `C:maj` ⇒ **DCT = b7**.
- `C:hdim7` (dim triad + `b7`) — removing `b7` yields `C:dim` ⇒ **DCT = b7**. N19's
  m7♭5→min/dom7 confusion is driven by the embedded triads, so the b7 must be exposed.
- `C:9` (`b7` + `9`) — removing the 9th yields `C:7`, a vocabulary member ⇒ **DCT = 9th**.
  Removing `b7` yields a `C:add9`-ish tuple which is *also* in the vocabulary
  (`('N','9','N','N')`, 105 occurrences), so `b7` is a differentiator too — but the DCT is
  the *highest*, the 9th. The b7 is then handled by the secondary rule §5.3.
- `C:maj` — removing any triad member leaves a non-vocabulary tuple ⇒ **DCT = None**, and
  the voicer is unconstrained by this section.

### 5.2 Exposure predicate

A candidate `v` satisfies exposure for DCT pitch `p` iff **at least one** of:

- **(a) Topmost.** `p == max(v.midi)`.
- **(b) Registrally isolated.** `p` is separated from its nearer neighbour in `v.midi` by
  ≥ 3 semitones on the side that would create the confusion, and by ≥ 2 semitones on the
  other, i.e. no chord tone sits 1–2 semitones below `p` masking it.
- **(c) Octave-exposed.** `p` is doubled at `p ± 12` and the lower copy also satisfies (b).

Additionally, and unconditionally: **the DCT is never the doubled degree** chosen by
`root_double_p`, and **the DCT is never the omitted degree**.

### 5.3 Secondary differentiator

When more than one degree is a differentiator (common for 9th/11th/13th chords), the
non-DCT differentiators must satisfy the *weaker* predicate (b) alone. This matters
because a 13th chord whose b7 is buried inside a cluster reads as a 6/9 chord in a mix.

### 5.4 Precedence

Per parent §1 and §3, DCT exposure **overrides voice-leading minimization** when the two
conflict. It is implemented as a candidate *filter*, not a cost term. Only if the filter
empties the candidate set does the engine fall back to §9's relaxation ladder — and even
then, predicate (b) is the last thing surrendered.

### 5.5 Rotation (anti-stereotypy)

N19's E1/E2 cross-voicing experiments (F84: 0.97 matched → 0.49–0.58 unmatched) mean that
*always* satisfying the DCT the same way is itself a defect. The engine therefore samples
which predicate branch to satisfy, per chord instance, from
`policy.dct_mode_weights = {top: w_a, isolated: w_b, octave: w_c}`, and the diversity
counter (§8) tracks the branch used per chord type.

---

## 6. Voice-leading and cost

### 6.1 Distance

`vl_distance(v, v_prev)` is the minimum total semitone motion over an optimal matching
between the two pitch multisets, solved as min-cost bipartite matching (Hungarian /
`scipy.optimize.linear_sum_assignment`), since voice count changes when extensions enter
and leave (`spec.md` §5.2). Unmatched voices in the larger set are charged
`policy.unmatched_penalty` semitones each rather than being free, so that inflating the
voice count is not a way to game the cost.

### 6.2 Transformation-aware shortcuts

Per parent §4.1–4.2, and only as a *scoring bias*, not a separate code path:

- **Triad→triad (`major`/`minor` only).** If the pitch-class sets are related by a
  neo-Riemannian **P**, **L**, or **R** operation (Cohn 1998 §II — each the minimal
  single-voice move between triads sharing two common tones), subtract
  `policy.plr_bonus` from the cost. Composite transformations (`D = L∘R`) get half the
  bonus. This is what gives the smooth voicers Cohn's hexatonic/octatonic bounded-region
  behaviour (C98 §III) — and hence the anchor-window property — without hand-coding it.
- **Set-class 4-27 (dom7 ↔ half-dim7).** If both chords are 4-27 members, or one is a
  triad and the other 4-27, apply the same bonus for Cube-Dance-adjacent pairs
  (C98 §IV / Douthett & Steinbach 2003). This is what makes jazz ii⁷♭⁵–V⁷–I voice
  leading fall out of the engine rather than being special-cased.
- **Everything else** (9ths–13ths, chromatic mediants, secondary dominants) uses §6.1
  directly, per parent §4.2's explicit instruction to fall back to Tymoczko-style
  minimum-VL computation where no compact group-theoretic apparatus exists.

### 6.3 Total cost and softmax selection

```
cost(v) = w_vl    * vl_distance(v, v_prev)
        + w_drift * drift_penalty(v)              # §7.2
        + w_space * spacing_penalty(v)            # §7.3
        + w_div   * diversity_penalty(v)          # §8
        + w_role  * policy.role_penalty(v)        # per-voicer idiom term
        - plr_bonus_if_applicable                 # §6.2

P(v) = exp(-cost(v) / tau) / Σ_j exp(-cost(v_j) / tau)
```

Sampling, not argmax (`spec.md` §5.3). `tau → 0` is deterministic minimal-motion voicing;
large `tau` approaches uniform. This is the "creativity knob," and it is *per voicer*,
distinct from `chord_gen.py`'s `--temperature` (`τ₁`), which tempers Stage-1 chord
transitions only.

### 6.4 Calibration against real music (E24)

E24's Billboard-derived stimulus corpus (N = 5076 comparison items) reports a mean
voice-leading distance of **8.37 semitones (SD 4.23)** summed across four voices
(E24 Table 4). Each voicer declares a `vl_target_mean` and `vl_target_sd`; `tau` is
**not** hand-picked but fitted, once, offline:

> Run the voicer over a held-out 200-song sample, binary-search `tau` until the empirical
> mean `vl_distance` is within 0.25 semitones of `vl_target_mean`, then check that the
> empirical SD is ≥ `vl_target_sd`. If the SD is too low, raise `w_div` rather than `tau`.

Normalize to 4 voices (`vl_distance * 4 / n_voices`) before comparing to E24.

E24 also found VL distance to be a statistically significant but *small* predictor of
perceptual discriminability — ~3% odds change per semitone, well behind spectral
pitch-class distance and surprisal (E24 Table 3). Parent §4.4 draws the correct
conclusion and this spec enforces it: **do not over-invest in parsimony.** A spread of VL
distances is worth more to ACR robustness than uniformly smooth voice leading, which is
why `vl_target_sd` is a floor, not a target.

---

## 7. Register anchoring

Register drift is the named failure mode in the parent's dev notes: HMM output is
non-diatonic, and pure parsimony optimization walks the register monotonically upward or
downward. Three independent mechanisms prevent it.

### 7.1 Anchor center

Computed **once per song**, from `tonic_pc`, never per chord:

```
anchor_center = the MIDI pitch p with p % 12 == tonic_pc
                minimizing |p - (window_lo + window_hi) / 2|
```

Note that this deliberately anchors to the *key*, not to the previous chord — per-chord
recentering is exactly what produces drift.

### 7.2 Drift penalty and hard clamp

- **Hard:** all pitches in `[window_lo, window_hi]`. Candidates violating this are never
  generated.
- **Soft:** `drift_penalty(v) = max(0, |centroid(v) - anchor_center| - drift_free)²`
  where `centroid` is the unweighted mean of `v.midi`. Quadratic, so small excursions are
  free and large ones are strongly discouraged.
- **Hard, secondary:** `|centroid(v) - anchor_center| <= drift_tol`. Candidates beyond
  `drift_tol` are filtered.

E24's own stimulus pipeline is the reference implementation here: four-voice harmony
bounded between **MIDI 48–72**, realized by a minimum-voice-leading algorithm
(`hrep`/`minVL`, Harrison & Pearce) rather than free-floating octave choice. Every
voicer's window in specs 01–06 is stated as a deviation from that 48–72 baseline, with
the instrument-physical reason given.

### 7.3 Spacing rules

Applied as `spacing_penalty`, with two components promoted to hard filters:

- **Hard — low-interval limit.** No interval smaller than a perfect 5th below MIDI 48,
  and no interval smaller than a major 3rd below MIDI 55. Below that, close intervals
  produce difference-tone mud that is exactly the "obscured" condition N19's confusions
  live in.
- **Hard — semitone-cluster separation.** Any two active degrees a semitone apart in
  pitch class (e.g. major 3rd vs `11`, root vs `b9`) must be ≥ 13 semitones apart in
  realized pitch (minor 9th or wider), except where the voicer explicitly permits
  clusters (jazz synth, §06).
- **Soft.** Penalize gaps > 12 semitones between adjacent inner voices; penalize more
  than `policy.max_close_pairs` intervals of ≤ 2 semitones.

---

## 8. Diversity accounting

Implements parent §2.1 — "diversify voicing per chord-type-and-instrument combination,
not just per chord instance."

### 8.1 Shape signature

A voicing's signature is register- and root-invariant so that the same *shape* is
recognized across keys and octaves:

```
signature(v) = ( voicer_id,
                 tuple(sorted(interval_from_lowest)),   # e.g. (0,4,7,11,14)
                 octave_band(min(v.midi)),              # low / mid / high
                 dct_mode )                             # top / isolated / octave
```

### 8.2 Chord type key

`chord_type(chord) = (triad, seventh, ninth, eleventh, thirteenth)` — root-invariant, so
all 12 transpositions of `min9` share a budget. This is what makes the ~5% rare-extension
tail (§2.4) get real coverage.

### 8.3 Penalty

```
diversity_penalty(v) = log(1 + count[chord_type][signature(v)])
                     - log(1 + mean_count[chord_type])
```

A persistent counter across the whole generation run (not per song), so a shape used
heavily in song 3 is discouraged in song 40. Reset per dataset build. `w_div` is the
knob that trades stylistic idiom against N19's cross-voicing-generalization requirement.

### 8.4 Coverage report

Dataset builds emit `coverage.json`: per `chord_type`, the number of distinct signatures
used and the entropy of the signature distribution. **Acceptance gate:** every chord type
with ≥ 20 occurrences in the corpus must reach ≥ 8 distinct signatures per voicer and a
normalized signature entropy ≥ 0.6. A voicer failing this gate is producing exactly the
single-voicing-family training data N19 showed collapses to F 0.49–0.58.

---

## 9. Relaxation ladder

When filtering empties the candidate set, relax in this order, logging each step. The
ordering encodes the spec's priorities: musical niceties yield first, ACR-critical
constraints yield last.

1. `spacing_penalty` soft components → cost-only.
2. `drift_tol` → `drift_tol * 1.5`.
3. Doubling budget → allow one extra doubling.
4. `max_voices` → `max_voices + 1` (guitar exempt; physically fixed at 6).
5. Omit the 5th even if `p_omit_fifth` did not fire (major/minor triads only).
6. Window → widen by one octave, and log a `WINDOW_RELAXED` warning.
7. DCT predicate (c) → (b) → (a) ordering relaxed; the *branch* may change but exposure
   is still required.
8. **Never relaxed:** presence of every active extension degree; DCT exposure by *some*
   predicate; the low-interval limit.

If step 7 still yields nothing, raise `VoicingImpossible` with the full chord event.
This should be unreachable and indicates a policy misconfiguration, not bad input.

---

## 10. `VoicerPolicy` schema

```python
@dataclass(frozen=True)
class VoicerPolicy:
    voicer_id: str                    # "pop-piano" | ... | "jazz-synth"
    genre: str                        # "pop_rock" | "jazz"
    instrument: str                   # "piano" | "guitar" | "synth"

    # register (§7)
    window_lo: int; window_hi: int
    drift_free: int; drift_tol: int

    # voice budget (§4)
    min_voices: int; max_voices: int
    p_omit_fifth: float
    root_double_p: float              # P(root doubled | bass_module_active)
    root_omission_p: float            # P(root omitted  | bass_module_active)
    max_doublings: int

    # parsimony (§6)
    tau: float                        # fitted, not hand-set (§6.4)
    w_vl: float; w_drift: float; w_space: float; w_div: float; w_role: float
    plr_bonus: float
    unmatched_penalty: float
    vl_target_mean: float             # semitones / 4 voices, cf. E24 = 8.37
    vl_target_sd: float

    # disambiguation (§5)
    dct_mode_weights: dict            # {"top":.., "isolated":.., "octave":..}

    # hooks
    candidate_source: Callable        # free_placement | shape_library
    role_penalty: Callable            # per-voicer idiom term
    post_filter: Callable             # playability, hand-span, patch limits
    section_profile: dict | None      # section -> parameter overrides
```

Every field in specs 01–06 is one of these. A voicer that needs a mechanism not
expressible here must extend *this* spec, not fork the engine.

---

## 11. Module registry and dispatch

```python
VOICERS = {
    ("pop_rock", "piano"):  pop_piano.POLICY,     # spec 01
    ("pop_rock", "guitar"): pop_guitar.POLICY,    # spec 02
    ("pop_rock", "synth"):  pop_synth.POLICY,     # spec 03
    ("jazz",     "piano"):  jazz_piano.POLICY,    # spec 04
    ("jazz",     "guitar"): jazz_guitar.POLICY,   # spec 05
    ("jazz",     "synth"):  jazz_synth.POLICY,    # spec 06
}

def voice_song(song, instrument, ctx) -> list[VoicedChord]:
    policy = VOICERS[(song["genre"], instrument)]
    return Engine(policy, ctx).run(song)
```

The genre comes from the song file and is not overridable; the instrument is the
caller's choice. Rendering multiple instruments over one song runs the engine once per
instrument with **independent** `prev` state but a **shared** diversity counter, so the
piano and guitar parts don't converge on the same registers.

---

## 12. CLI

```
python voicer.py --in gen/jazz-labels/song_0.json \
                 --instrument piano --bass-active --section head \
                 --seed 12345 --out voiced/jazz/song_0.piano.json
python voicer.py --in-dir gen/pop-rock-labels --instruments piano,guitar,synth \
                 --seed 1 --out-dir voiced/pop_rock --coverage coverage.json
```

`--tau` and `--w-div` override the policy for experiments. `--strict` promotes every
relaxation-ladder step to a hard error, for use in CI.

---

## 13. Validation

Unit:

1. Degree resolution matches §2.3 for all 8 triads × all extension-token combinations.
2. All five collisions in §2.5 resolve without dropping a required pitch class.
3. `compute_dct` returns the 7th for `maj7`/`dom7`/`hdim7`, the 9th for `9`-chords, and
   `None` for bare triads.
4. Exposure predicates (a)/(b)/(c) accept and reject the hand-built fixtures in
   `tests/fixtures/dct_*.json`.
5. `vl_distance` is symmetric, zero for identical voicings, and correct against
   hand-computed matchings where voice counts differ.

Property, over all 100 songs × 2 genres in `gen/`:

6. No emitted pitch outside `[window_lo, window_hi]`.
7. No centroid outside `drift_tol` — and, separately, `max(centroid) - min(centroid)`
   across a whole song ≤ 2 × `drift_tol` (the actual drift test).
8. Every active extension degree present in every emitted voicing.
9. `VoicingImpossible` never raised; `WINDOW_RELAXED` fires on < 0.5% of chords.

Statistical, per voicer:

10. Mean VL distance within 0.25 semitones of `vl_target_mean`; SD ≥ `vl_target_sd`
    (§6.4, calibrated against E24's 8.37 ± 4.23).
11. Coverage gate of §8.4 passes.

End-to-end, and the only test that measures the thing the project actually cares about:

12. Render a pilot set, train a small ACR CNN in the manner of N19, and confirm that
    maj7→maj, dom7→maj, and m7♭5→min/dom7 confusion rates on *mixed* audio are below the
    baselines reported in N19 Fig. 6. Confusion rate on these three pairs is the primary
    success metric for the entire voicing layer.

---

## References

- Cohn, R. (1998). Introduction to neo-Riemannian theory. *JMT* 42(2), 167–180.
- Douthett, J. & Steinbach, R. (2003). Parsimonious graphs. *JMT* 42(2).
- Eitel, M., Ruth, N., Harrison, P., Frieler, K., & Müllensiefen, D. (2024). *Music &
  Science*, 7.
- Majchrzak, M. & Mańdziuk, J. (2025). arXiv:2508.05878.
- Nadar, C.-R., Abeßer, J., & Grollmisch, S. (2019). *SMC 2019*, Málaga.
- Ostermann, F., Vatolkin, I., & Ebeling, M. (2023). *EURASIP JASMP*, 2023:13.
- Tymoczko, D. (2006). *Science*, 313(5783), 72–74.
