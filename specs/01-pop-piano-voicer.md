# Spec 01 — Pop/Rock Piano Voicer

**Module id:** `pop-piano` · **Key:** `("pop_rock", "piano")`
**Implements:** spec 07 (`VoicerPolicy`). Only deltas from spec 07 are stated here.
**Parent:** `voicing_algorithm_spec.md` §5, §6 (Piano), §7 (Pop/rock).

---

## 1. Role

The parent spec designates piano as the **reference voicer**: "most flexible; no hard
physical constraint beyond the register window... use this as the 'reference' voicer
against which guitar/synth constraints are described as restrictions" (parent §6). Specs
02–06 are written as restrictions on, or departures from, this one.

Pop piano is also the *widest-voice-count* voicer, because block-chord two-hand doubling
is idiomatic and because pop production routinely stacks the same chord across two
octaves. That makes it the natural home for the octave-doubled DCT exposure branch
(spec 07 §5.2c), which is rarer in the other five voicers.

---

## 2. The pop-extension problem

The HMM is deliberately tuned to over-produce rare extensions, so this voicer receives
`b7/9/11/13` stacks labelled `pop_rock` — chords that a real pop pianist would encounter
rarely and often thin out. Parent §7 is explicit and supersedes its own earlier draft:

> Per the reframed goal (§0), **do not reduce extensions for idiomatic sparseness.**
> Where this spec's earlier draft said "bias toward triads and simple 7ths," that
> guidance is superseded.

So this voicer does **not** simplify. What stays genre-specific is *doubling density*
(higher than jazz) and the *distribution of voice-leading distances* (wider than jazz).
In practice this means a pop-piano `C13` sounds like a pop pianist voicing a chord they
rarely play — full, root-doubled, extensions on top — rather than like a jazz voicing.
That is the intended outcome, not a compromise.

Corpus reality check (spec 07 §2.4): 4773/9600 events carry no extension at all and
3325 carry only a `b7`. The overwhelming majority of this voicer's work is triads and
seventh chords, and it must sound genuinely idiomatic on those; the extension handling is
the tail that must not break.

---

## 3. Register

```
window_lo = 43   # G2 — bottom of comfortable LH pop-piano root territory
window_hi = 88   # E6 — above this, pop piano is a topline/ostinato role, out of scope
drift_free = 5
drift_tol  = 11
```

E24's reference realization bounds four-voice harmony to MIDI **48–72** (E24, via
`hrep`/`minVL`). This voicer widens that in both directions, for two reasons:

- **Downward to 43:** E24 is voicing abstract four-voice harmony; pop piano actually
  plays LH roots and root-5ths in the G2–C3 region, and clamping to 48 would make every
  voicing sound like a choir reduction.
- **Upward to 88:** the octave-doubled DCT branch needs headroom. A `Cmaj9` with the 9th
  doubled at both D5 and D6 is a normal pop-piano sound and is a distinct signature for
  diversity accounting (spec 07 §8).

`drift_tol` of 11 is the widest of the six voicers, consistent with the parent's note
that pop tolerates and desires a wider spread. It is still a hard clamp — the drift
failure mode from the parent's dev notes is not permitted to reappear just because the
genre is loose.

### 3.1 Hand split

```
lh_window = (43, 60)
rh_window = (55, 88)
lh_max_voices = 3
rh_max_voices = 4
max_hand_span = 14 semitones per hand   # a comfortable 9th-to-10th reach
```

Overlap in 55–60 is permitted but costs `w_role`, since crossed/overlapping hands are
uncommon in straightforward pop comping.

---

## 4. Tone selection

```
min_voices = 3
max_voices = 7
p_omit_fifth   = 0.15
root_double_p  = 0.70     # given bass_module_active
root_omission_p = 0.02
max_doublings  = 3
```

Parent §5, pop-piano row: *"Double root, often 8ve above bass — idiomatic weight; still
expose extension per §1."* `root_double_p = 0.70` makes that a strong default rather than
a rule, per the parent's requirement that each row's default be a probability so the
dataset carries variety in this dimension too.

- **Preferred doubling target:** the root, one octave above `bass_pc`, in the LH. This is
  the literal reading of the parent's "often 8ve above bass."
- **Second doubling target:** the 5th, in the RH, but only when no `11`/`#11` is active
  (a doubled 5th plus an 11th crowds the same region).
- **Root omission** is near-zero: rootless voicings are a jazz device and are only
  produced here as occasional diversity samples.
- `p_omit_fifth = 0.15` is low — pop piano is a full, weighted sound. The 5th is thinned
  mainly when voice count is already at 6–7 because of a full extension stack.
- Extensions: all active slots sounded, unconditionally (spec 07 §4.1).

---

## 5. Candidate source

`candidate_source = free_placement`, restricted to hand-feasible partitions:

1. Partition the selected degrees into an LH set and an RH set, LH entirely below RH
   except in the permitted overlap band.
2. Enumerate octave assignments per hand within that hand's window and `max_hand_span`.
3. Reject any candidate where either hand exceeds its voice cap or span.
4. Apply spec 07 §7.3 spacing rules; the low-interval limit is doing real work here,
   since LH thirds below MIDI 48 are the classic muddy-piano failure.

Typical candidate counts after filtering: ~150–400 for a seventh chord, ~40–120 for a
full 13th chord.

---

## 6. Parsimony

```
tau            = fitted (§6.4 of spec 07); expect ≈ 3.0–4.0
w_vl    = 1.0
w_drift = 0.7
w_space = 0.6
w_div   = 1.1
w_role  = 0.8
plr_bonus = 1.5
unmatched_penalty = 2.0
vl_target_mean = 8.4    # semitones summed over 4 voices
vl_target_sd   = 4.2
```

