# Spec 02 — Pop/Rock Guitar Voicer

**Module id:** `pop-guitar` · **Key:** `("pop_rock", "guitar")`
**Implements:** spec 07 (`VoicerPolicy`), with `candidate_source = shape_library`.
**Parent:** `voicing_algorithm_spec.md` §5, §6 (Guitar), §7 (Pop/rock), §8.

---

## 1. Role and the central restriction

Guitar is the only voicer where pitch placement is **not free**. Parent §6:

> Needs a playability filter: max ~4–5 fret span across strings, max 6 simultaneous
> notes (often fewer, since strings get muted), and voicing choice constrained to a
> **shape library** rather than free pitch placement — pick the nearest playable shape to
> the previous shape (a discrete analogue of §4's voice-leading minimization, over shapes
> instead of continuous pitch space).

So spec 07's engine runs unchanged, but its candidate set is a finite library of fingered
shapes rather than an enumeration of octave assignments. Everything downstream — VL
distance, DCT filtering, diversity accounting, softmax — operates identically on the
concrete MIDI note sets that the shapes produce.

N19's own guitar MIDI generation used **barré chord voicings rooted on the low E, A, and
D strings**, which parent §6 endorses as "a reasonable, empirically-used minimum viable
shape set to start from." This voicer takes that as the literal seed of its library and
extends it, because N19's shape set covers only their 7 chord types and this system must
handle 9ths through 13ths.

---

## 2. Instrument model

Standard tuning, MIDI open strings, string 6 (low) → string 1 (high):

```
E2=40, A2=45, D3=50, G3=55, B3=59, E4=64
```

```
window_lo = 40    # E2, open low E — hard physical floor
window_hi = 76    # E5, 12th fret of string 1; above this is lead territory, out of scope
drift_free = 5
drift_tol  = 10
max_fret        = 12     # pop guitar rarely comps above the 12th
max_fret_span   = 4      # parent §6: "max ~4-5 fret span"; 5 allowed with penalty
max_voices      = 6
min_voices      = 3
```

A shape is a 6-tuple over `{fret_int, "x"}`, one entry per string, plus a barre flag.
Realized MIDI = `open_string + fret` for every non-`x` entry, ascending.

---

## 3. Shape library

### 3.1 Structure

```python
Shape = {
  "id": "E-barre-maj7",
  "root_string": 6 | 5 | 4,
  "frets": [0, 2, 1, 1, 0, "x"],     # relative to the root fret
  "degrees": ["1","5","7","3","5",None],
  "barre": True,
  "span": 2,
  "chord_types": [("major","7","N","N","N")],
  "tags": ["barre","full","pop"],
}
```

Shapes are stored **root-relative**; transposition to a concrete root is a scalar add,
and any transposition placing a required fret outside `[0, max_fret]` is discarded at
enumeration time. Open-position shapes (`open_only = True`) are stored absolutely and
only offered for their native roots — a real constraint that correctly makes some keys
sound different from others on guitar, which is itself acoustic diversity worth having.

### 3.2 Required coverage

| family | shapes required | source |
|---|---|---|
| Barre major / minor / dom7 / maj7 / min7 / m7♭5 | E-, A-, D-string roots | N19 guitar MIDI design |
| Open-position C, A, G, E, D and their minor/7th forms | native roots only | standard pop practice |
| Power chords (`triad == "5"`) | E-, A-, D-string roots, 2- and 3-note | corpus has 93 `5` events |
| `sus2` / `sus4` | E-, A-string roots | corpus has 310 sus events |
| Diminished / augmented | E-, A-string roots, 4-note dim7 movable | corpus has 444 events |
| add9 / maj9 / min9 / dom9 | E-, A-string roots, ≥ 3 shapes each | required by parent §0 |
| 11 / #11 / 13 / b13 | E-, A-string roots, ≥ 2 shapes each, 5th omitted | see §3.3 |

Parent §8 flags this honestly as an open problem: *"Guitar shape-library coverage for
extended chords (9th–13th) — not addressed by any reviewed source."* This spec therefore
states the construction rule rather than citing one.

### 3.3 Construction rule for extended shapes

