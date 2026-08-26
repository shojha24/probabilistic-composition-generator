# Chord Progression Generator — Technical Spec

## 0. Goal

Generate realistic pop/jazz chord progressions — root, triad quality, bass note, and
extensions — from a trained corpus, such that:

- Common progressions reproduce idiomatic multi-chord phrasing (ii–V–I, IV–I–V, etc.)
- Rare chords/roots/extensions still surface, rather than being smoothed away
- Bass motion and extension choice are internally consistent with each other and with
  the chord above them (no vertical clashes)
- The final output is a voice-led, playable sequence of concrete note sets, not just
  chord symbols

Architecture: a 4-stage generative pipeline. Stages 1–3 are a **cascaded Markov/AR
chain with per-stage backoff**, producing a symbolic chord (root, triad, bass,
extensions) at each time step. Stage 4 is a **probabilistic voicer** that turns each
symbolic chord into real notes using parsimonious voice leading with softmax sampling.

**Separate models are trained per genre umbrella (pop/rock, jazz)** — not a single
pooled model with genre as a context variable. See §1.3.

```
Stage 1: Root/Triad   (2nd-order HMM, backs off to 1st → 0th order)
    ↓
Stage 2: Bass         (1st-order AR on Root, backs off to 0th order)
    ↓
Stage 3: Extensions   (4-slot trie walk over Root+Bass+history, backs off progressively)
    ↓
Stage 4: Voicer       (candidate voicings ranked by distance, softmax sampled)
```

---

## 1. Data Model

### 1.1 Corpus representation

Each training progression is a time-ordered sequence of chord events, encoded
key-relative rather than in absolute pitch:

```json
{
  "root_interval": 7,        // 0–11, this chord's root relative to the song's ACTIVE TONIC at this timestamp (not the previous chord)
  "triad": "minor",          // closed 7-way vocabulary — see below
  "bass_interval": 3,        // 0–11, this chord's bass note relative to ITS OWN ROOT (inversion/slash interval)
  "seventh":    "b7",        // {7, b7, bb7, N}
  "ninth":      "9",         // {9, b9, #9, N}
  "eleventh":   "N",         // {11, #11, N}
  "thirteenth": "N"          // {13, b13, N}
}
```

**Root encoding.** `root_interval` is computed against the *time-resolved active
key* (songs may modulate; key spans are looked up per chord timestamp), not against
the previous chord. This means Stage 1's `Root_t` is a functional/scale-degree
position (closer to a Roman-numeral slot than a raw interval-of-motion), which
generalizes across keys and is what actually carries "ii–V–I"-type functional
identity — the motion between consecutive `root_interval`s is recoverable but is not
itself the primary encoding.

**Triad vocabulary — closed, 7-way.** All quality "flavor" (6, 7, 9, 11, 13, maj7,
etc.) is stripped out of `triad` and pushed entirely into the four extension slots
below. `triad ∈ {major, minor, diminished, augmented, sus4, sus2, "5"}`. This keeps
Stage 1's state space small (12 roots × 7 triads = 84 symbols) independent of how
richly extended the corpus's chords are — critical for backoff sparsity at 2nd order.

**Bass encoding.** `bass_interval` is relative to the chord's own root (0 = root
position, other values = inversions / slash chords), matching Stage 2's design in
§3.1.

**Extensions — four independent categorical slots, not one blob.** `seventh`,
`ninth`, `eleventh`, `thirteenth` are separate fields, each with a small closed
alphabet (including `N` = absent). This is the corpus's native representation and is
what Stage 3 is built around — see §4.

Sequences are segmented by song/section (don't let Markov context bleed across
unrelated tunes).

### 1.2 State variables per stage

| Stage | Variable(s) predicted | Conditioning context |
|---|---|---|
| 1 | `(Root_t, Triad_t)` | `(Root_{t-1}, Triad_{t-1}, Root_{t-2}, Triad_{t-2})` |
| 2 | `Bass_t` | `(Root_t, Bass_{t-1})` |
| 3 | `(7th_t, 9th_t, 11th_t, 13th_t)` | `(Root_t, Triad_t, Bass_t, Ext_{t-1})`, generated as an ordered trie walk — see §4 |

