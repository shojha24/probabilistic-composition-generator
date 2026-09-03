# Spec 03 — Pop/Rock Synth Voicer

**Module id:** `pop-synth` · **Key:** `("pop_rock", "synth")`
**Implements:** spec 07 (`VoicerPolicy`), `candidate_source = free_placement`.
**Parent:** `voicing_algorithm_spec.md` §5, §6 (Synth), §7 (Pop/rock).

---

## 1. Role

Parent §6 settles the open design question up front:

> No physical constraint; the open design question is whether a given synth voicer patch
> is a pad (wide spread, doubled octaves, sparse rhythm), a lead (single line, irrelevant
> to this spec), or a comping instrument. **Recommend defaulting synth voicers to the pad
> role** for both genres, since that's the most chord-voicing-relevant use case, and
> treating "lead" as out of scope.

So: pop synth is a **pad**. Wide spread, octave doubling, long sustained notes, no hand
constraint at all. This makes it the most register-permissive of the six voicers and the
one most exposed to drift, since nothing physical bounds it.

It also makes it the most valuable voicer for N19's cross-voicing-generalization problem.
A pad's characteristic wide-open spacing is an entirely different *voicing family* from
close-position piano and fretted guitar shapes. N19's E1/E2 experiments measured F84
dropping from 0.97 on matched voicings to 0.49–0.58 on unseen voicing families; a pad
voicer is the cheapest way to add a third family to the training distribution, and its
parameters below are tuned with that as the primary objective rather than genre idiom.

---

## 2. Register

```
window_lo = 36    # C2 — sub-bass pad territory; below this the pad fights the bass module
window_hi = 96    # C7 — top of usable pad shimmer
drift_free = 6
drift_tol  = 12
min_voices = 3
max_voices = 8    # highest of the six voicers
max_doublings = 4
```

This is the widest window in the system — E24's four-voice reference bound of MIDI 48–72
is deliberately blown out in both directions, because a pad that lives inside 48–72 is
just a slow piano. The wide window is the *point* of this voicer.

The cost is that spec 07 §7.2's drift machinery is load-bearing here in a way it is not
elsewhere. Three compensations:

- `drift_tol = 12` is a hard filter, and one octave is a real ceiling, not a suggestion.
- **Octave-band anchoring.** In addition to the centroid test, the pad's *lowest sounding
  voice* must remain within `[anchor_center - 16, anchor_center - 2]`. A pad drifts
  primarily by its bottom falling out, and centroid alone will not catch that when the
  top is simultaneously spreading upward.
- `w_drift = 1.0`, the highest of the six.

---

## 3. Tone selection

```
p_omit_fifth    = 0.10    # lowest of the six: pads want the 5th for width
root_double_p   = section-dependent, see §3.1
root_omission_p = section-dependent, see §3.1
```

Parent §5, pop-synth row: *"Genre/section-dependent; more omission in verse pads, more
doubling in choruses — use probabilistic toggle, not fixed."* This is the only row in the
parent's table that is explicitly conditioned on section, so this voicer is the one where
`section_profile` is mandatory rather than optional.

### 3.1 Section profile

```python
section_profile = {
  "verse":     {"root_double_p": 0.25, "root_omission_p": 0.35,
                "max_voices": 5, "spread": "open",   "anchor_shift": 0},
  "prechorus": {"root_double_p": 0.45, "root_omission_p": 0.20,
                "max_voices": 6, "spread": "open",   "anchor_shift": +2},
  "chorus":    {"root_double_p": 0.80, "root_omission_p": 0.05,
                "max_voices": 8, "spread": "wide",   "anchor_shift": +5},
  "bridge":    {"root_double_p": 0.35, "root_omission_p": 0.30,
                "max_voices": 6, "spread": "sparse", "anchor_shift": -4},
  "outro":     {"root_double_p": 0.55, "root_omission_p": 0.15,
                "max_voices": 7, "spread": "wide",   "anchor_shift": 0},
}
```

`spread` selects a spacing template applied as a `role_penalty` shaping term:

| `spread` | target adjacent-interval profile |
|---|---|
| `sparse` | 3–4 voices, adjacent gaps 7–14 semitones, at most one gap < 5 |
| `open` | adjacent gaps 5–12, no more than one gap < 4 |
| `wide` | ≥ 2 voices doubled at the octave; total ambitus ≥ 24 semitones |

`anchor_shift` is bounded to ±5 and reverts at section end, so a chorus lift cannot
accumulate into drift across a song — validated by spec 07 §13.7.

---

## 4. Candidate source

`free_placement`, with pad-specific enumeration:

1. Assign the required degrees to a "core" register band of `anchor_center ± 12`.
2. Optionally add up to `max_doublings` octave copies of the root, 5th, or the DCT
   (never of a semitone-adjacent pair member) anywhere in the window.
3. Apply the `spread` template as a candidate *generator* constraint, not just a
   penalty — otherwise the enumeration explodes. Typical post-filter counts: 300–900.

Doubling targets are deliberately restricted to root, 5th, and DCT. Octave-doubling a
9th, 11th, or b7 that is *not* the DCT thickens exactly the region that produces N19's
ambiguity, and doubling a 3rd in a wide pad produces a strident, unrepresentative timbre.

---

## 5. Parsimony

```
tau     = fitted; expect ≈ 5.0-7.0    # loosest continuous-pitch voicer
w_vl    = 0.5     # lowest of the six
w_drift = 1.0     # highest of the six
w_space = 0.9
w_div   = 1.3     # highest of the six
w_role  = 0.7
plr_bonus = 0.4
unmatched_penalty = 1.2   # low: pads add and drop voices freely
vl_target_mean = 11.0
vl_target_sd   = 6.0
```

