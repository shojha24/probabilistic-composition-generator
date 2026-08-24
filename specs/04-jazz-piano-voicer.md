# Spec 04 — Jazz Piano Voicer

**Module id:** `jazz-piano` · **Key:** `("jazz", "piano")`
**Implements:** spec 07 (`VoicerPolicy`), `candidate_source = free_placement`.
**Parent:** `voicing_algorithm_spec.md` §4.1–4.2, §5, §6 (Piano), §7 (Jazz).

---

## 1. Role

The most *structured* of the six voicers, and the one where the parent spec's
neo-Riemannian and 4-27 machinery (parent §4.1–4.2) actually earns its place. Two
defining departures from pop piano (spec 01):

- **Rootless by default.** Parent §5, jazz-piano row: *"Omit root by default (rootless
  voicings) — classic comping move; root doubling only for solo/unaccompanied contexts."*
- **Shell-based construction.** Parent §7, Jazz: *"Shell voicings (3rd+7th) as the
  structural skeleton, extensions layered on top, rootless voicings the default when bass
  is active, and lower target voice-leading distance on average."*

This produces a genuine tension with spec 07 §5 that must be stated explicitly, because
it is the most subtle correctness risk in the system: **rootless voicings remove the very
tone that anchors a chord's identity, which increases the burden on the DCT.** A rootless
`Cmaj7` (E–G–B–D) is literally an `Em7` pitch-class set. N19's measured maj7↔min
confusion is not a coincidence there — it is the same set. So this voicer's DCT rules
(§6) are stricter than any other's, and root omission is *conditioned on the bass module
actually sounding the root*.

---

## 2. Register

```
window_lo = 48   # C3 — the standard bottom of two-hand comping when a bass is present
window_hi = 84   # C6
drift_free = 4
drift_tol  = 8   # tightest of the six
```

This is the closest of any voicer to E24's reference realization bound of MIDI **48–72**
(four-voice harmony via `hrep`/`minVL`), extended up to 84 only to accommodate upper-
structure extensions above a shell. Parent §3 cites E24 as "a directly reusable reference
implementation, not just a citation," and this is the voicer that reuses it most directly.

`drift_tol = 8` is the tightest in the system, for a specific reason: jazz is the
high-parsimony genre (parent §7), and high parsimony is exactly the setting where the
parent's dev-note drift failure occurs — "chords keep going up or down in relation to the
original chord to optimize parsimony." A tight clamp on the highest-parsimony voicer is
where the clamp matters most. Parent §4.1 argues that PLR chains give bounded register
"for free" via Cohn's hexatonic/octatonic systems (C98 §III); that argument holds for
consonant triads, and this voicer's material is mostly tetrachords and larger, so the
explicit clamp is retained rather than trusted away.

### 2.1 Hand split

```
lh_window = (48, 67)     # shell / rootless left hand
rh_window = (60, 84)     # upper structure
lh_max_voices = 3
rh_max_voices = 4
max_hand_span = 12       # tighter than pop piano's 14; jazz voicings are compact
```

---

## 3. Tone selection

```
min_voices = 3
max_voices = 6
p_omit_fifth    = 0.60    # highest of the six
root_double_p   = 0.05    # given bass_module_active
root_omission_p = 0.70    # given bass_module_active; see §3.1
max_doublings   = 1
```

- `p_omit_fifth = 0.60`: the perfect 5th is the first thing a jazz pianist drops. It is
  still barred for `diminished`, `augmented`, `sus2`, `sus4`, `5`, `1` (spec 07 §4.1) —
  for those triads the 5th *is* the quality, and dropping it from a `hdim7` would
  manufacture exactly N19's m7♭5→min/dom7 confusion by hand.
- `root_omission_p = 0.70`: a strong default, not a rule, per the parent's requirement
  that the §5 table's entries be probabilities so the dataset varies.
- `max_doublings = 1`: jazz voicings are lean; doubling is an occasional colour, not a
  weighting device.

### 3.1 Root omission gate (hard)

Root omission requires **all** of:

1. `bass_module_active == True`. With no bass module, the voicer owns the root and
   `root_omission_p` is forced to 0.0 (parent §5: "root doubling only for
   solo/unaccompanied contexts").
2. `bass_interval == 0`. On an inversion (781/9600 corpus events), the bass module sounds
   the *bass* note, not the root, so omitting the root leaves the root pitch class absent
   from the entire texture. Root omission is disabled for all inverted chords.
3. The resulting rootless pitch-class set is **not** an exact member of the corpus chord
   vocabulary under a different root. Rootless `Cmaj7` = `Em7`; rootless `C7` = `Em7b5`.
   When this test fails, either keep the root or require the DCT to be exposed by the
   `top` predicate *and* the shell to include the 3rd — see §6.2.

Condition 3 is the single most important rule in this spec. Without it, the jazz piano
voicer becomes a machine for generating mislabelled training data, which is worse than
generating no data at all.

### 3.2 Shell construction

Build in three layers, in order:

1. **Shell (LH):** the 3rd and 7th, the two tones that define quality — the "structural
   skeleton" of parent §7. When the 7th is `N` (4773 + others of the corpus have no
   seventh), the shell is 3rd + 5th, or 3rd + root if the 5th is omitted.
