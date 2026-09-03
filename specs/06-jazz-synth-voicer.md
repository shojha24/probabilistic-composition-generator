# Spec 06 — Jazz Synth Voicer

**Module id:** `jazz-synth` · **Key:** `("jazz", "synth")`
**Implements:** spec 07 (`VoicerPolicy`), `candidate_source = free_placement`.
**Parent:** `voicing_algorithm_spec.md` §5, §6 (Synth), §7 (Jazz).
**Siblings:** spec 03 (pop synth — same pad role, different policy), spec 04 (jazz piano —
same genre idiom, which this voicer follows in tone selection).

---

## 1. Role

Parent §5, jazz-synth row: *"Follow piano/guitar convention unless used as a pad/lead."*
Parent §6 then resolves the ambiguity in favour of the pad: *"Recommend defaulting synth
voicers to the pad role for both genres... treating 'lead' as out of scope."*

Those two instructions pull in different directions, and this spec resolves them by
**splitting the two stages of the pipeline between them**:

- **Stage 1 (tone selection) follows jazz-piano convention** — rootless defaults, shell
  skeleton, aggressive 5th omission. This is the "follow piano/guitar convention" half.
- **Stage 2 (register realization) is a pad** — wide spread, octave doubling, no hand
  constraint. This is the "default to the pad role" half.

The result is a genuinely distinct voicing family: jazz tone content in pad register
distribution. That is worth having for its own sake, since spec 07 §8 counts distinct
signatures per chord type, and N19's E1/E2 results (F84 0.97 matched → 0.49–0.58 unseen)
say the count of distinct families is what protects the trained model.

This is exactly the AAM-paper's own architectural pattern: element generators that
"differ in temporal pattern but share the same underlying voicing method" (AAM-paper
§4.3). Here, jazz-synth and jazz-piano share tone selection and differ in realization.

---

## 2. Register

```
window_lo = 40    # E2 — lower than jazz piano; pads can sit under the comping register
window_hi = 91    # G6
drift_free = 5
drift_tol  = 11
min_voices = 3
max_voices = 7
max_doublings = 3
```

Wider than jazz piano's 48–84 (spec 04) and narrower than pop synth's 36–96 (spec 03).
E24's four-voice reference bound of MIDI 48–72 is exceeded in both directions for the
same reason as pop synth: a pad inside 48–72 is a slow piano.

Two anti-drift compensations, since nothing physical constrains this voicer:

- **Bottom-voice bound.** `min(midi)` must lie in `[anchor_center - 14, anchor_center]`.
  Pads drift by the bottom falling out; centroid alone will not detect it when the top
  spreads upward simultaneously.
- `w_drift = 0.95`, second only to pop synth.

The tension with spec 04 is deliberate and worth naming: jazz is the high-parsimony genre
(parent §7), and high parsimony is precisely the setting in which the parent's dev-note
drift failure occurs ("chords keep going up or down... to optimize parsimony"). A
high-parsimony voicer with *no physical register constraint* is the worst combination in
the system, which is why this voicer carries both an explicit clamp and a bottom-voice
bound rather than relying on parent §4.1's claim that PLR chains bound register for free.

---

## 3. Tone selection

Inherits spec 04 §3 nearly verbatim:

```
p_omit_fifth    = 0.45    # lower than jazz piano's 0.60: pads want some width
root_double_p   = 0.15
root_omission_p = 0.55    # given bass_module_active; gated per spec 04 §3.1
```

- **Root-omission gate.** All three conditions from spec 04 §3.1 apply unchanged:
  `bass_module_active`, `bass_interval == 0`, and the no-manufactured-ambiguity check
  (rootless `Cmaj7` ≡ `Em7`; rootless `C7` ≡ `Em7♭5`). Spec 04 §6.2's branch A/B
  resolution applies. This is non-negotiable and is inherited, not re-derived.
- `root_omission_p = 0.55` is lower than jazz piano's 0.70 because a pad has more voices
  available and less need to economize, and because a sustained rootless pad is more
  ambiguous over time than a struck rootless piano chord that decays.