The `w_vl` / `w_drift` inversion is the defining parameter choice of this voicer, and it
is a direct reading of two findings:

- E24 Table 3: voice-leading distance is a *significant but small* predictor of
  perceptual discriminability (~3% odds change per semitone), well behind spectral
  pitch-class distance and chord surprisal. Parent §4.4 concludes: "don't over-invest in
  hyper-parsimonious voicing at the expense of variety."
- N19 E1/E2: variety across voicing families is worth far more than smoothness.

So for the one voicer with no physical constraint forcing its hand, parsimony is
down-weighted and variety and register control are up-weighted. `vl_target_mean = 11.0`
sits well above E24's real-music 8.37 (Table 4), on purpose: pads legitimately make large
leaps between chords, and this voicer is the system's designated source of
*high*-VL-distance training examples, balancing the jazz voicers' low ones. Parent §4.4:
"a spread of VL distances in the training set is more valuable for ACR robustness than
uniformly minimal voicing."

`unmatched_penalty = 1.2` is low because a pad genuinely changes voice count between
chords — 5 voices on a triad, 8 on a 13th chord — and charging that heavily would push
the voicer toward a constant, uniform texture.

---

## 6. Disambiguation policy

```
dct_mode_weights = {"top": 0.35, "isolated": 0.35, "octave": 0.30}
```

The most evenly balanced of the six, because a pad can satisfy all three predicates
cheaply, and an even split maximizes the number of distinct exposure families this voicer
contributes (spec 07 §5.5).

Pad-specific hazards:

- **Sustained-cluster masking.** A pad's long sustain means partial overlap is
  continuous, not transient; a DCT a semitone from a neighbour is masked for the chord's
  entire duration. Spec 07 §7.3's semitone-cluster separation (≥ 13 semitones) is
  therefore a **hard** filter here with no relaxation-ladder exemption — this voicer
  opts out of ladder step 1 for that rule specifically.
- **Octave-doubling washout.** When the DCT is octave-doubled (branch c), both copies
  must satisfy predicate (b); a masked lower copy plus an exposed upper copy is worse
  than a single exposed copy, because the masked copy contributes energy at the confusing
  frequency without contributing clarity.
- **Sub-bass collision.** When `bass_module_active`, no pad voice below `anchor_center - 16`
  may sound a pitch class other than the root or 5th. A 3rd or b7 down at C2 against the
  bass module's root is mud, and mud is where N19's mixed-audio confusions live
  (Fig. 6: maj7→maj, dom7→maj, m7♭5→min/dom7 all worsen substantially on complex audio
  relative to isolated chords).

---

## 7. `role_penalty`

| condition | penalty |
|---|---|
| Adjacent-interval profile deviates from the active `spread` template | 0.3 per violating gap |
| Total ambitus < 19 semitones (pad sounds like a keyboard stab) | 1.0 |
| Total ambitus > 48 semitones | 0.8 |
| More than 4 doublings | 1.5 |
| A non-root, non-5th, non-DCT degree is octave-doubled | 1.2 |
| No voice within ±7 of `anchor_center` (hollow middle) | 0.9 |

---

## 8. Diversity

`w_div = 1.3`, highest in the system. This voicer's explicit job (§1) is to be the third
voicing family, and `w_div` is the lever that makes it cover its own space rather than
settling into one comfortable pad shape.

`octave_band(min(midi))` bands for signatures:

```
low  : 36-49
mid  : 50-64
high : 65-96
```

**Additional gate beyond spec 07 §8.4:** across the dataset, this voicer's emitted
`spread` classes must be distributed no more skewed than 60/25/15 across
`wide`/`open`/`sparse`. If section labels in the source data are absent or degenerate,
sample `spread` directly from that distribution rather than letting one class dominate.

---

## 9. Validation

Inherits spec 07 §13, plus:

1. **Bottom-voice anchoring.** `min(midi)` within `[anchor_center - 16, anchor_center - 2]`
   for every emitted chord — the drift test centroid alone cannot catch (§2).
2. **Section coherence.** Chorus centroids average ≥ 4 semitones above verse centroids
   within a song; the song-wide centroid range still ≤ 2 × `drift_tol`.
3. **Cluster separation.** Zero emitted voicings with two semitone-adjacent pitch classes
   less than 13 semitones apart. Hard assertion, since this voicer waives the relaxation
   ladder for that rule.
4. **Calibration.** Mean normalized VL distance in 11.0 ± 0.25, SD ≥ 6.0 — and, as a
   cross-voicer check, strictly greater than pop piano's 8.4 (spec 01) and jazz piano's
   6.0 (spec 04), so that the three together span a genuine range around E24's measured
   8.37 ± 4.23 rather than clustering on it.
5. **Spread distribution.** The §8 gate passes.
6. **Voice-count variety.** The distribution of `len(midi)` has entropy ≥ 0.7 normalized
   over the range 3–8; a pad that always plays 6 notes is a single voicing family wearing
   a costume.

---

## References

Nadar et al. (2019) §E1/E2, Fig. 6; Eitel et al. (2024) Tables 3–4; Ostermann et al.
(2023) §3.3 (intelligent samplers / human playing behaviour), §4.3 (chord pad generator
as a distinct element generator); `voicing_algorithm_spec.md` §5, §6, §7.