2. **Guide extensions (RH):** the 9th, 11th, 13th, in ascending order, closely voiced.
3. **Colour (RH top):** the DCT, placed per §6.

For `triad in ("sus4","sus2")` the "3rd" of the shell is the sus tone (5 or 2 semitones);
for `triad == "5"` or `"1"` (147 corpus events) the shell degenerates and the voicer emits
root/5th octaves with no extension layer.

---

## 4. Candidate source

`free_placement`, restricted to shell-anchored partitions:

1. Place the shell in `lh_window`, subject to `max_hand_span`.
2. Enumerate RH octave assignments for the remaining degrees in `rh_window`.
3. Reject candidates where the RH lowest voice is more than 16 semitones above the LH
   highest voice (a hole in the middle), or where the hands cross.
4. Apply spec 07 §7.3 spacing rules. The low-interval limit is rarely binding given
   `window_lo = 48`, which is itself why 48 was chosen.

Typical filtered candidate counts: 60–200 — smaller than pop piano's, because the shell
anchor prunes aggressively. This is intentional; the smaller space is the reason this
voicer's `tau` runs low without becoming deterministic.

---

## 5. Parsimony — the PLR / Cube-Dance voicer

```
tau     = fitted; expect ≈ 1.8-2.5    # lowest of the six
w_vl    = 1.6     # highest of the six
w_drift = 0.8
w_space = 0.7
w_div   = 0.9
w_role  = 1.0
plr_bonus = 3.0   # highest of the six
unmatched_penalty = 2.5
vl_target_mean = 6.0
vl_target_sd   = 3.5
```

Parent §7 asks for "lower target voice-leading distance on average (biased toward the
PLR/4-27 minimal-motion end of §4.1–4.2)." `vl_target_mean = 6.0` is set roughly
0.55 SD below E24's real-music mean of 8.37 (SD 4.23, E24 Table 4) — a meaningful
reduction that is still inside the observed distribution of real progressions, rather
than an artificial hyper-smoothness that would produce unrepresentative audio.

`vl_target_sd = 3.5` is a **floor**, and the reason the reduction is bounded. Parent §4.4
and §2 both insist on spread: "still with enough spread in the training data that the
model doesn't only ever see maximally smooth transitions." If calibration (spec 07 §6.4)
finds the SD below 3.5, raise `w_div`, not `tau`.

### 5.1 Transformation bonuses

This is the voicer that exercises spec 07 §6.2 in full:

- **Triads.** P, L, R each earn the full `plr_bonus = 3.0` — Cohn's three contextual
  inversions, each the minimal single-voice move between triads sharing two common tones
  (C98 §II). Composite `D = L∘R` earns half.
- **Set-class 4-27.** Dominant and half-diminished sevenths related by Cube-Dance
  adjacency earn the full bonus (C98 §IV, referencing Douthett & Steinbach). Parent §4.2
  names the payoff precisely: this is what makes ii⁷♭⁵–V⁷–I voice leading emerge from the
  engine — "a principled way to choose, e.g., which inversion of the next dom7 chord
  minimizes total voice movement from the current m7♭5 — rather than hand-coding 'resolve
  down a fifth, keep common tones' as a special case."
- **Beyond 4-27.** For 9th–13th chords, no bonus; spec 07 §6.1's Tymoczko-style
  minimum-VL computation applies directly, per parent §4.2's explicit instruction that no
  equivalently compact apparatus exists for higher tetrachords and pentachords in the
  reviewed literature.

### 5.2 Common-tone retention

Additional bonus of 0.5 per pitch retained at exactly the same MIDI pitch between
consecutive voicings (`spec.md` §5.2, "common-tone bonus"), capped at 3 retained tones.
This is what produces the characteristic jazz-comping effect of a held upper structure
over a moving shell, and it is cheap to compute inside the existing matching.

---

## 6. Disambiguation policy

```
dct_mode_weights = {"top": 0.55, "isolated": 0.40, "octave": 0.05}
```

`octave` is near-zero: `max_doublings = 1` and lean voicings leave no room, and octave-
doubling an extension is not a jazz-piano idiom.

### 6.1 Interaction with rootless voicings

Rootless voicing removes the root, which is often the tone a listener (or model) uses to
orient. Compensations, all hard:

- The **3rd must always be present** in a rootless voicing. A rootless voicing missing
  both root and 3rd is unlabellable.