### 1.3 Genre partitioning

Train **fully separate models per genre umbrella** (`pop_rock`, `jazz`) — independent
count tables, independent backoff/interpolation weights, independent extension tries
— rather than one pooled model with genre as an extra conditioning variable.

Rationale: extension vocabulary richness differs by an order of magnitude between the
two umbrellas (jazz corpora carry far more altered/extended chords), and pop/rock
phrase idioms (Stage 1 bigrams/trigrams) are stylistically distinct enough from jazz
turnarounds that pooling would blur both rather than sharing usefully. A conditioning
variable only helps when the conditioned-on populations share enough structure to
benefit from shared counts; genre is treated here as a hard split instead. Generation
is invoked against one genre's full model stack at a time.

---

## 2. Stage 1 — Harmonic Backbone

### 2.1 Model

Order-2 categorical HMM over the joint `(Root, Triad)` symbol, where `Root` is the
tonic-relative scale-degree position defined in §1.1 (not raw motion from the
previous chord) and `Triad` is drawn from the closed 7-way vocabulary. Trained
independently per genre (§1.3):

```
P(Root_t, Triad_t | Root_{t-1}, Triad_{t-1}, Root_{t-2}, Triad_{t-2})
```

Estimated by counting 3-grams of `(Root, Triad)` tokens in the corpus, with
Laplace/add-k smoothing on top (k small, e.g. 0.1–0.5) purely to avoid zero
floors within the *observed*-context distributions — this is separate from the
structural backoff below, which handles *unobserved* contexts.

### 2.2 Backoff cascade

Katz-style backoff, three levels:

1. **Level 2 (full context):** if the 3-gram context `(Root_{t-1},Triad_{t-1},Root_{t-2},Triad_{t-2})`
   has been seen ≥ `min_count_2` times in training, sample from its distribution.
2. **Level 1 (drop t-2):** else, back off to
   `P(Root_t, Triad_t | Root_{t-1}, Triad_{t-1})`. If seen ≥ `min_count_1` times, sample from it.
3. **Level 0 (unigram):** else, back off to the marginal
   `P(Root_t, Triad_t)` computed over the whole corpus (or corpus-with-key-normalization).
   This always has support as long as the chord type occurs anywhere in training, so it
   is the guaranteed floor.

**Recommended refinement — interpolate, don't hard-switch.** A hard count threshold
creates an audible "cliff": a chord with 4 training occurrences behaves totally
differently from one with 3. Instead, weight each level by a confidence factor derived
from its context count (Witten-Bell or simple count-based mixing):

```
λ2 = N2 / (N2 + D)      # D = discount constant, tune empirically (start ~1–3)
P_final = λ2 · P_level2 + (1 - λ2) · [ λ1 · P_level1 + (1 - λ1) · P_level0 ]
```

where `N2` is the count of the 2nd-order context and `λ1` is defined analogously from
`N1`. This keeps rare-context behavior smooth as a function of data density rather
than a step function.

### 2.3 Rarity-preservation note

Backoff to Level 1/0 is expected and *good* for rare chords — it deliberately trades
away 2nd-order phrase memory for guaranteed local plausibility. Do not attempt to
"protect" the 2nd-order phrase logic by refusing to back off; that reintroduces the
zero-probability crash you're trying to avoid.

---

## 3. Stage 2 — Bass AR

### 3.1 Model

```
P(Bass_t | Root_t, Bass_{t-1})
```

Bass is generated immediately after Root/Triad are fixed for time `t`, and before
Extensions — bass gets priority because it is the strongest determinant of perceived
voice-leading smoothness and because Extensions in Stage 3 need to condition on it.

Encode `Bass_t` relative to `Root_t` (i.e., predict the **inversion/slash interval**:
root position, 1st inversion, 2nd inversion, or a non-chord-tone bass for
slash-chords) rather than absolute pitch class. This is both lower-sparsity and
musically the right invariant — "walk up a step into 1st inversion" generalizes
across keys and roots in a way raw pitch classes don't.

