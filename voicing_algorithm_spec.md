# Chord Voicing Algorithm Specification

## 0. Purpose and scope

This spec covers the voicing layer that sits on top of an existing chord-progression
generator (HMM-based, non-diatonic, tuned to over-produce rare extensions). The chord
module decides *what* chord plays and its inversion/bass note; percussion, bass, and
melody modules are out of scope. This spec covers only *how the chord tones are
distributed across register and instrument voices*.

**Primary goal:** produce realistic-sounding audio containing rare/extended chords
(9ths–13ths) to use as training data for automatic chord recognition (ACR) models,
particularly to cover chord types that are underrepresented in real-world corpora
(Nadar, Abeßer & Grollmisch, 2019 — hereafter **N19** — note that seventh chords are
"heavily underrepresented" in standard ACR training sets, motivating their own synthetic
IDMT-SMT-CHORDS dataset). This reframes "realistic" away from genre-idiomatic sparseness
and toward *acoustic plausibility with reliably audible extensions* — the voicer's job is
not to make pop songs sound authentically simple, it's to make rare chords sound like
something that could really have been played and recorded.

**Minimum deliverable:** 6 voicers (2 genres × 3 instruments): pop-piano, pop-guitar,
pop-synth, jazz-piano, jazz-guitar, jazz-synth.

---

## 1. Design constraint derived directly from ACR failure modes (N19)

This is the single most important constraint in this spec, because it comes directly
from evidence about what breaks ACR models, not from music-theory intuition.

N19 trained CNNs on synthetic isolated-chord recordings across 7 chord types (maj, min,
maj7, min7, dom7, m7♭5, and the "5" power chord) and found systematic confusions that
trace directly to **shared chord tones between chord types**:

- maj7 confused with maj and min (they share the root, 3rd, and 5th; only the 7th
  differs)
- m7♭5 confused with min and dom7 (shared tones across the two triads embedded in the
  tetrachord)
- On mixed/complex audio (not isolated chords), these confusions got substantially worse:
  maj7→maj, dom7→maj, and m7♭5→min/dom7 were the dominant error modes (Fig. 6, N19).

**Design rule (applies to every voicer, every genre):** whenever a chord tone is the
*sole differentiator* of the chord's extended quality (the 7th in a 7th chord, the 9th
in a 9-chord not already implying a 7th, etc.), that tone must be voiced in a registrally
exposed position — on top, or otherwise not doubled/obscured by an adjacent chord tone a
semitone or two away that could read as ambiguous in a mixed-audio context. This is a
hard constraint, not a style preference, because N19's data shows this is exactly where
recognition fails in real (non-isolated) audio.

**Corollary:** don't just include the extension tone — include it in more than one
voicing/register position across the dataset (see §2), because N19's cross-test
experiments (E1/E2: train on piano voicings, test on non-piano voicings and vice versa)
showed a **sharp drop in F-score** when a model only ever saw one family of voicings at
training time (F84 dropped from 0.97 on matched voicings to 0.49–0.58 on unseen voicing
types). A voicer that always resolves an extension the same way (e.g., always on top,
always same octave) is actively bad for this use case, even though it might sound "more
correct" musically.

---

## 2. Training-data diversity requirements (N19 + Majchrzak & Mańdziuk, 2025)

Majchrzak & Mańdziuk (hereafter **MM25**) trained ACR transformers (BTC, Harmony
Transformer) on AAM (algorithmically-composed synthetic audio), Billboard (real pop), and
Winterreise (real classical), and found:

- AAM-trained models transfer reasonably well to Billboard (real pop) — AAM and Billboard
  chord-progression statistics are "closer to each other" than AAM and Winterreise are —
  supporting the general viability of synthetic-composition-derived training data for pop
  ACR specifically (MM25 §5.2, §6).