- The **7th must always be present** in a rootless voicing of any chord whose `seventh`
  slot is active. Since the shell *is* 3rd + 7th, this follows from §3.2, but it is
  asserted independently because the omission ladder (spec 07 §9) must never reach it.
- When the DCT is the 7th and the voicing is rootless, predicate (b) alone is
  insufficient — the 7th must be `top` or must be the highest voice of the LH shell with
  a clear ≥ 3 semitone gap to the RH. This is the direct countermeasure to N19's
  maj7→maj and dom7→maj confusions on mixed audio (Fig. 6), applied where the risk is
  highest.

### 6.2 The rootless-collision case

When §3.1 condition 3 fails (rootless `Cmaj7` ≡ `Em7`, rootless `C7` ≡ `Em7♭5`), the
voicer takes one of two branches, sampled 50/50 for diversity:

- **Branch A — keep the root.** Place it as the lowest LH voice. Simplest, and the safer
  of the two.
- **Branch B — go rootless with a compensating extension.** Permitted only if the chord
  has an active 9th, 11th, or 13th whose presence breaks the ambiguity (rootless `Cmaj9`
  = E–G–B–D–... which is no longer a clean `Em7`), and only with the DCT on top.

Branch B is what makes the jazz voicer sound like jazz on extended chords; branch A is
what keeps plain `maj7`/`dom7` events honest. The corpus makes this consequential:
`b7`-only chords are 3325/9600 events, and essentially all of them route to branch A.

---

## 7. `role_penalty`

| condition | penalty |
|---|---|
| Shell (3rd+7th) not in the LH | 1.5 |
| LH interval < 3 semitones below MIDI 55 | 1.5 |
| Gap between LH top and RH bottom > 16 semitones | 1.0 |
| More than 1 doubling | 2.0 |
| 5th present *and* 13th present (the 5th adds nothing and crowds) | 0.6 |
| `b9` sounded below the 3rd | 1.2 |
| Hands cross | 3.0 |

---

## 8. Diversity

`w_div = 0.9` — lower than the pop voicers, because jazz's idiom constraints legitimately
narrow the space and because this voicer is the system's designated source of *low*-VL
training examples. It must still clear spec 07 §8.4's coverage gate (≥ 8 signatures,
≥ 0.6 normalized entropy per chord type with ≥ 20 occurrences); if it cannot at
`w_div = 0.9`, raise `w_div` rather than loosening the idiom terms, since the coverage
gate encodes N19's E1/E2 finding (F 0.97 → 0.49–0.58 on unseen voicing families) and the
idiom terms encode taste.

**Additional gate:** rootless and rooted voicings must each account for ≥ 20% of emitted
voicings for chord types with an active `seventh`. A voicer that is *always* rootless is
one voicing family, which is the failure mode the gate exists to catch.

Signature bands:

```
low  : 48-58
mid  : 59-70
high : 71-84
```

---

## 9. Validation

Inherits spec 07 §13, plus:

1. **Shell integrity.** Every emitted voicing of a chord with an active `seventh`
   contains both the 3rd and the 7th (or the sus tone in place of the 3rd). Hard
   assertion across all 100 jazz songs.
2. **Rootless legality.** Zero rootless voicings emitted when `bass_module_active` is
   false, or when `bass_interval != 0`, or when §3.1 condition 3 fails without routing
   through §6.2 branch B.
3. **No manufactured ambiguity.** For every emitted rootless voicing, the pitch-class set
   is checked against the corpus chord vocabulary under all 12 roots; any exact match to
   a *different* labelled chord type must have been produced by §6.2 branch B with the
   DCT on top. This is the test that this voicer is not poisoning its own labels.
4. **Calibration.** Mean normalized VL distance in 6.0 ± 0.25 and SD ≥ 3.5; and strictly
   below pop piano's 8.4 (spec 01) and pop synth's 11.0 (spec 03).
5. **Drift.** Song-wide centroid range ≤ 16 semitones (2 × `drift_tol`) — the tightest
   drift bound in the system, on the highest-parsimony voicer, which is where the parent's
   dev-note failure mode would surface first if it survived.
6. **PLR utilization.** For triad-to-triad transitions between `major`/`minor` chords, at
   least 45% of emitted transitions are P/L/R-adjacent. Below that, `plr_bonus` is not
   biting and §5.1's whole mechanism is decorative.
7. **Rootless/rooted balance.** The §8 additional gate passes.

---

## References

Cohn (1998) §II (P/L/R as minimal single-voice moves), §III (hexatonic/octatonic
systems), §IV (set-class 4-27, Cube Dance); Douthett & Steinbach (2003); Eitel et al.
(2024) Tables 3–4 (VL mean 8.37 ± 4.23; VL as a small predictor), and the `hrep`/`minVL`
MIDI 48–72 realization pipeline; Nadar et al. (2019) Fig. 6, §E1/E2; Tymoczko (2006).