A guitar cannot sound 7 degrees on 6 strings under a 4-fret span. Where a chord's active
degree count exceeds what any library shape can hold, apply this **fixed omission
priority**, most-omittable first:

```
5th  >  root  >  9th  >  11th  >  3rd  >  13th  >  7th
```

The 5th goes first (it carries no quality information for major/minor triads, spec 07
§4.1). The root goes second **only when `bass_module_active`** — the bass module is
already sounding it (parent §5). The 7th is last because it is the DCT in the majority of
extended chords, and the 13th is second-to-last because a 13th chord voiced without its
13th is a lie about the label.

This is the **one place in the entire system where an extension may be dropped**, and it
is permitted only because the alternative is emitting an unplayable shape and labelling
the audio as guitar. Every such drop must:

- be logged as `EXTENSION_DROPPED` with chord, voicer, and degree;
- never drop the DCT (spec 07 §5) — if the priority list would reach the DCT, the shape
  is rejected instead;
- keep the dataset-wide drop rate under **2% of chords**, checked in validation (§9.4).
  Above that, the library has insufficient coverage and must be extended, not the
  threshold raised.

### 3.4 Muting

Strings marked `x` are muted and contribute no pitch. Parent §6 notes chords are "often
fewer [than 6 notes], since strings get muted"; the library encodes this per shape rather
than deriving it, because which strings a guitarist mutes is a property of the fingering.

---

## 4. Tone selection

```
p_omit_fifth    = 0.35    # higher than pop piano: strings are scarce
root_double_p   = 0.60
root_omission_p = 0.08
max_doublings   = 2
```

Parent §5, pop-guitar row: *"Double root (open/barre shapes commonly include it) — root
omission rare in pop guitar shapes."* Note that on guitar, doubling is largely *decided by
the shape*, not chosen: an E-barre major already contains the root three times. So
`root_double_p` is applied as a **shape-selection bias** (a `role_penalty` term favouring
root-containing shapes) rather than as an added voice. `root_omission_p = 0.08` covers
the genuinely idiomatic rootless cases (upper-string triad grips over a bass note).

---

## 5. Candidate source and shape voice leading

```
candidate_source = shape_library
```

For each chord:

1. Look up shapes whose `chord_types` contains the event's `(triad, 7th, 9th, 11th, 13th)`
   tuple, or a superset-compatible tuple after §3.3 omission.
2. For each shape × each viable root fret in `[0, max_fret]`, realize the MIDI set.
3. Discard: any realization outside `[window_lo, window_hi]`, span > 5, or violating
   spec 07 §7.3's low-interval limit (rarely binding on guitar — the tuning enforces
   wide low intervals already, which is precisely why guitar voicings are clear in a mix).
4. Pass the survivors to the standard engine.

**Shape distance.** Parent §6 asks for "the nearest playable shape to the previous shape."
Implemented as an additional cost term folded into `w_role`:

```
shape_distance(s, s_prev) = |root_fret(s) - root_fret(s_prev)|
                          + 2 * (s.id != s_prev.id)
                          + |muted_strings(s) Δ muted_strings(s_prev)|
```

This is a genuine discrete analogue of VL minimization — a hand that stays in position
and changes grip minimally — and it is scored *alongside*, not instead of, spec 07 §6.1's
pitch-space VL distance. Both matter: the first is playability, the second is what the
listener and the ACR model hear.

---

## 6. Parsimony

```
tau     = fitted; expect ≈ 4.5-6.0   # highest of the six; the candidate set is small and discrete
w_vl    = 0.7
w_drift = 0.9
w_space = 0.3     # tuning already enforces most spacing
w_div   = 1.2
w_role  = 1.4     # shape distance lives here; playability dominates
plr_bonus = 0.5   # weak: the shape library, not PLR, determines what is reachable
vl_target_mean = 9.5
vl_target_sd   = 5.0
```