- Adding AAM to a small real-music training set measurably improved performance on the
  real-music test set (Table 3, experiments 8/12 vs. 9 alone) — i.e., synthetic data is
  useful as an *augmentation*, not just a standalone set.
- N19 separately found that models trained on **isolated chords** generalize poorly to
  **complex mixed-instrument recordings** (E4, E7: F24 collapses from ~0.9 to ~0.2–0.4),
  confirming a known pattern from automatic music transcription — models need to be
  trained on data of *similar acoustic complexity* to the target use case.

**Implications for the voicer design:**

1. **Diversify voicing per chord-type-and-instrument combination**, not just per chord
   instance. The same `Dm9` chord should not always resolve to the same relative voicing
   shape across the dataset — vary register, doubling, and (for guitar) shape/inversion
   systematically, mirroring N19's own dataset design (their IDMT-SMT-CHORDS piano MIDI
   "includes all chord types in all possible root note positions and inversions"; their
   guitar MIDI is barré-chord-based across multiple root strings).
2. **Render into full-mix context, not isolated stems**, at least for a meaningful
   fraction of the dataset — since your setup already has percussion/bass/melody modules
   running alongside chords, this is a natural fit and directly addresses the isolated-vs-
   mixed generalization gap N19 identified.
3. **Use real/humanized samples, not static velocity-locked MIDI renders.** This isn't
   just an aesthetic nicety — Ostermann et al. (2023, hereafter **AAM-paper**) explicitly
   designed their Recording Studio pipeline around "intelligent samplers, which aim to
   simulate human playing behavior" rather than static samples, as a first-order design
   principle for realism (§3.3).

---

## 3. Shared architecture (all six voicers)

Every voicer is built from the same two-stage pipeline, parameterized differently per
genre/instrument. This mirrors the separation used in the AAM-paper's own "element
generator" design (chord pad generator vs. arpeggiation generator, differing in
temporal pattern but sharing the same underlying voicing method — AAM-paper §4.3).

### Stage 1 — Tone selection
Given the chord symbol (root + quality + available extensions from the HMM output) and
genre density policy, decide which of the up to 7 possible scale positions (root, 3rd,
5th, 7th, 9th, 11th, 13th) are actually sounded, and decide the **root-doubling /
omission policy** relative to whatever the bass module is doing (see §6). Extensions are
never silently dropped for "idiomatic simplicity" in this system — per §0, the whole
point is to keep rare extensions audible. What *can* vary by genre is doubling density
and which non-essential chord tones (e.g. 5th) get thinned to make room.

### Stage 2 — Register / spread realization
Given the selected pitch classes (and, separately, the disambiguation-critical tone
identified in §1), assign actual octave placement per voice, subject to:
- an **anchor window** tied to the progression's key center (not per-chord recentering)
  to prevent register drift — the AAM-paper flags this exact failure mode from its own
  HMM-generated, non-diatonic progressions ("chords keep going up or down... to optimize
  parsimony"), and the Eitel et al. (2024, hereafter **E24**) stimulus-generation
  pipeline demonstrates a concrete working implementation: chord sequences realized as
  four-voice harmony bounded between MIDI 48–72, computed via a minimum-voice-leading
  algorithm (`hrep`/`minVL`, Harrison & Pearce) rather than free-floating octave choice.
  That's a directly reusable reference implementation, not just a citation.
- a **parsimony parameter**, i.e. how much voice-leading distance (summed semitone
  movement across voices, Tymoczko 2006) is tolerated/targeted between consecutive
  chords. This is implemented formally below (§4), not as an ad hoc heuristic.
- the **disambiguation-tone exposure rule** from §1, which can override pure
  voice-leading-minimization when the two conflict (rare, but when it happens,
  disambiguation wins, since misleading ACR training data is worse than a slightly less
  smooth voicing).

---

## 4. Parsimony / voice-leading engine

### 4.1 Triads: neo-Riemannian PLR as the formal mechanism
Cohn's (1998) survey (hereafter **C98**) is the right formal tool for the "smooth
voice-leading" end of the parsimony spectrum. The three contextual inversions —
**P** (parallel, semitone move, shared perfect-fifth edge), **L** (leading-tone exchange,
semitone move, shared minor-third edge), **R** (relative, whole-step move, shared
major-third edge) — are each defined as the *minimal* single-voice move between two
triads sharing two common tones (C98 §II). Chained P/L/R operations trace the
**hexatonic** (all-semitone-motion) and **octatonic** (alternating semitone/whole-step)
systems C98 details in §III, which map directly onto a bounded, non-drifting register
region — i.e., this machinery gives you the anchor-window property "for free" as a side
effect of using minimal transformations, rather than needing a separately bolted-on
register clamp.

**Recommended implementation:** represent the parsimony parameter as a literal graph
distance over the Tonnetz (Fig. 2 in C98) rather than a semitone-count heuristic. A
"parsimony = 1" jazz voicer moves along adjacent Tonnetz triangles (P/L/R); a "parsimony
= low" pop voicer is permitted larger jumps (composite transformations, e.g. the
redundant **D** = L∘R, or arbitrary transposition) when the section calls for it (chorus
lift, register jump for energy).