### 3.2 Backoff cascade

1. **Level 1:** `P(Bass_t | Root_t, Bass_{t-1})` if seen ≥ `min_count_1b` times.
2. **Level 0:** else `P(Bass_t | Root_t)` — the most common bass/inversion for this
   chord regardless of what preceded it. This is your stated fix and is correct; it
   always has support if the chord (Root, Triad) occurs anywhere in the corpus with
   any bass.

Apply the same interpolation treatment as Stage 1 if hard cliffs are audible in
practice (usually less critical here since the state space is much smaller).

---

## 4. Stage 3 — Extension Generation (4-Slot Trie Walk, Cascaded on Bass)

Extensions are natively four separate categorical fields (`seventh, ninth, eleventh,
thirteenth` — §1.1), but they are **not independent of each other**: which ninths are
plausible depends heavily on which seventh was chosen (e.g. a `b9` overwhelmingly
co-occurs with a dominant `b7`, rarely with a `maj7`). Sampling the four slots as
independent AR chains risks assembling a full extension tuple that never occurred
together in the corpus — the exact problem the earlier extension-chain algorithm
was built to prevent. This section replaces that ad hoc algorithm with a structure
that fits directly into the 2nd-order-HMM-plus-backoff framework already used for
Stages 1–2.

### 4.1 Two orthogonal problems, two mechanisms

There are two separate questions being conflated if you try to solve this with one
AR chain:

1. **Combinatorial validity** — "is this *specific combination* of (7th, 9th, 11th,
   13th) one that has actually occurred for this chord?" This is a structural
   constraint, independent of sequence history.
2. **Sequential/vertical preference** — "given the previous chord's extensions and
   this chord's bass note, which of the *valid* combinations is most idiomatic right
   now?" This is the AR/history-conditioning part.

Mechanism (1) is solved with a **per-(Root,Triad) extension trie**. Mechanism (2) is
solved by **reweighting trie-branch choice using history and bass, with backoff** —
structurally the same backoff cascade used in Stages 1–2, just applied at each trie
node instead of at a single flat state.

### 4.2 The extension trie (combinatorial validity)

For each `(Root, Triad)` state observed in training, build a trie over the ordered
slot sequence `seventh → ninth → eleventh → thirteenth`, where each node is a
partial-tuple prefix and each edge is a slot value observed to extend that prefix in
the corpus (including `N`, so "no extension at all" is itself a valid path). Edge
weights are raw co-occurrence counts.

Because the trie is built only from prefixes that actually occurred, **any full
root-to-leaf walk is guaranteed to reproduce a combination attested in training** —
this is what replaces the old validity-checking algorithm, and it falls out of the
data structure rather than needing separate enforcement code.

**Trie selection backoff** (which trie to walk, before even touching history/bass):

1. **Level A:** use the `(Root_t, Triad_t)`-specific trie if it has ≥ `min_count_A`
   total instances.
2. **Level B:** else back off to a trie aggregated over all roots sharing this
   `Triad_t` (extension usage is driven far more by chord quality than by which root
   it's built on — e.g. what extensions suit "dominant" is fairly root-invariant).
3. **Level C:** else back off to the genre-wide trie aggregated over all
   `(Root, Triad)` — guaranteed floor, since `N`-only paths exist for essentially
   every genre corpus.

### 4.3 Walking the trie with history/bass conditioning (the AR + backoff part)

At each node during the walk (i.e., at each of the 4 slots in order), the next edge
is chosen by scoring the node's *children* (which are fixed, valid options from
§4.2) against history and bass context, with the same backoff-cascade logic as
Stages 1 and 3's predecessor design — but now the fallback is always among the
trie's own valid children, so it can never propose an invalid value:

1. **Level 2 (full context):** score children by
   `P(child | trie_prefix, Ext_{t-1}, Bass_t)` if this exact combination of
   trie-node + history + bass has been seen ≥ threshold.