- **Shell skeleton** per spec 04 §3.2: 3rd + 7th first, extensions layered above. The
  difference is realization — the shell is *spread*, not placed in a left hand (§4).

---

## 4. Candidate source

`free_placement` with a spread shell:

1. Place the shell (3rd + 7th) with the two tones **7 to 19 semitones apart**, rather than
   in the close LH position jazz piano uses. This single change is what makes the voicer
   sound like a pad rather than a piano transcription.
2. Layer extensions above the shell's upper tone, or between the shell tones when the
   spacing allows ≥ 4 semitones of clearance.
3. Optionally add up to `max_doublings` octave copies of the root (when present), the 5th,
   or the DCT — same restricted doubling target list as spec 03 §4, and for the same
   reason: doubling a non-DCT extension thickens the ambiguous region.

### 4.1 Controlled cluster exemption

This is the one voicer that may deliberately produce close-interval upper structures —
the modern jazz-keyboard sound of stacked 4ths and 9th/3rd clusters. Spec 07 §7.3's
semitone-cluster separation rule (≥ 13 semitones between semitone-adjacent pitch classes)
is relaxed to **≥ 11 semitones**, and only under all of:

- the cluster lies entirely **above MIDI 64**;
- neither cluster member is the DCT;
- the chord has at least 5 sounded voices;
- at most one such cluster per voicing.

Below MIDI 64 the full ≥ 13 rule applies with no exemption. The rationale is acoustic, not
stylistic: low semitone clusters in a sustained pad produce the sustained masking that
N19's mixed-audio confusions live in (Fig. 6 — maj7→maj, dom7→maj, m7♭5→min/dom7 all
worsen substantially on complex versus isolated audio), whereas high clusters are
perceptually resolved and are a real, recorded jazz-keyboard sound.

---

## 5. Parsimony

```
tau     = fitted; expect ≈ 2.5-3.5
w_vl    = 1.1
w_drift = 0.95
w_space = 0.8
w_div   = 1.1
w_role  = 0.8
plr_bonus = 2.0
unmatched_penalty = 1.5
vl_target_mean = 7.0
vl_target_sd   = 4.0
```

`vl_target_mean = 7.0` sits between jazz piano's 6.0 and E24's real-music 8.37 (E24
Table 4). Jazz tone selection wants smoothness; pad realization wants width and octave
doubling, which adds motion. 7.0 is where those two land, and it keeps the six voicers
spanning a genuine range — 6.0 / 7.0 / 7.5 / 8.4 / 9.5 / 11.0 — centred near E24's
measured 8.37 ± 4.23 rather than clustered on it.

That spread across voicers *is the design*, and it comes straight from parent §4.4:
voice-leading distance was only a small predictor of perceptual discriminability in E24
(~3% odds change per semitone, Table 3), so "a spread of VL distances in the training set
is more valuable for ACR robustness than uniformly minimal voicing." No single voicer can
provide that spread; the ensemble does.

`plr_bonus = 2.0` retains spec 04's PLR and Cube-Dance machinery (Cohn 1998 §II, §IV) at
reduced weight — the transformations still shape triad and 4-27 transitions, but the
pad's octave doublings mean the realized voicings are never literal parsimonious moves.

---

## 6. Disambiguation policy

```
dct_mode_weights = {"top": 0.40, "isolated": 0.30, "octave": 0.30}
```