`vl_target_mean = 8.4` is set to E24's measured mean of **8.37 semitones (SD 4.23)** on
real Billboard-derived four-chord progressions (E24 Table 4). Pop piano is the voicer
that should sit exactly on the real-pop number, because Billboard *is* pop — this voicer
is the calibration anchor for the whole system, and the other five are described as
offsets from it.

The `plr_bonus` is deliberately modest. Pop is full of P/L/R-adjacent triad pairs
(C→Am is R, C→Cm is P, C→Em is L — Cohn 1998 §II), and rewarding them produces natural
common-tone retention. But E24 found VL distance to be only a *small* predictor of
perceptual discriminability (~3% odds change per semitone, well behind spectral
pitch-class distance and surprisal, E24 Table 3), so leaning harder on parsimony would
buy little and cost variety. Parent §4.4's instruction — "don't over-invest in
hyper-parsimonious voicing at the expense of variety" — is implemented here as a
comparatively high `w_div` (1.1) against a modest `plr_bonus`.

### 6.1 Section-driven register lift

```python
section_profile = {
  "verse":     {"drift_free": 4, "max_voices": 5, "root_double_p": 0.60},
  "prechorus": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.70},
  "chorus":    {"drift_free": 8, "max_voices": 7, "root_double_p": 0.85,
                "anchor_shift": +4},
  "bridge":    {"drift_free": 6, "max_voices": 6, "root_double_p": 0.65,
                "anchor_shift": -3},
  "outro":     {"drift_free": 5, "max_voices": 6, "root_double_p": 0.70},
}
```

`anchor_shift` moves `anchor_center` (spec 07 §7.1) for the duration of the section only,
then returns. This is the parent's §4.1 "parsimony = low pop voicer is permitted larger
jumps... when the section calls for it (chorus lift, register jump for energy)" — the
jump is a controlled, bounded, reversible anchor move, not a drift.

---

## 7. Disambiguation policy

```
dct_mode_weights = {"top": 0.50, "isolated": 0.20, "octave": 0.30}
```

Pop piano carries the highest `octave` weight of the six voicers, because it has the
voice budget and the register headroom for it, and because octave-doubled extensions are
genuinely idiomatic in pop keyboard writing. This matters directly: N19's E1/E2 results
showed F84 collapsing from 0.97 to 0.49–0.58 when a model met a voicing family it hadn't
trained on, so a voicer that can cheaply contribute a *third* distinct exposure family
should.

Specific handling of N19's worst confusions in a pop context:

- **maj7 → maj** (N19's dominant error on mixed audio, Fig. 6). Pop piano's habit of
  doubling the root is exactly what buries a maj7's 7th, since the 7th sits a semitone
  below a doubled root. Hard rule, enforced by spec 07 §5.2: when the DCT is a major 7th,
  no root copy may sit within 2 semitones above it. In practice this pushes the doubled
  root down an octave rather than dropping it.
- **dom7 → maj.** Same mechanism; the `b7` must clear any root copy by ≥ 2 semitones on
  the `isolated` branch, or sit on top.
- **`9`-chords.** The 9th is the DCT (spec 07 §5.1) and the `b7` is a secondary
  differentiator that must still satisfy predicate (b). The tempting pop voicing —
  root/5th LH, 3rd/b7/9 clustered in the RH — is rejected unless the b7 clears its
  neighbours.

---

## 8. `role_penalty`

Idiom terms, all soft:

| condition | penalty |
|---|---|
| Hands overlap in the 55–60 band | 1.0 |
| RH interval > 12 semitones between adjacent voices | 0.6 |
| LH plays a 3rd (any) below MIDI 52 | 1.2 |
| Voicing has no pitch below MIDI 55 while `bass_module_active` is false | 2.0 |
| More than 2 semitone-adjacent pairs anywhere | 0.8 |
| `13` present but no `b7`/`7` sounded below it | 0.7 (reads as a 6th chord) |

The last row is an ACR term wearing an idiom hat: a 13th without an audible 7th below is
the pop-piano route into N19's dom7→maj family of errors.

---

## 9. Diversity

`w_div = 1.1`, the highest among the pop voicers. Rationale: pop piano's candidate space
is the largest of the six, so it is the voicer best positioned to satisfy spec 07 §8.4's
coverage gate (≥ 8 distinct signatures and ≥ 0.6 normalized entropy per chord type with
≥ 20 corpus occurrences). Signature bands for `octave_band(min(midi))`:

```
low  : 43-54
mid  : 55-67
high : 68-88
```

---

## 10. Validation

Inherits spec 07 §13, plus:

1. **Hand feasibility.** No hand ever exceeds 14 semitones or its voice cap, over all 100
   pop songs.
2. **Calibration.** Mean normalized VL distance falls in 8.37 ± 0.25 and SD ≥ 4.2,
   matching E24 Table 4 — this voicer is the calibration anchor and its failure
   invalidates the offsets used by specs 02–06.
3. **maj7 root-masking.** Zero emitted `maj7`/`dom7` voicings in which a root copy sits
   1–2 semitones above the DCT. Assertion, not statistic.
4. **Section lift.** Chorus-section centroids average ≥ 3 semitones above verse-section
   centroids within the same song, and the song-wide centroid range still satisfies spec
   07 §13.7 (≤ 2 × `drift_tol`). This is the test that a lift is not a drift.
5. **Extension retention.** 100% of active extension slots sounded — no silent thinning,
   including in the `b7/9/11/13` stacks that appear 33 times in the corpus.

---

## References

Cohn (1998) §II–III; Eitel et al. (2024) Tables 3–4; Nadar et al. (2019) §E1/E2, Fig. 6;
Ostermann et al. (2023) §4.3; Tymoczko (2006).