2. **Level 1 (drop history):** else `P(child | trie_prefix, Bass_t)` — vertical
   consistency with the bass note is kept, sequential phrase memory on extensions is
   dropped.
3. **Level 0 (trie marginal):** else the trie node's own unconditional child
   distribution, `P(child | trie_prefix)`. This is the same object used for §4.2's
   trie itself and is guaranteed to have support by construction.

Interpolate (λ-weighted, as in Stages 1–2) rather than hard-switching between these
levels. Repeat this scoring/selection step for each of the four slots in order,
descending the trie one level per slot; the final leaf reached is the emitted
`(7th_t, 9th_t, 11th_t, 13th_t)`.

### 4.4 Why bass is kept, history is dropped first

Same reasoning as before: a clashing extension is audibly worse than a merely
unprepared one, so when a context is too sparse to support full conditioning, drop
`Ext_{t-1}` before dropping `Bass_t`.

---

## 5. Stage 4 — Voicer (Probabilistic Parsimonious Voice Leading)

Takes the symbolic chord stream `(Root_t, Triad_t, Bass_t, Ext_t)` for all `t` and
renders it into concrete pitched note sets, one chord voicing at a time, conditioned
on the previous voicing.

### 5.1 Candidate generation

For each chord at time `t`:

1. Enumerate valid **voicings** consistent with the symbolic chord: assignments of
   the required chord tones — triad tones implied by `Triad_t`/`Bass_t`, plus every
   active token across the four extension slots — to playable pitches across a
   working register range, such that:
   - the bass note is fixed to `Bass_t` in the lowest voice,
   - every required chord tone appears at least once.

   **Doubling, omission, and voice-count constraints are intentionally left
   unspecified here.** Which tones can be safely omitted, which may be doubled, and
   how many simultaneous voices are even physically available are all downstream of
   which instrument(s) will realize the chords (piano vs. guitar vs. synth voicing
   differ substantially in note count and reachable intervals). That layer is
   deferred to an instrument-specific voicing-constraint module built once
   instrumentation is decided, rather than baked into this spec as a generic
   configurable ruleset.
2. This produces a candidate set `V_t = {v_1, v_2, ..., v_k}`, sized however broadly
   the register range and required-tone constraint above allow.

### 5.2 Ranking by voice-leading distance

For each candidate `v_i ∈ V_t`, compute a distance to the *previous chosen voicing*
`v_{t-1}^*`:

```
dist(v_i, v_{t-1}^*) = Σ_j |pitch_j(v_i) − pitch_j(v_{t-1}^*)|
```

using the minimal-total-motion voice assignment (solve the optimal voice-to-voice
matching, e.g. via bipartite min-cost matching if voice count/tone count changes
between chords — extensions being added/dropped changes voice count between `t-1` and
`t`). This is the standard parsimonious voice-leading distance (total semitone
motion across matched voices).

Optionally add secondary penalty terms into the same scalar score:
- register drift penalty (keep the voicing centered in a target range),
- common-tone bonus (reward retained pitches, standard in parsimonious voice leading),
- avoid-parallel-fifths/octaves penalty if stylistically desired.

### 5.3 Softmax sampling (not argmax)

Convert distances to a probability distribution and sample rather than always taking
the closest voicing — this is what makes it a *generator* rather than a deterministic
optimizer, and gives you controllable variety:

```
P(v_i) = exp(-dist(v_i, v_{t-1}^*) / τ) / Σ_j exp(-dist(v_j, v_{t-1}^*) / τ)
```

- `τ → 0`: converges to always picking the strict minimal-motion voicing (fully
  deterministic parsimonious voice leading).