### 4.2 Sevenths: generalizing PLR to set-class 4-27
C98 notes (§IV, referencing Douthett & Steinbach's then-unpublished work, later
published as Douthett & Steinbach 2003 in the same *Journal of Music Theory* issue) that
the same parsimonious-voice-leading apparatus extends to **set-class 4-27** — dominant
and half-diminished seventh chords — via "Cube Dance," which charts minimal-motion
relationships among triads and 4-27 tetrachords together. This is directly usable for
jazz ii-V-I voice leading (m7♭5 → dom7 → maj/min7 chains) and gives a principled way to
choose, e.g., which inversion of the next dom7 chord minimizes total voice movement from
the current m7♭5 — rather than hand-coding "resolve down a fifth, keep common tones" as
a special case.

For extensions beyond the seventh (9th–13th) not covered by 4-27 theory, fall back to
direct Tymoczko-style minimum-voice-leading-distance computation (§4.3) rather than a
closed-form transformation set, since no equivalently compact group-theoretic apparatus
is cited in the sources reviewed here for higher tetrachords/pentachords.

### 4.3 General case: Tymoczko minimum voice-leading distance
For chord-to-chord transitions not covered by a closed-form PLR/4-27 transformation
(secondary dominants, chromatic mediants, arbitrary jumps to rare extended chords), use
direct minimum-voice-leading-distance computation between pitch-class sets (Tymoczko,
2006, as operationalized in E24's Voice-Leading Distance measure and the `minVL` package
used in both E24 and the AAM-paper's realization pipeline). This computes, for each
candidate register realization, the total semitone distance summed across voices between
the previous chord's realized pitches and the candidate next-chord pitches, and picks
(or biases toward, depending on the parsimony parameter) the minimum.

### 4.4 Calibration reference values (E24)
E24's empirical data on real four-chord progressions (Billboard-corpus-derived, N=5076
comparison items) gives concrete numbers to calibrate the parsimony parameter against
real music rather than picking thresholds arbitrarily:

- Mean voice-leading distance between a target chord and its same-position placeholder
  in a near-identical progression: **8.37 semitones** (SD 4.23) summed across 4 voices
  (E24 Table 4).
- Voice-leading distance was a *significant but comparatively small* predictor of
  perceptual discriminability relative to spectral pitch-class distance and chord
  surprisal (E24 Table 3, final model) — each 1-semitone increase in VL distance only
  changed odds of correct discrimination by ~3%. This suggests voice-leading smoothness
  alone is not the dominant perceptual/acoustic signal; **don't over-invest** in
  hyper-parsimonious voicing at the expense of variety (§2) — a spread of VL distances in
  the training set is more valuable for ACR robustness than uniformly minimal voicing.