The `octave` weight is high (matching pop synth, unlike jazz piano's 0.05) because a pad
has the voice budget for it, and because octave-doubling is one of the few ways a
sustained pad can make an extension cut through a mix without simply being loudest.

Hazards specific to this voicer:

- **Rootless + pad sustain.** The most dangerous combination in the system: a rootless
  voicing whose pitch-class set names a different chord, sustained for a whole bar. Spec
  04 §3.1 condition 3 is enforced with **no relaxation-ladder exemption at all** — not
  even step 7. If the check fails, branch A (keep the root) is forced; branch B is
  unavailable to this voicer unless the DCT is the top voice *and* octave-doubled.
- **Cluster-adjacent DCT.** The §4.1 exemption explicitly excludes the DCT. A DCT inside a
  permitted upper cluster is a rejected candidate.
- **Doubling washout.** When the DCT is octave-doubled, both copies must satisfy predicate
  (b) independently (same rule as spec 03 §6) — a masked lower copy adds energy at the
  confusing frequency without adding clarity.
- **Bass-module collision.** When `bass_module_active`, no voice below
  `anchor_center - 10` may sound a pitch class other than the root, 5th, or 7th. A pad 3rd
  or 9th in the bass register against the bass module's root is mud.

---

## 7. `role_penalty`

| condition | penalty |
|---|---|
| Shell tones closer than 7 or wider than 19 semitones | 1.0 |
| Total ambitus < 22 semitones (sounds like a keyboard voicing, not a pad) | 1.0 |
| Total ambitus > 45 semitones | 0.8 |
| More than one upper cluster (§4.1) | 1.6 |
| Any cluster below MIDI 64 | hard reject, not a penalty |
| 5th present *and* both 9th and 13th present | 0.7 |
| No voice within ±8 of `anchor_center` (hollow middle) | 0.9 |
| More than 3 doublings | 1.5 |

---

## 8. Diversity

`w_div = 1.1` — above jazz piano's 0.9, because this voicer's justification (§1) is that
it contributes a distinct voicing family, and `w_div` is what stops it settling into one
comfortable pad shape.

Signature bands:

```
low  : 40-53
mid  : 54-69
high : 70-91
```

**Additional gates**, beyond spec 07 §8.4:

- Rootless and rooted voicings each ≥ 20% of emitted voicings for chord types with an
  active `seventh` (mirrors specs 04 and 05).
- Shell-spacing variety: the distribution of shell interval width (7–19 semitones) must
  have normalized entropy ≥ 0.6. A voicer that always spreads the shell by exactly an
  octave is one signature pretending to be many.
- Voicings with and without an octave-doubled DCT each ≥ 20% of extended-chord voicings.

---

## 9. Validation

Inherits spec 07 §13, plus:

1. **Shell integrity.** Every emitted voicing of a chord with an active `seventh`
   contains both the 3rd and the 7th (or the sus tone in place of the 3rd) — same hard
   assertion as spec 04 §9.1.
2. **Shell spread.** Shell interval within 7–19 semitones on ≥ 95% of emitted voicings.
3. **Rootless legality and no manufactured ambiguity.** Spec 04 §9.2 and §9.3 enforced
   identically, with the added restriction that branch B requires a top-voice,
   octave-doubled DCT.
4. **Cluster legality.** Zero clusters below MIDI 64; zero clusters containing the DCT;
   at most one cluster per voicing; cluster members ≥ 11 semitones apart. Hard assertions.
5. **Bottom-voice anchoring.** `min(midi)` within `[anchor_center - 14, anchor_center]`
   for every emitted chord.
6. **Calibration.** Mean normalized VL distance in 7.0 ± 0.25, SD ≥ 4.0; strictly above
   jazz piano's 6.0 and strictly below pop guitar's 9.5.
7. **Cross-voicer distinctness.** Over the same input song, the signature sets produced by
   `jazz-synth` and `jazz-piano` overlap on < 25% of chords. If they overlap more, this
   voicer is not earning its place as a separate voicing family and its §1 justification
   fails. This test is the whole reason the voicer exists and is run in CI, not by hand.
8. **Drift.** Song-wide centroid range ≤ 22 semitones (2 × `drift_tol`), and no monotone
   centroid trend: the Spearman correlation between chord index and centroid must have
   |ρ| < 0.3 over each song. This is the direct statistical test for the parent's
   dev-note failure mode, applied to the voicer most at risk of it.

---

## References

Cohn (1998) §II, §IV; Eitel et al. (2024) Tables 3–4; Nadar et al. (2019) §E1/E2, Fig. 6;
Ostermann et al. (2023) §3.3, §4.3 (element generators sharing a voicing method while
differing in realization); `voicing_algorithm_spec.md` §5, §6, §7.