- `τ` large: approaches uniform random voicing choice among valid candidates.
- Recommended starting point: tune `τ` so the *expected* rank of the chosen voicing is
  low (i.e., you're usually picking one of the top 2–3 closest options), with
  occasional larger leaps for expressive variety. This is your one clearly-exposed
  "creativity knob" and worth exposing as a top-level generation parameter.

### 5.4 Output

The chosen `v_t^*` becomes the actual rendered chord (MIDI notes / notation), and is
carried forward as `v_{t-1}^*` for the next time step's distance computation.

---

## 6. Full Generation Algorithm (pseudocode)

```
model = load_genre_model(genre)   # pop_rock OR jazz — fully separate parameter sets (§1.3)
state = init_state()  # holds Root_{t-1,t-2}, Triad_{t-1,t-2}, Bass_{t-1}, Ext_{t-1}, v_{t-1}*

for t in range(T):
    # Stage 1
    (Root_t, Triad_t) = backoff_sample_stage1(model, state)

    # Stage 2
    Bass_t = backoff_sample_stage2(model, Root_t, state.Bass_prev)

    # Stage 3 — trie selection, then slot-by-slot walk with backoff at each node
    trie = select_extension_trie(model, Root_t, Triad_t)          # §4.2 backoff (A→B→C)
    Ext_t = walk_trie_with_backoff(trie, state.Ext_prev, Bass_t)  # §4.3 backoff (2→1→0), one call per slot

    # Stage 4
    candidates = enumerate_voicings(Root_t, Triad_t, Bass_t, Ext_t)
    scored     = [ (v, dist(v, state.v_prev)) for v in candidates ]
    v_t        = softmax_sample(scored, tau)

    emit(Root_t, Triad_t, Bass_t, Ext_t, v_t)
    state.update(Root_t, Triad_t, Bass_t, Ext_t, v_t)
```

---

## 7. Training Procedure

1. Parse corpus into per-song chord-event sequences (schema in §1.1); segment on
   song/section boundaries.
2. **Split by genre umbrella first** (pop_rock, jazz) — all subsequent steps run
   independently per genre; no cross-genre pooling at any stage (§1.3).
3. Normalize to tonic-relative root / root-relative bass encodings for Stage 1–2
   training.
4. Count n-grams at each required order for Stages 1–2 (2-gram+1, 1-gram+1, 0-gram
   for Stage 1; 1-gram+1 and 0-gram for Stage 2).
5. For Stage 3, build the extension tries: one per `(Root, Triad)`, one per `Triad`
   (aggregated across roots), and one genre-wide — the three trie-selection backoff
   levels from §4.2. Within each trie, also accumulate the history/bass-conditioned
   child counts needed for §4.3's node-level backoff (full context, bass-only,
   marginal).
6. Compute discount/interpolation weights (`λ`) per context from context counts, for
   both the flat Stage 1–2 backoff and the trie-node Stage 3 backoff.
7. Store all count tables and tries as sparse dict/hashmap structures keyed by
   context tuple / trie path — no need for dense matrices given the state space size
   after relative-encoding.
8. Validate: hold out a dev split of songs, check that no generation step ever hits a
   true zero-probability wall (i.e., confirm Level-0/Level-C fallbacks always have
   support) and that every generated extension tuple is traceable to an actual
   root-to-leaf trie path seen in training; this is the main correctness test for the
   backoff and trie design, not perplexity.

---

## 8. Known Failure Modes & Mitigations

| Failure mode | Cause | Mitigation |
|---|---|---|
| Generation crashes on unseen `(Root,Bass)` pair | Missing Level-0 fallback in Stage 2 | Verify unigram tables have full coverage over all chords seen anywhere in corpus |
| Generation produces an extension combination never seen in training | Slots sampled as independent AR chains instead of a shared trie | Use the per-(Root,Triad) trie structure (§4.2) — a root-to-leaf walk is valid by construction; independent per-slot sampling reintroduces exactly this bug |
| Audible "seam" when backoff triggers | Hard count-threshold cutover | Use interpolated (λ-weighted) mixing instead of switch, at both the flat Stage 1–2 backoff and the trie-node Stage 3 backoff |
| Extension clashes with bass despite trie | Stage 3 backed off past Level 1 at a given node (dropped `Bass_t` conditioning) into pure trie marginal | Rare; acceptable per design, but can add a hard post-hoc clash filter (e.g. reject extension a semitone above bass) as a final safety net before voicing |
| Voicing leaps wildly on rare chords | Small register-bounded candidate set for unusual extensions/qualities produces few valid voicings, all distant | Widen register window; revisit once instrument-specific voicing constraints (§5.1) are defined, since those will also affect candidate density |
| Over-repetition of the same "safe" progression | τ too low / min_count thresholds too aggressive, over-relying on Level 2 / Level A | Raise τ slightly, or inject a small ε floor of Level-0/Level-C mass even when higher levels have support, to keep some entropy |