`vl_target_mean = 9.5` sits **above** E24's real-music 8.37 (E24 Table 4) deliberately.
Guitar voice leading is quantized by the fretboard: a chord change often forces a whole
grip to move several frets, producing larger summed motion than an idealized four-voice
realization would. Forcing this voicer down to 8.37 would push it toward a handful of
adjacent-position shapes and directly damage the diversity requirement. E24's own finding
that VL distance is only a weak perceptual predictor (~3% odds change per semitone,
Table 3) is the licence to let it float upward here.

`w_drift = 0.9` is comparatively high because the fretboard is the *easiest* place to
drift: transposing a movable barre shape up 7 frets is a single cheap operation, and
unchecked it walks the whole part into the 12th-fret region over a 48-chord song. The
anchor window is doing more work here than the parent's §4.1 claim that PLR gives it "for
free" — that claim holds for triads in free pitch space, not for fretted shapes, so this
voicer relies on spec 07 §7.2's explicit clamp.

---

## 7. Disambiguation policy

```
dct_mode_weights = {"top": 0.65, "isolated": 0.30, "octave": 0.05}
```

Heavily weighted toward `top`, because on a guitar the top voice is the highest
unmuted string and is both the easiest to control and the loudest in a mix. `octave` is
near-zero: octave-doubling an extension costs two strings the shape usually cannot spare.

Guitar-specific hazards, all enforced as hard filters:

- **The doubled-root maj7 trap.** Barre shapes contain up to three root copies. A maj7's
  7th sits a semitone below a root, and an E-barre maj7 with a root on string 2 directly
  above the 7th is unusable under spec 07 §5.2. Such shapes must be stored with that
  string muted, or excluded from `maj7` `chord_types` entirely.
- **The dropped-3rd power-chord trap.** Where §3.3 omission is applied, the 3rd is late
  in the priority list precisely because a shape reduced to root/5th/7th reads as a
  power-chord-plus-note and feeds N19's confusion between `5`, maj, and min.
- **Open-string ambiguity.** Open-position shapes frequently place the extension in an
  inner voice on an open string, where it is quiet. Extended-chord shapes tagged
  `open_only` must place the DCT on a fretted string 1 or 2.

---

## 8. Diversity

`w_div = 1.2`. The signature (spec 07 §8.1) is extended for guitar with the shape family:

```
signature(v) += ("root_string": s.root_string, "family": s.tags[0])
```

so that the same interval pattern played as an E-barre and as an A-barre counts as two
signatures — which is correct, since they differ in string timbre, doubling, and open-
string content, and N19's own dataset design varied exactly this axis (barré voicings
rooted on low E, A, and D).

**Coverage gate (spec 07 §8.4) applies with one addition:** every chord type with ≥ 20
corpus occurrences must be voiced from **at least 2 different `root_string` values**
across the dataset. A chord type that only ever appears as an E-shape is exactly the
single-voicing-family training data N19 showed collapses a model to F 0.49–0.58.

---

## 9. Validation

Inherits spec 07 §13, plus:

1. **Playability.** Every emitted voicing maps back to a library shape at a concrete
   root fret, with span ≤ 5, ≤ 6 sounded strings, and no fret outside `[0, 12]`. Round-
   trip assertion: `realize(shape, fret) == emitted.midi`.
2. **String legality.** Every pitch is producible on its assigned string
   (`pitch - open_string ∈ [0, max_fret]`), and no two sounded pitches share a string.
3. **Library completeness.** For all 9600 corpus chord events across both label sets, at
   least one shape (after §3.3 omission) exists. Any miss is a library gap and fails CI.
4. **Drop rate.** `EXTENSION_DROPPED` fires on < 2% of chords dataset-wide, and never for
   a DCT. Report per chord type; a single type above 20% indicates a targeted library gap.
5. **No fretboard drift.** Mean `root_fret` over the first 8 chords of a song and over the
   last 8 differ by ≤ 3 frets. This is the guitar-specific restatement of the parent's
   dev-note drift concern and is stricter than the generic centroid test.
6. **Root-string spread.** The §8 gate passes.

---

## References

Nadar et al. (2019) — guitar MIDI barré design, §E1/E2, Fig. 6; Eitel et al. (2024)
Tables 3–4; Cohn (1998) §II; `voicing_algorithm_spec.md` §6, §8 (open problem: extended
guitar shape coverage).
