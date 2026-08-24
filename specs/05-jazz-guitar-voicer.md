# Spec 05 — Jazz Guitar Voicer

**Module id:** `jazz-guitar` · **Key:** `("jazz", "guitar")`
**Implements:** spec 07 (`VoicerPolicy`), `candidate_source = shape_library`.
**Parent:** `voicing_algorithm_spec.md` §5, §6 (Guitar), §7 (Jazz), §8.
**Sibling:** spec 02 (pop guitar) — shares the instrument model and library machinery;
only the shape inventory and policy differ.

---

## 1. Role

Parent §6 gives the brief:

> N19's own guitar MIDI generation used **barré chord voicings rooted on the low E, A,
> and D strings**, which is a reasonable, empirically-used minimum viable shape set to
> start from; **extend with drop-2/drop-3 voicings for jazz 7th chords** (standard
> jazz-guitar practice, not covered by the reviewed papers but consistent with N19's own
> extension into 4-note chord types). **Root omission is expected/idiomatic when the bass
> module is active** (§5).

So jazz guitar = pop guitar's fretboard model + a drop-2/drop-3 inventory + rootless
defaults. Parent §5's jazz-guitar row: *"Omit root by default — mirrors piano; shell
voicings (3rd+7th) as the skeleton."*

Parent §8 is candid that this is the least literature-backed area in the whole document:
*"Guitar shape-library coverage for extended chords (9th–13th) — not addressed by any
reviewed source; jazz-guitar voicing references (drop-2/drop-3 practice) are pedagogical
rather than research literature."* This spec therefore states the drop-voicing
construction **as a derivation rule** so the library can be generated and audited rather
than hand-curated from an uncited source.

---

## 2. Instrument model

Identical to spec 02 §2 (standard tuning, open strings 40/45/50/55/59/64), with jazz
adjustments:

```
window_lo = 45    # A2 — jazz comping rarely uses the open low E when a bass is present
window_hi = 79    # G5
drift_free = 4
drift_tol  = 9
max_fret       = 15   # jazz comping goes higher than pop
max_fret_span  = 4
max_voices     = 5    # jazz guitar comping is 3-5 notes, not 6
min_voices     = 3
```

`window_lo = 45` rather than 40 is the concrete expression of "root omission is
expected/idiomatic when the bass module is active": with a bass module sounding the root,
the guitar vacates the low E-string register entirely rather than competing with it.
`max_voices = 5` reflects that drop-2 and drop-3 voicings are 4-note grips, occasionally
5 with an added extension, never 6-string barres.

---

## 3. Shape library

Shares spec 02 §3.1's `Shape` structure and root-relative storage. Different inventory.

### 3.1 Drop-voicing derivation (the construction rule)

Start from a 4-note close-position voicing of the chord's shell-plus-extension set,
ordered ascending as `[v1, v2, v3, v4]` (v4 highest):

- **Drop-2:** move `v3` (the second voice from the top) down an octave.
- **Drop-3:** move `v2` (the third voice from the top) down an octave.
- **Drop-2&4:** move `v3` and `v1` down an octave.

Then assign to string sets:

| voicing | string sets |
|---|---|
| Drop-2 | 6-5-4-3, 5-4-3-2, 4-3-2-1 |
| Drop-3 | 6-4-3-2, 5-3-2-1 (note the skipped, muted string) |
| Drop-2&4 | 5-4-2-1, 6-5-3-2 |

A derived shape is **admitted to the library** iff, at some root fret in `[0, 15]`, every
required fret is reachable within `max_fret_span` and all pitches land in
`[window_lo, window_hi]`. This makes the library a *computed artifact* with a generation
script and a checked-in snapshot, not a hand-typed table — which is the only honest way
to handle an area the parent flags as unsourced.

### 3.2 Required coverage

| family | requirement |
|---|---|
| Shell voicings (root + 3rd + 7th; root + 7th + 3rd) | E-, A-string roots, all 4 seventh qualities |
| Drop-2, all inversions | maj7, min7, dom7, m7♭5, dim7, minmaj7 × 3 string sets |
| Drop-3, all inversions | maj7, min7, dom7, m7♭5 × 2 string sets |
| Rootless 4-note (3-7-9-5, 7-3-13-9, etc.) | ≥ 4 shapes per seventh quality |
| Altered dominants (`b9`, `#9`, `#11`, `b13`) | ≥ 3 shapes each; corpus has 125 `b7/b9` and 57 `b7/#9` events |
| 9 / 11 / 13 with 5th omitted | ≥ 3 shapes each |
| sus4 / sus2 / augmented / diminished | ≥ 2 shapes each |
| `5` / `1` | 2-note grips |