---

## 9. Tunable Hyperparameters (summary)

- `min_count_2`, `min_count_1` (Stage 1), `min_count_1b` (Stage 2)
- `min_count_A`, `min_count_B` (Stage 3 trie-selection backoff — §4.2) and separate
  thresholds for the Level 2/1/0 node-walk backoff (§4.3)
- Discount constant `D` (interpolation smoothing, all stages/levels)
- Add-k smoothing constant `k` (within-distribution smoothing)
- Voicing register bounds (doubling/omission/voice-count policy deliberately deferred
  to an instrument-specific module — see §5.1)
- Softmax temperature `τ` (creativity knob for the voicer)
- Optional ε-floor mixing-in of Level-0/Level-C mass at generation time, independent
  of observed counts, for extra variety control
- Stage 1 Level-1 softmax temperature `τ_1` and self-transition discount `δ` — see
  §10.3

---

## 10. Command-Line Interface

### 10.1 Invocation

```python
p = argparse.ArgumentParser(description="Chord sequence generator")

p.add_argument("--genre",       default="pop_rock", choices=VALID_GENRES)
p.add_argument("--tonic",       default="C",
               help="Tonic note (C, F#, Bb …) or integer 0-11")
p.add_argument("--num",         type=int, default=48,
               help="Chords per song")
p.add_argument("--bpm",         type=int, default=120)

p.add_argument("--debug", action="store_true",
               help="Shortcut for --random-genre --random-tonic "
                    "--random-num --random-bpm all at once")
p.add_argument("--random-genre", action="store_true")
p.add_argument("--random-tonic", action="store_true")
p.add_argument("--random-num",   action="store_true")
p.add_argument("--random-num-range", default="10,50")
p.add_argument("--random-bpm",   action="store_true")
p.add_argument("--random-bpm-range", default="60,180")

p.add_argument("--temperature", type=float, default=None,
               help="Softmax temperature for level-1 transitions. "
                    "Default: uniform per-genre value from "
                    "GENRES_TO_TEMPS (not re-rolled per song).")
p.add_argument("--self-transition-discount", type=float, default=None)

p.add_argument("--songs",       type=int, default=5)
p.add_argument("--seed",        type=int, default=None)
p.add_argument("--dist-dir",    default=_DEFAULT_DIST_DIR)
p.add_argument("--out-dir",     default=None,
               help="Optional output override. By default, use "
                    "gen/jazz-labels or gen/pop-rock-labels by genre.")
return p.parse_args()
```

`VALID_GENRES = {"pop_rock", "jazz"}`, matching the two trained model umbrellas in
§1.3 — `--genre` selects which fully-separate model stack (§1.3, §6 `load_genre_model`)
is loaded for the whole invocation.

### 10.2 Parameter reference

| Flag | Type | Default | Meaning |
|---|---|---|---|
| `--genre` | choice | `pop_rock` | Which genre-umbrella model stack to load (§1.3). |
| `--tonic` | note or int | `C` | Active tonic used to resolve `root_interval` (§1.1) into absolute pitches at render time. Accepts a note name or an integer 0–11. |
| `--num` | int | `48` | Chords generated per song — this is `T` in the §6 pseudocode's `for t in range(T)` loop. |
| `--bpm` | int | `120` | Tempo metadata attached to the rendered output; not consumed by any generation stage (Stages 1–4 are tempo-agnostic). |
| `--songs` | int | `5` | Number of independent songs to generate in this run, each with its own fresh `init_state()` (§6) — Markov context does not carry across songs, consistent with the segmentation rule in §1.1. |
| `--seed` | int | `None` (unset) | RNG seed for reproducible sampling across all stochastic draws (backoff sampling, trie walks, voicer softmax, and any `--random-*` resolution). Unset means non-deterministic. |
| `--dist-dir` | path | `_DEFAULT_DIST_DIR` | Directory containing the trained count tables / extension tries produced by §7 training (per-genre, loaded by `load_genre_model`). |
| `--out-dir` | path | genre-specific under `gen/` | Optional output override. Without it, jazz uses `gen/jazz-labels` and pop/rock uses `gen/pop-rock-labels`. |