---

## 5. Root-doubling / bass-coordination policy

Recap of the earlier design decision, now formalized as a per-voicer parameter table
(no new citation needed beyond AAM-paper's confirmation that a dedicated, independently-
voicing bass module is a standard, separable architecture — AAM-paper §4.3, "bass
generator provides the root notes... independent element generator"):

| Voicer | Default when bass module active | Notes |
|---|---|---|
| Pop piano | Double root, often 8ve above bass | idiomatic weight; still expose extension per §1 |
| Pop guitar | Double root (open/barre shapes commonly include it) | root omission rare in pop guitar shapes |
| Pop synth | Genre/section-dependent; more omission in verse pads, more doubling in choruses | use probabilistic toggle, not fixed |
| Jazz piano | Omit root by default (rootless voicings) | classic comping move; root doubling only for solo/unaccompanied contexts |
| Jazz guitar | Omit root by default | mirrors piano; shell voicings (3rd+7th) as skeleton |
| Jazz synth | Follow piano/guitar convention unless used as a pad/lead | — |

Each row's "default" should be a **probability**, not a hard rule (per earlier design
discussion), so the dataset contains variety here too — this is the same diversity
argument as §2, applied to root-doubling specifically.

---

## 6. Per-instrument realization constraints

### Piano (pop + jazz)
Most flexible; no hard physical constraint beyond the register window. Two-hand split
(LH shell = root+7th or bare root/5th; RH = upper structure/extensions) is the natural
default for jazz; pop piano can use full block-chord RH+LH doubling. Use this as the
"reference" voicer against which guitar/synth constraints are described as restrictions.