### 3.3 Omission priority

Same mechanism as spec 02 §3.3, different order, because jazz omits differently:

```
5th  >  root  >  11th  >  9th  >  13th  >  3rd  >  7th
```

The root moves *up* the priority list relative to pop (it is the second thing dropped,
and with `root_omission_p = 0.75` it is frequently gone before the priority list is even
consulted). The 3rd and 7th are last — they are the shell, and dropping either
manufactures the exact confusions N19 measured (maj7↔maj/min, m7♭5↔min/dom7).

Same three constraints as spec 02: never drop the DCT, log every drop as
`EXTENSION_DROPPED`, keep the dataset-wide rate under **3%** (slightly higher than pop's
2%, because `max_voices = 5` genuinely cannot hold a 6-degree chord and jazz chords in
this corpus carry more degrees).

---

## 4. Tone selection

```
p_omit_fifth    = 0.70    # highest in the system; drop-2 shapes routinely have no 5th
root_double_p   = 0.03
root_omission_p = 0.75    # given bass_module_active; gated per §4.1
max_doublings   = 1
```

### 4.1 Root omission gate

Inherits **all three** conditions from spec 04 §3.1 verbatim — `bass_module_active`,
`bass_interval == 0`, and the no-manufactured-ambiguity test (rootless `Cmaj7` ≡ `Em7`,
rootless `C7` ≡ `Em7♭5`). The parent explicitly says jazz guitar "mirrors piano" here, and
that mirroring must include the safety condition, not just the permissive default. On
guitar the risk is if anything higher, since a 4-note rootless grip *is* a complete,
plausible, differently-named chord with no additional voices to disambiguate it.

The §6.2 branch A/B resolution from spec 04 applies unchanged.

---

## 5. Candidate source

`shape_library`, per spec 02 §5, with `shape_distance` scored under `w_role`. Two jazz
additions:

- **Position continuity.** A `position` is a 4-fret window. Staying within the same
  position across a chord change earns a bonus of 1.0 folded into `role_penalty`; this is
  what produces the characteristic jazz-comping sound of a hand that barely moves through
  a ii–V–I. It is the fretboard analogue of spec 04 §5.2's common-tone bonus.
- **Inversion-aware selection for 4-27 pairs.** When both chords are set-class 4-27
  members (dom7 / half-dim7), rank candidate shapes by Cube-Dance adjacency of their
  realized pitch-class sets before applying the general cost (spec 07 §6.2). Parent §4.2
  identifies this as the concrete payoff of the 4-27 apparatus: choosing *which inversion*
  of the next dom7 minimizes total voice movement from the current m7♭5, rather than
  hard-coding "resolve down a fifth."

---

## 6. Parsimony

```
tau     = fitted; expect ≈ 3.0-4.0
w_vl    = 1.2
w_drift = 0.9
w_space = 0.3
w_div   = 1.0
w_role  = 1.3     # shape distance + position continuity
plr_bonus = 2.0
unmatched_penalty = 2.0
vl_target_mean = 7.5
vl_target_sd   = 4.0
```

`vl_target_mean = 7.5` sits between jazz piano's 6.0 (spec 04) and E24's real-music 8.37
(E24 Table 4). Jazz guitar is genuinely smoother than pop guitar (9.5, spec 02) because
drop-2 inversions are designed for exactly this — a drop-2 maj7 and the drop-2 min7 a
fourth away sit in nearly the same fretboard position. But it cannot reach jazz piano's
6.0, because the fretboard quantizes motion and no amount of weighting changes that.
Setting the target where the instrument can actually reach it is what keeps calibration
(spec 07 §6.4) from driving `tau` to zero and collapsing the voicer onto three shapes.

---

## 7. Disambiguation policy

```
dct_mode_weights = {"top": 0.60, "isolated": 0.38, "octave": 0.02}
```

Jazz-guitar-specific hazards, all hard:

- **Drop-2 inner-voice burial.** Drop-2 moves the second-from-top voice down an octave.
  When the DCT happens to be that voice, the shape places the chord's sole differentiator
  in the lowest, most masked position of the grip. Rule: **a drop-2 or drop-3 shape whose
  dropped voice is the DCT is rejected** unless the resulting pitch still satisfies
  predicate (b) with ≥ 3 semitones of clearance. In practice this steers extended chords
  toward the drop-2 inversion that puts the 7th or the extension on string 1 or 2.
- **Shell-only voicings.** A bare root+3rd+7th shell is a legitimate jazz-guitar sound and
  the 7th is trivially exposed. But when the chord event carries a 9th/11th/13th, a shell
  that drops all of them is an `EXTENSION_DROPPED` event and is subject to the §3.3 3%
  budget — shells are not a licence to ignore the corpus's extended chords.
- **Rootless + inner-voice DCT.** The two riskiest choices in this spec must not combine:
  a rootless voicing whose DCT is not the top voice is rejected outright, with no
  relaxation-ladder exemption.

---

## 8. `role_penalty`

| condition | penalty |
|---|---|
| `shape_distance` (spec 02 §5) | ×1.0, direct |
| Position changed between consecutive chords | 1.0 (equivalently: −1.0 for staying) |
| Fret span = 5 (allowed but awkward) | 0.8 |
| 6 sounded strings | 1.5 (not a jazz-comping texture) |
| Voicing includes an open string above fret 5 elsewhere in the grip | 0.7 |
| Root present while `bass_module_active` and `root_omission_p` gate passed | 0.4 |
| Two adjacent sounded strings a semitone apart below MIDI 55 | 1.6 |

---

## 9. Diversity

`w_div = 1.0`. Signature extended with `root_string` and shape family, per spec 02 §8.

**Additional gates:**

- Every seventh-chord type with ≥ 20 corpus occurrences must be voiced with at least
  **3 distinct inversions** across the dataset (drop-2 root position, 1st, 2nd, 3rd).
  N19's IDMT-SMT-CHORDS piano set deliberately included "all chord types in all possible
  root note positions and inversions"; the guitar equivalent is inversion coverage across
  drop shapes, and it is the direct countermeasure to the E1/E2 collapse
  (F 0.97 → 0.49–0.58 on unseen voicing families).
- Rootless and rooted voicings must each account for ≥ 20% of emitted voicings for chord
  types with an active `seventh` (mirrors spec 04 §8).
- Drop-2 and drop-3 families must each account for ≥ 15% of emitted 4-note voicings.

Signature bands:

```
low  : 45-56
mid  : 57-68
high : 69-79
```

---

## 10. Validation

Inherits spec 07 §13 and spec 02 §9's playability tests (round-trip realization, string
legality, no fretboard drift), plus:

1. **Library derivation.** The checked-in library snapshot is byte-identical to the output
   of the §3.1 derivation script. A hand-edited library fails CI — this is what keeps an
   unsourced area (parent §8) auditable.
2. **Rootless legality.** Same three conditions as spec 04 §9.2, enforced identically.
3. **No manufactured ambiguity.** Same as spec 04 §9.3 — every rootless pitch-class set
   checked against the corpus vocabulary under all 12 roots.
4. **Drop-voice DCT.** Zero emitted drop-2/drop-3 voicings whose dropped voice is the DCT
   without ≥ 3 semitones of clearance. Hard assertion.
5. **Drop rate.** `EXTENSION_DROPPED` < 3% dataset-wide, never for a DCT, reported per
   chord type.
6. **Calibration.** Mean normalized VL distance in 7.5 ± 0.25, SD ≥ 4.0; strictly between
   jazz piano's 6.0 and pop guitar's 9.5.
7. **Inversion and family coverage.** The §9 additional gates pass.
8. **Position continuity.** Median absolute fret change between consecutive chords ≤ 3.
   Above that, `w_role`'s position term is not biting and the part will not sound like
   comping.

---

## References

Nadar et al. (2019) — barré voicings on E/A/D strings, all-inversions dataset design,
§E1/E2, Fig. 6; Cohn (1998) §IV (set-class 4-27, Cube Dance); Douthett & Steinbach
(2003); Eitel et al. (2024) Table 4; `voicing_algorithm_spec.md` §5, §6, §7, §8 (open
problem: drop-2/drop-3 practice is pedagogical, not research literature — hence §3.1's
derivation rule).