**Randomization flags.** Each `--random-X` flag causes `X` to be resolved by uniform
random draw instead of taking its explicit/default value, independently **once per
song** (not once per run), so a batch of `--songs N` songs gets `N` independently
rolled values for each randomized parameter:

| Flag | Randomizes | Range source |
|---|---|---|
| `--random-genre` | `--genre` | uniform over `VALID_GENRES` |
| `--random-tonic` | `--tonic` | uniform over the 12 pitch classes |
| `--random-num` | `--num` | uniform integer over `--random-num-range` (`"lo,hi"`, default `10,50`) |
| `--random-bpm` | `--bpm` | uniform integer over `--random-bpm-range` (`"lo,hi"`, default `60,180`) |

`--debug` is pure sugar: setting it is equivalent to passing all four
`--random-genre --random-tonic --random-num --random-bpm` flags at once (their
`*-range` flags are unaffected and still take their own defaults/overrides). An
explicit `--random-num-range`/`--random-bpm-range` passed alongside `--debug` still
applies, since `--debug` only toggles the four boolean switches, not the ranges.

If a parameter's explicit flag (e.g. `--num 32`) is passed *and* its `--random-*`
counterpart is set, the random draw takes precedence for that parameter — the
explicit value is treated as inert unless later overridden by a per-song reroll.

### 10.3 New generation-time knobs

Two parameters here are not defined elsewhere in this spec and extend the Stage 1
design (§2):

**`--temperature` (`τ_1`).** Applies softmax tempering specifically to the **Level 1**
conditional distribution `P(Root_t, Triad_t | Root_{t-1}, Triad_{t-1})` (§2.2) before
it participates in the `λ`-weighted interpolation with Level 2 and Level 0 — it does
not affect Stage 4's voicing temperature `τ` (§5.3), which is a separate, unrelated
knob despite the similar name. Concretely:

```
P_level1_tempered(x) = P_level1(x)^(1/τ_1) / Σ_y P_level1(y)^(1/τ_1)
```

used in place of the raw `P_level1` in the §2.2 interpolation formula. `τ_1 → 0`
sharpens Level 1 toward its argmax (more idiomatic-but-repetitive bigram behavior);
`τ_1` large flattens it toward uniform (more exploratory backoff-adjacent behavior)
without actually dropping to Level 0.

If `--temperature` is not passed, `τ_1` is looked up per-genre from a fixed table
`GENRES_TO_TEMPS` (one default value per entry in `VALID_GENRES`) rather than being
re-rolled or re-sampled per song — i.e., every song in a batch that doesn't override
`--temperature` uses the same genre-appropriate default, even across `--songs N`.
This differs from the `--random-*` parameters in §10.2, which do get re-rolled per
song.

**`--self-transition-discount` (`δ`).** A multiplicative discount applied to the
probability mass of the self-transition case — `Root_t == Root_{t-1}` **and**
`Triad_t == Triad_{t-1}` (an exact chord repeat) — within the Stage 1 distribution
before sampling, with the removed mass renormalized across the remaining symbols.
This directly targets the "over-repetition of the same 'safe' progression" failure
mode (§8), giving an explicit knob rather than relying solely on raising `τ` at
Stage 4. `δ = 1` is a no-op (no discount); `δ = 0` would forbid immediate chord
repeats outright. If unset, no self-transition discount is applied (behavior
unchanged from §2).