### Guitar (pop + jazz)
Needs a playability filter: max ~4–5 fret span across strings, max 6 simultaneous notes
(often fewer, since strings get muted), and voicing choice constrained to a shape
library rather than free pitch placement — pick the nearest playable shape to the
previous shape (a discrete analogue of §4's voice-leading minimization, over shapes
instead of continuous pitch space). N19's own guitar MIDI generation used **barré chord
voicings rooted on the low E, A, and D strings**, which is a reasonable, empirically-used
minimum viable shape set to start from; extend with drop-2/drop-3 voicings for jazz 7th
chords (standard jazz-guitar practice, not covered by the reviewed papers but consistent
with N19's own extension into 4-note chord types). Root omission is expected/idiomatic
when the bass module is active (§5).

### Synth (pop + jazz)
No physical constraint; the open design question is whether a given synth voicer
patch is a pad (wide spread, doubled octaves, sparse rhythm), a lead (single line,
irrelevant to this spec), or a comping instrument (follows piano/guitar conventions).
Recommend defaulting synth voicers to the pad role for both genres, since that's the
most chord-voicing-relevant use case, and treating "lead" as out of scope for this
module.

---

## 7. Genre-specific tone-selection policy

### Pop/rock
Per the reframed goal (§0), **do not reduce extensions for idiomatic sparseness.** Where
this spec's earlier draft said "bias toward triads and simple 7ths," that guidance is
superseded: extensions present in the HMM output should be voiced and exposed per §1.
What remains genre-specific is *density of doubling* (more root/5th doubling than jazz)
and *typical voice-leading distance distribution* (wider spread of VL distances is fine
and even desirable — see §4.4 — since real pop production varies a lot in this
dimension across sections).

### Jazz
Shell voicings (3rd+7th) as the structural skeleton, extensions layered on top, rootless
voicings the default when bass is active (§5), and lower target voice-leading distance
on average (biased toward the PLR/4-27 minimal-motion end of §4.1–4.2) — but still with
enough spread in the training data (§4.4, §2) that the model doesn't only ever see
maximally smooth transitions.

---

## 8. Open parameters to tune empirically (not resolved by the literature reviewed)

- Exact probability distributions for root-doubling toggles per genre/section (§5) —
  no paper reviewed here gives empirical pop/jazz doubling-frequency data; this needs
  either corpus analysis (e.g. a Billboard-derived study analogous to E24's stimulus
  corpus, but for voicing rather than progression) or your own judgment calibration.
- Exact fraction of dataset that should be isolated-chord vs. full-mix rendering (§2.2) —
  N19's own dataset used isolated recordings; MM25 used full mixes (AAM); neither paper
  directly studies the optimal ratio for a *combined* dataset targeting robustness across
  both isolated and mixed contexts.
- Guitar shape-library coverage for extended chords (9th–13th) — not addressed by any
  reviewed source; jazz-guitar voicing references (drop-2/drop-3 practice) are pedagogical
  rather than research literature and would need to be sourced separately if formal
  backing is wanted.

---

## References

- Ostermann, F., Vatolkin, I., & Ebeling, M. (2023). AAM: A dataset of Artificial Audio
  Multitracks for diverse music information retrieval tasks. *EURASIP Journal on Audio,
  Speech, and Music Processing*, 2023:13.
- Eitel, M., Ruth, N., Harrison, P., Frieler, K., & Müllensiefen, D. (2024). Perception of
  chord sequences modeled with prediction by partial matching, voice-leading distance, and
  spectral pitch-class similarity. *Music & Science*, 7.
- Cohn, R. (1998). Introduction to neo-Riemannian theory: A survey and a historical
  perspective. *Journal of Music Theory*, 42(2), 167–180.
- Rohrmeier, M. (2011). Towards a generative syntax of tonal harmony. *Journal of
  Mathematics and Music*, 5(1), 35–53. (Tangential to voicing; relevant if function-aware
  extension defaults are added later — see prior discussion.)
- Nadar, C.-R., Abeßer, J., & Grollmisch, S. (2019). Towards CNN-based acoustic modeling
  of seventh chords for automatic chord recognition. *Proceedings of the 16th Sound and
  Music Computing Conference (SMC)*, Málaga, Spain.
- Majchrzak, M., & Mańdziuk, J. (2025). Training chord recognition models on artificially
  generated audio. arXiv:2508.05878.
- Tymoczko, D. (2006). The geometry of musical chords. *Science*, 313(5783), 72–74.
  (Cited via C98 and E24; not independently reviewed in full here.)

---

## Original Unstructured Dev Notes:

BG info: voicing algo will be run on top of a chord module that already generates chord progressions. Chord module is accompanied with a percussion module, bass module, and melody module, but we are only implementing enhancements to the chord module.

Two genres:
pop_rock
jazz

Three instrument types:
Piano
Guitar
Synth

Seven possible notes played:
Root
3rd
5th
7th
9th
11th
13th

Rules external to voicing:
If the bass module is run, it will always play the root 
Inversions are decided before the voicing algo runs, so the bass note played via bass module will not be changed by the voicing algo
Voicing algo has control over the voicing of all chord notes including bass if decided, but wont change whether the bass module plays the bass note independently. The first note voiced by this algo (i.e. the next note played after the bass) does not also have to be the bass note
The generated progressions that these algos will take as input are non-diatonic since they were generated from an HMM; in the past, I’ve struggled with register drift as chords keep going up or down in relation to the original chord to optimize parsimony. We will have to decide to what degree parsimony is implemented in every algo (some will have high parsimony, some low), but always remember to keep all chord voicings anchored to our original chord, area around the original chord, or octave the original key would be found in
The voicing feel for pop and jazz will be different, but fair warning that rare extensions (9ths-13ths) will still be found in pop progressions since I’ve tuned the HMM to output rare chords more often. Definitely design the pop voicers with the mindset of how actual pop songs get chords voiced (usually with basic triads and 7ths), but keep in mind that it will end up probably handling more tones too.

Goal: To have at least 1 voicer per genre and instrument type.
