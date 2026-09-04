# Melody generation over non-diatonic harmony and acoustic interference

This report summarizes research relevant to adding melody generation to this
repository. It focuses on two related but different questions:

1. How should a symbolic melody be generated when the chord progression is
   chromatic, borrowed, extended, or otherwise not cleanly diatonic?
2. When the result is rendered to audio, under what conditions can the melody
   mask the accompaniment or make chord recognition less reliable, and what
   alternatives are available?

The research does not specify one complete implementation for this project.
The recommendations below distinguish findings demonstrated in the cited work
from engineering interpretations for the current codebase.

## Executive findings

1. **Do not make a new hard key decision at every chord change.** A chord is
   compatible with several keys, and chromatic chords can have a local
   function without replacing the global tonic. Greedy per-chord key
   estimation would create artificial tonal resets and would make the
   generator less able to represent secondary dominants, modal mixture,
   tonicization, and other chromatic practice.
2. **Use a global tonic/mode as a weak prior plus explicit chord-event
   conditioning.** Strong metric positions should generally favor active chord
   members, including extensions and alterations. Home-scale notes, held tones,
   and controlled chromatic notes can fill weaker positions when they have a
   plausible approach, passing, neighbor, enclosure, or resolution role. For
   synthetic progressions selected to cover rare chords, the global key may be
   only a scaffold and must not override the event label.
3. **Treat tension as a sequence property, not a note-label property.**
   Harmonic function, sensory dissonance, and horizontal motion all contribute
   to perceived tension. A non-chord tone is not automatically wrong; an
   unresolved or metrically overexposed non-chord tone is the more relevant
   failure mode.
4. **Use a sequence model or whole-phrase scorer.** The predecessor's contour,
   rhythm, phrase copying, range correction, and cadence ideas are reusable,
   but its pitch selection is effectively anchored to the final chord rather
   than conditioned on the active chord at each slot.
5. **Melody is not inherently acoustic interference.** Masking depends on
   spectral overlap, register, loudness, timbre, temporal overlap, and the
   listening or recognition task. A high, sparse, spectrally distinct melody
   can be salient while a dense melody in the chord register can distort
   chroma or mask chord evidence.
6. **Keep chord labels authoritative and melody conditions explicit.** The
   `ChordEvent` sequence must not be changed, rejected, or locally relabeled
   because of melody generation. Melody should be a controlled augmentation
   whose exposure, density, register, and masking condition are recorded.
   Chord recognition, melody extraction, and source separation should be
   evaluated as separate downstream tasks rather than treated as interchangeable
   measurements.
7. **Use source separation as an experiment, not an assumption.** Separation
   can make chord-relevant instruments more accessible, but artifacts and
   domain mismatch can also change the evidence presented to a recognizer.

## Evidence boundaries

The sources span several evidence classes:

- **Music-cognition and theory research** studies tonal context, expectation,
  tension, and voice leading. It supports design principles, not a project-
  specific probability table.
- **Symbolic generation and harmonization research** demonstrates ways to
  combine local and global dependencies, meter, chord structure, and melodic
  context. Much of it uses Western tonal or chorale data and may not transfer
  directly to this project's jazz and pop/rock distributions.
- **Audio MIR research** studies melody extraction, multipitch estimation, and
  chord estimation from recorded signals. Its errors and metrics are not
  substitutes for symbolic validation.
- **Masking and mix-clarity research** often uses controlled stimuli or
  perceptual models. It motivates measurable audio experiments but does not
  prove that a particular symbolic melody algorithm will improve a mix.
- **Source-separation tools and applied studies** provide useful baselines, but
  their performance is data- and domain-dependent.

There is no single cited paper that validates an extension-rich, probabilistic
melody generator over arbitrary non-diatonic chord events and then measures
its effect on chord-recognition audio. That gap should be handled with
controlled ablations in this repository.

## Current project and predecessor context

### What the predecessor provides

The predecessor analysis in
[`melody_docs/MELODY_REPORT.md`](melody_docs/MELODY_REPORT.md) describes the
active `MelodyBow` implementation. Its useful structural ideas are:

- a fixed 4/4 eighth-note raster;
- a compact diatonic relation representation;
- explicit rests and duration thinning;
- bounded contour generation;
- copied prefixes or motifs;
- instrument-range correction;
- a forced final cadence;
- serialization as a separate step after composition decisions.

Its main limitation for this project is harmonic conditioning. It builds a
chord raster, but ordinary notes do not use the chord at the current raster
position to choose pitches. The final chord mainly supplies a reference root
or fifth. The source TODO for distinguishing chord and non-chord tones is not
implemented. Other portability issues include recognition of only `w` and `h`
duration suffixes, static unseeded random state, and octave-shift range
correction that can create large jumps.

The predecessor is therefore a good source of **phrase and rendering
mechanics**, but not a sufficient model of melody over changing harmony.

### Current repository contracts

The current harmonic authority is `ChordEvent` in
[`voicing/types.py`](voicing/types.py), not a rendered chord label:

- `root_interval`, `triad`, `bass_interval`, and the extension slots are the
  normative fields;
- `resolve_degrees()` exposes the active root, third, fifth, seventh, ninth,
  eleventh, and thirteenth degrees after collision merging;
- `root`, `bass`, and `harte` are diagnostic/display fields and should not be
  parsed for voicing or melody decisions;
- `Song` currently stores a global `tonic_pc`, but not an explicit mode or
  local-key timeline;
- durations include `ww`, `w.`, `w`, `h.`, `h`, `q.`, and `q`;
- the renderer and arpeggio scheduler use a sixteenth-note grid;
- the voicer may omit roots or extensions, so the source chord contract and
  the realized register are not always identical;
- `VoicedChord` contains realized MIDI notes, roles, diagnostics, and the
  voicer's DCT-protection result.

This implies a two-level harmonic interface:

1. Use the source `ChordEvent` and `resolve_degrees()` for the intended
   harmonic vocabulary.
2. Use `VoicedChord` only as optional realized-register and provenance context.

If the project wants melodies that follow what is actually audible rather than
what the source event requests, the melody stage must explicitly choose between
the source-degree contract and the realized-voicing contract. It should not
silently assume that every extension survives voicing.

## Long-tail objective: melody as a controllable augmentation

The primary output of this repository is a labeled chord progression for
long-tail chord-recognition data. Melody therefore has a different role from
the primary composition in a melody-generation system: it is a **conditional
augmentation of an already selected chord event**.

The invariant is:

```text
ChordGenerator output and rare-feature quotas
    -> authoritative ChordEvent sequence
    -> chord-aware melody variant
    -> matched rendered audio and manifest metadata
```

The melody stage must not:

- resample, remove, or relabel a rare chord because a melody is difficult to
  generate;
- replace a non-diatonic event with a locally diatonic interpretation;
- change the chord-event duration or event boundaries;
- silently use a melody note to compensate for an extension that the realized
  chord voicing failed to sound.

If no musically acceptable candidate is available, use an explicit fallback
such as a rest or held note and record the fallback. Preserving the labeled
rare event is more important than forcing every event to contain a busy
melody.

### Per-event conditioning for rare chords

For each event, derive and retain:

- the full chord-type tuple and active pitch classes;
- the active extension profile;
- the DCT and any secondary differentiators;
- the source chord index and sixteenth-note timeline;
- the realized-voicing pitch set and any extension omissions;
- a melody exposure condition and its random seed.

Use the active `ChordEvent` degrees as the primary melody vocabulary. The
global tonic and mode provide continuity when they are meaningful, but they
are not a legality filter. This is especially important for a progression
containing many rare or non-diatonic events: the melody should be able to
target an altered degree without first inventing a new key.

For example, in a C-major context, an `A7(b9)` event has active pitch classes
`A, C#, E, G, Bb`. A melody can expose `C#` or `Bb` on selected accents and
resolve them toward neighboring material without declaring a D-major key for
that event. The altered chord remains the label-bearing object.

### Melody variants should be balanced

A single melody policy is not sufficient for recognition training. Render
matched variants from the same chord sequence:

| Variant | Recognition role |
|---|---|
| Neutral | Adds realistic activity with limited chord-specific cues |
| DCT/extension exposed | Provides a clear positive example of the rare differentiator |
| DCT/extension withheld | Tests whether the chord track carries the label independently |
| Chromatic distractor | Tests robustness to plausible non-chord material |
| Register-overlap or dense | Tests masking and chord confusion |
| Oracle stems | Separates symbolic label difficulty from mix interference |

The proportions of these variants should be controlled independently of chord
rarity. Otherwise a recognizer can learn that "rare chord" means "dense,
chromatic, high-register melody" rather than learning the chord evidence.

This structure also lets one rare chord event produce several audio conditions
without changing the Stage 1-3 chord distribution. Variants derived from the
same base progression must remain in the same train/validation/test split to
avoid leakage.

## Research on non-diatonic harmony and melody

### Tonal context, key ambiguity, and expectation

#### Krumhansl and Kessler (1982)

**Krumhansl, C. L., and Kessler, E. J.** "Tracing the Dynamic Changes in
Perceived Tonal Organization in a Spatial Representation of Musical Keys."
*Psychological Review*, 89(4), 334-368.
[DOI: 10.1037/0033-295X.89.4.334](https://doi.org/10.1037/0033-295X.89.4.334)

Probe-tone experiments show that perceived tonal organization is contextual:
the same pitch can have different stability depending on the surrounding
tonal material. The key space also supports nearby and changing tonal
interpretations rather than a single context-free label.

**Implication for this project:** a pitch should receive a context-dependent
stability score. A local chromatic chord should not force all melody choices
to be regenerated under a newly declared key.

#### Temperley (2002)

**Temperley, D.** "A Bayesian Approach to Key-Finding." In *Music and
Artificial Intelligence*, LNAI 2445, Springer, 195-206.
[Author PDF](https://davidtemperley.com/wp-content/uploads/2015/11/temperley-maai.pdf);
[DOI: 10.1007/3-540-45722-4_18](https://doi.org/10.1007/3-540-45722-4_18)

The model frames key finding as probabilistic inference from pitch-class
evidence and key profiles. This framing naturally represents uncertainty and
can be extended with a prior that discourages unnecessary changes of key.

**Implication:** if modulation support is needed, infer key states over bars or
phrases with a persistence penalty, rather than selecting an unrelated key for
each chord. The posterior can influence melody probabilities without becoming
a hard per-event scale.

#### Bigand, Parncutt, and Lerdahl (1996)

**Bigand, E., Parncutt, R., and Lerdahl, F.** "Perception of Musical Tension in
Short Chord Sequences: The Influence of Harmonic Function, Sensory Dissonance,
Horizontal Motion, and Musical Training." *Perception & Psychophysics*, 58(1),
125-141.
[DOI: 10.3758/BF03205482](https://doi.org/10.3758/BF03205482)

The experiments separate several contributors to perceived tension:
harmonic function, sensory dissonance, and horizontal motion. No single
chord-label or interval statistic explains the whole response.

**Implication:** melody scoring should include both vertical compatibility
with the active chord and horizontal movement/resolution. A chromatic
approach tone can be acceptable when its short-horizon resolution lowers
expected tension; a stable chord tone can still be awkward if it creates a
bad leap or phrase shape.

#### Cheung et al. (2024)

**Cheung, V. K. M., Harrison, P. M. C., Koelsch, S., Pearce, M. T.,
Friederici, A. D., and Meyer, L.** "Cognitive and Sensory Expectations
Independently Shape Musical Expectancy and Pleasure." *Philosophical
Transactions of the Royal Society B*, 379(1895), 20220420.
[DOI: 10.1098/rstb.2022.0420](https://doi.org/10.1098/rstb.2022.0420)

This work reports that cognitive expectation and sensory expectation make
independent contributions to musical expectancy and pleasure. Surprise is
therefore not equivalent to error, and a musically effective event may violate
one expectation while satisfying another.

**Implication:** do not make the generator maximize diatonic or chord-tone
membership alone. Keep a controllable tension budget and reward intelligible
resolution, phrase position, and contrast.

### Voice leading and chromatic chords

#### Tymoczko (2011)

**Tymoczko, D.** *A Geometry of Music: Harmony and Counterpoint in the
Extended Common Practice*. Oxford University Press, 2011.
[Oxford University Press record](https://global.oup.com/academic/product/a-geometry-of-music-9780195336672);
ISBN 978-0-19-533667-2.

The geometric treatment relates chord types through voice-leading distance and
shows how smooth motion, consonance, scale structure, and melodic fluency
interact in extended tonal practice.

**Implication:** chromaticity should be a soft cost, not a blanket rejection.
Candidate melodies should prefer manageable motion and coherent transformations
between adjacent harmonic events while still allowing chromatic targets.

#### Callender, Quinn, and Tymoczko (2008)

**Callender, C., Quinn, I., and Tymoczko, D.** "Generalized Voice-Leading
Spaces." *Science*, 320(5877), 346-348.
[DOI: 10.1126/science.1153021](https://doi.org/10.1126/science.1153021)

The paper formalizes voice-leading spaces in which distances correspond to
efficient transformations between multisets of pitches. It provides a general
way to reason about continuity even when chord sets are not diatonic.

**Implication:** add interval continuity or voice-leading distance to the
sequence score. Do not require every melody note to belong to one global scale
if a small chromatic motion gives a better transition.

#### Gotham (2023)

**Gotham, M. R. H.** "Chromatic Chords in Theory and Practice." *Proceedings
of the 24th International Society for Music Information Retrieval Conference*,
2023.
[Open proceedings record](https://zenodo.org/records/10265275)

Gotham compares terminology and practice across theoretical traditions and
uses corpus evidence to examine how chromatic chord categories actually occur.
The paper highlights disagreement over definitions and warns against treating
informal chromatic-harmony categories as universally standardized.

**Implication:** "chord-scale" assignments should be style-dependent heuristics,
not an unquestioned universal rule system. The generator should retain the
explicit chord event and let a style model decide how strongly to favor
diatonic, modal-mixture, or altered tones.

### Probabilistic melody/harmony generation

#### Paiement, Eck, and Bengio (2006)

**Paiement, J.-F., Eck, D., and Bengio, S.** "Probabilistic Melodic
Harmonization." In *Advances in Artificial Intelligence*, LNAI 4013, Springer,
218-229.
[DOI: 10.1007/11766247_19](https://doi.org/10.1007/11766247_19);
[Google Research record](https://research.google/pubs/probabilistic-melodic-harmonization-2/)

The authors use a graphical probabilistic model with a structured chord
representation and show why dependencies beyond a simple local Markov chain
matter for harmonization. The model is intended for both analysis and
generation conditioned on a melody.

**Implication:** represent chord roles explicitly and score multiple time
scales. A melody generator should condition on the current chord, nearby
chords, and phrase-level structure rather than drawing independent notes.

#### Allan and Williams (2004)

**Allan, M., and Williams, C. K. I.** "Harmonising Chorales by Probabilistic
Inference." *Advances in Neural Information Processing Systems 17*, 2004.
[Proceedings record](https://papers.nips.cc/paper/2714-harmonising-chorales-by-probabilistic-inference)

This work learns a probabilistic harmonization model from Bach chorales and
uses inference to generate plausible hidden harmonizing parts. It demonstrates
that learned local transitions and voice-part dependencies can produce useful
part writing without encoding every rule as a hard constraint.

**Implication:** use hard constraints only for invalid timing, range, or
serialization cases. Treat chord-tone preference, leap size, parallel motion,
and tension as weighted terms that can trade off against each other.

#### Simon, Morris, and Basu (2008)

**Simon, I., Morris, D., and Basu, S.** "MySong: Automatic Accompaniment
Generation for Vocal Melodies." *Proceedings of CHI 2008*, 725-734.
[DOI: 10.1145/1357054.1357169](https://doi.org/10.1145/1357054.1357169)

MySong demonstrates a practical accompaniment system driven by a sung
melodic input, with style and user interaction in the loop. Its central
lesson for this project is architectural: melody and harmony are most useful
when represented as explicit, time-aligned conditioning variables rather than
as unrelated random tracks.

**Implication:** preserve an explicit event timeline and make the relationship
between a melody event and its source chord inspectable in the output.

#### Yeh et al. (2021)

**Yeh, Y.-C., et al.** "Automatic Melody Harmonization with Triad Chords: A
Comparative Study." *Journal of New Music Research*.
[DOI: 10.1080/09298215.2021.1873392](https://doi.org/10.1080/09298215.2021.1873392)

This comparative study evaluates automatic harmonization methods under a
triad-chord target. It is useful evidence that melody/harmony systems need
explicit model and evaluation choices, but its triad-only target is narrower
than this repository's seventh, ninth, eleventh, and thirteenth slots.

**Implication:** use the study as a baseline for triad-conditioned experiments,
not as evidence that a triad-only representation is sufficient for the current
project.

#### Wu et al. (2021, preprint)

**Wu, S., Yang, Y., Wang, Z., Li, X., and Sun, M.** "Generating Chord
Progression from Melody with Flexible Harmonic Rhythm and Controllable
Harmonic Density." arXiv:2112.11122.
[arXiv record](https://arxiv.org/abs/2112.11122);
[DOI: 10.48550/arXiv.2112.11122](https://doi.org/10.48550/arXiv.2112.11122)

The AutoHarmonizer work separates harmonic rhythm from the note grid and
exposes harmonic-density control. It shows why a system should not assume
that one chord change per fixed melody interval is the only meaningful timing
relationship.

**Implication:** preserve the current event durations and sixteenth grid, but
allow melody scheduling to span chord boundaries, hold notes across changes,
and expose density as a parameter.

#### Tsushima et al. (2018)

**Tsushima, H., Nakamura, E., Itoyama, K., and Yoshii, K.** "Interactive
Arrangement of Chords and Melodies Based on a Tree-Structured Generative
Model." *Proceedings of ISMIR 2018*, 145-151.
[ISMIR proceedings PDF](https://archives.ismir.net/ismir2018/paper/000145.pdf)

The tree-structured model uses hierarchical context for interactive
arrangement of chords and melodies. Its relevance is the use of phrase or
section structure to coordinate musical decisions at multiple resolutions.

**Implication:** phrase copying from the predecessor can be retained, but
copied material should be rescored or regenerated against the new local chord
sequence instead of being copied as unqualified pitch values.

## Research on acoustic interference, masking, and alternatives

### Auditory masking and mix clarity

#### Chon and Huron (2014)

**Chon, S. H., and Huron, D.** "Does Auditory Masking Explain the High Voice
Superiority?" In *Proceedings of the 13th International Conference on Music
Perception and Cognition*, 2014.
[Author PDF](https://ccrma.stanford.edu/~shchon/pubs/ICMPC2014_Masking.pdf)

The paper examines whether auditory masking helps explain why the highest
voice is often perceived as the melody. It discusses masking interactions for
complex tones and concludes that masking can provide part of the explanation,
but is not a complete account of melodic salience.

**Implication:** placing a melody above the accompaniment can improve
salience, but "highest" is not a sufficient rule. Register, spectrum, and
timbre should be measured together.

#### Parker and Fenton (2021)

**Parker, A., and Fenton, S.** "Musical Mix Clarity Prediction Using
Decomposition and Perceptual Masking Thresholds." *Applied Sciences*, 11(20),
9578.
[DOI: 10.3390/app11209578](https://doi.org/10.3390/app11209578)

This work predicts mix clarity using source decomposition and perceptual
masking thresholds. It treats clarity as a measurable consequence of
spectral and perceptual interactions rather than assuming that adding a part
is harmless.

**Implication:** an audio-aware melody experiment should measure spectral
overlap and masking proxies, not only symbolic chord-tone rates. A simple
first control is melody register, velocity, note density, and instrument
timbre; a later control can use a masking model.

#### Nakayama et al. (2024)

**Nakayama, M., Hayashi, T., Takahashi, T., and Nishiura, T.** "Comfortable
Sound Design Based on Auditory Masking with Chord Progression and Melody
Generation Corresponding to the Peak Frequencies of Dental Treatment
Noises." *Applied Sciences*, 14(22), 10467.
[DOI: 10.3390/app142210467](https://doi.org/10.3390/app142210467)

This application combines auditory-masking considerations with generated
chords and melody to make an external noise source less objectionable. It is
not a general melody-composition benchmark, but it demonstrates a useful
alternative framing: choose musical material with knowledge of the interfering
spectral content.

**Implication:** if generated audio will be tested against a known noise or
recognition condition, expose an optional spectral interference cost instead
of trying to infer it from symbolic diatonicity.

### Melody extraction and multipitch analysis

#### Salamon et al. (2014)

**Salamon, J., Gomez, E., Ellis, D. P. W., and Richard, G.** "Melody
Extraction from Polyphonic Music Signals: Approaches, Applications, and
Challenges." *IEEE Signal Processing Magazine*, 31(2), 118-134.
[DOI: 10.1109/MSP.2013.2271648](https://doi.org/10.1109/MSP.2013.2271648);
[author copy](https://www.ee.columbia.edu/~dpwe/pubs/SalGER14-melody.pdf)

This survey describes melody extraction as a difficult estimation problem
because of overlapping harmonics, accompaniment, vibrato, timbre variation,
and ambiguous melody definitions. It also distinguishes melody extraction
from related tasks such as multipitch estimation.

**Implication:** a generated melody stem should be an oracle reference for
evaluation. Do not judge symbolic melody quality only by running a melody
extractor over the final mix.

#### Klapuri (2008)

**Klapuri, A.** "Multipitch Analysis of Polyphonic Music and Speech Signals
Using an Auditory Model." *IEEE Transactions on Audio, Speech, and Language
Processing*, 16(2), 255-266.
[DOI: 10.1109/TASL.2007.911306](https://doi.org/10.1109/TASL.2007.911306)

Klapuri models the auditory periphery and iteratively detects and cancels
fundamental frequencies to estimate multiple concurrent pitches. This is an
alternative to assuming that one dominant F0 is the only relevant evidence.

**Implication:** if the evaluation asks whether the mix contains the intended
melody and chord pitches, multi-F0 or salience-based analysis is more
appropriate than a monophonic melody extractor alone.

#### Bittner et al. (2017)

**Bittner, R. M., McFee, B., Salamon, J., Li, P., and Bello, J. P.** "Deep
Salience Representations for F0 Estimation in Polyphonic Music." *Proceedings
of ISMIR 2017*.
[ISMIR proceedings PDF](https://archives.ismir.net/ismir2017/paper/000063.pdf);
[companion code](https://github.com/rabitt/ismir2017-deepsalience)

The authors learn a salience representation with a fully convolutional model
for both melody tracking and multi-F0 estimation. The work addresses the
scarcity of fully labeled polyphonic F0 data with a large semi-automatically
generated training set.

**Implication:** salience and multi-F0 baselines can be useful for rendered
audio experiments, but their performance should be reported separately from
symbolic ground truth and checked for domain mismatch.

### Chord estimation and joint context

#### McVicar et al. (2014)

**McVicar, M., Santos-Rodriguez, R., Ni, Y., and De Bie, T.** "Automatic Chord
Estimation from Audio: A Review of the State of the Art." *IEEE/ACM
Transactions on Audio, Speech, and Language Processing*, 22(2), 556-575.
[DOI: 10.1109/TASLP.2013.2294580](https://doi.org/10.1109/TASLP.2013.2294580)

This survey covers chroma features, HMM and dynamic Bayesian approaches,
datasets, evaluation, and the limitations caused by corpus bias and
overfitting. Chord estimation is a temporal inference problem, not merely a
frame-by-frame pitch-class lookup.

**Implication:** evaluate chord stability over time and preserve transition
context. A melody that adds a few chromatic pitch classes may be harmless in
one context and change the recognized label in another.

#### Mauch and Dixon (2010)

**Mauch, M., and Dixon, S.** "Simultaneous Estimation of Chords and Musical
Context From Audio." *IEEE Transactions on Audio, Speech, and Language
Processing*, 18(6), 1280-1289.
[DOI: 10.1109/TASL.2009.2032947](https://doi.org/10.1109/TASL.2009.2032947)

The system jointly estimates chords and musical context, using contextual
information to improve interpretation rather than treating each chord frame
as independent.

**Implication:** a future local-key model can be an additional latent,
phrase-level context variable coupled to chord events and melody candidates.
It should not replace the explicit chord event or be recomputed greedily for
every event.

### Source separation as an alternative front end

#### Rafii et al. (2018)

**Rafii, Z., Liutkus, A., Stoter, F.-R., Mimilakis, S. I., FitzGerald, D., and
Pardo, B.** "An Overview of Lead and Accompaniment Separation in Music."
*IEEE/ACM Transactions on Audio, Speech, and Language Processing*, 26(8),
1307-1335.
[DOI: 10.1109/TASLP.2018.2825440](https://doi.org/10.1109/TASLP.2018.2825440)

This survey frames lead/accompaniment separation as its own problem and
reviews supervised, unsupervised, and score-informed approaches. Separation
can make a lead or accompaniment more accessible, but it is not equivalent
to perfect isolation.

**Implication:** compare full-mix chord recognition with accompaniment-only,
oracle-stem, and estimated-separated conditions. Report separation quality
and recognition quality independently.

#### Stoter et al. (2019): Open-Unmix

**Stoter, F.-R., Uhlich, S., Liutkus, A., and Mitsufuji, Y.** "Open-Unmix - A
Reference Implementation for Music Source Separation." *Journal of Open
Source Software*, 4(41), 1667.
[DOI: 10.21105/joss.01667](https://doi.org/10.21105/joss.01667)

Open-Unmix supplies a reproducible, reference implementation for neural music
source separation.

**Implication:** it is a reasonable experimental baseline, not a required
dependency of symbolic generation. Any use in the evaluation should preserve
the original mix and record the model/version and stems used.

#### Hennequin et al. (2020): Spleeter

**Hennequin, R., Khlif, A., Voituret, F., and Moussallam, M.** "Spleeter: A
Fast and Efficient Music Source Separation Tool with Pre-Trained Models."
*Journal of Open Source Software*, 5(50), 2154.
[DOI: 10.21105/joss.02154](https://doi.org/10.21105/joss.02154)

Spleeter provides fast pretrained separation models and is useful for
repeatable baseline experiments. As with any estimated stem, it can introduce
artifacts and remove or redistribute evidence.

**Implication:** use it only as one front-end condition. Do not use separated
audio to overwrite the symbolic labels or to claim that masking was solved.

#### Mitoma and Furuya (2025, preliminary applied study)

**Mitoma, A., and Furuya, K.** "Accuracy Improvement of Automatic Chord
Recognition with Source Separation Preprocessing." *Proceedings of APSIPA ASC
2025*, paper P307.
[Proceedings PDF](https://apsipa.com/proceedings/2025/papers/APSIPA2025_P307.pdf)

This applied study reports improved chord-recognition results in its
experimental setup after emphasizing chord-relevant instruments through source
separation and remixing. Its venue and task-specific setup make it promising
supporting evidence rather than a universal result.

**Implication:** source separation is worth testing for this corpus, especially
when the generated melody is intended to be a nuisance variable, but the
experiment must include unseparated and oracle-stem controls.

## Recommended architecture for long-tail melody augmentation

The melody implementation should be downstream of chord generation and should
produce a reproducible, inspectable variant of each completed chord sequence.
It is not a second harmonic generator. Chord-event selection, rare-feature
quotas, transition support, and chord labels remain owned by the existing
pipeline.

### 1. Represent tonal context explicitly

Add an optional song-level mode or pitch-class scale to the symbolic contract.
A global tonic without a mode is not enough to distinguish, for example, major,
natural-minor, harmonic-minor, and modal material. For the long-tail generator,
mode should be a configurable prior, not a hard constraint: some quota-driven
progressions will not have a strong global tonal interpretation.

For modulation support, add a separate phrase- or bar-level local-key model:

```text
key_state_t = global/phrase key and mode
emission     = chord pitch-class evidence + optional melody evidence
transition   = key persistence plus modulation cost
```

Use Viterbi or posterior inference over a phrase/bar timeline only when the
progression has enough evidence for a tonal context. The local key should
influence candidate weights, not erase or relabel the source `ChordEvent`.

### 2. Generate from the active chord event

For each sixteenth-note onset, construct candidates from:

- active `resolve_degrees()` members, including extensions and alterations;
- global home-scale notes;
- held notes from the previous event;
- controlled chromatic approach, passing, neighbor, and enclosure notes;
- rests.

Assign role metadata such as `chord_tone`, `extension`, `scale_tone`,
`approach`, `passing`, `neighbor`, `held`, or `rest`. This makes later
validation and error analysis possible.

Strong metric positions should favor the root, third, fifth, or a
stylistically stable extension according to the selected exposure condition.
For rare or altered events, the DCT and active extension may receive higher
weights in an exposed variant and lower weights in a withheld variant. Weak
positions can carry tension when the candidate resolves over a short horizon.
If the event is a no-chord, prefer a rest or a conservative held-note policy
and record that choice.

### 3. Score complete sequences

A practical first model can use beam search, Viterbi, or weighted sequential
sampling. A candidate sequence score should combine:

```text
melodic contour and interval continuity
+ active chord-degree compatibility
+ extension/alteration role
+ global mode or local-key fit
+ metrical strength
+ tension followed by resolution
+ phrase repetition and cadence
+ register and instrument range
+ density/rest preference
+ optional realized-voicing overlap or masking cost
```

The last term should be optional and should not replace the symbolic harmonic
score. Add a hard label-preservation invariant outside the musical score:
melody selection must not alter the chord sequence, event duration, or
recognition label. A melody can intentionally use a chord extension that the
voicer later omits; that mismatch should be visible in diagnostics rather than
silently treated as audible evidence.

### 4. Reuse predecessor structure selectively

Retain the predecessor's bounded contour, rests, phrase copying, and final
cadence as proposal mechanisms. Before accepting a copied or cadential note,
rescore it against the active chord and mode. Replace hard octave jumps with
nearest-register candidate selection or a scored octave option.

Use the repository's seeded `random.Random` path rather than class-load
`SecureRandom` state so a song, melody, and render can be reproduced from the
manifest seed.

### 5. Keep rendering and validation explicit

A future melody track would likely need:

- a melody event scheduler modeled on the existing structured arpeggio
  scheduler in `chord_module.py`;
- a renderer integration in `render.py`;
- manifest records for melody configuration, seed, exposure condition, and
  source event mapping;
- instrument-specific melodic tessitura in `instruments.py`;
- validation in `eda/validate_rendered_corpus.py` for timeline coverage,
  range, no-chord behavior, note density, harmonic role metadata, and
  label-preservation invariants.

The serialized output should retain both source-chord index and onset/duration
in sixteenth units. This avoids recovering alignment from rendered audio.

## Proposed experiments and ablations

### Symbolic melody quality

For every base progression, keep the chord JSON and event timing identical
while changing only the melody condition. Compare:

1. global key/mode plus chord-conditioned candidates;
2. greedy key estimation at each chord change;
3. phrase-level HMM/Viterbi key inference;
4. chord tones only;
5. chord tones plus home-scale notes;
6. chord tones plus controlled chromatic passing/approach notes;
7. independent slot sampling;
8. sequence scoring with phrase and resolution terms.

Measure:

- duration-grid coverage and timing validity;
- note/rest density and range violations;
- chord-tone and stable-extension rate by metric strength;
- non-chord-tone resolution rate within a short horizon;
- interval and leap distributions;
- repeated-note and phrase-reuse rates;
- cadence completion;
- no-chord violations;
- source-degree versus realized-voicing mismatch.

The metrics should be reported by genre and by chord type, especially altered
and extension-rich events. A high global chord-tone rate can hide a generator
that fails specifically on chromatic chords.

For the rare-feature strata, also report DCT/extension exposure, realized
extension retention, melody fallback rate, and whether the melody caused an
otherwise stable chord recognizer to confuse the target with a neighboring
label. These should be reported separately for natural and quota-targeted
events.

### Chord-recognition and masking impact

Render matched versions with the same chord events:

1. accompaniment only;
2. accompaniment plus a sparse high-register melody;
3. accompaniment plus a dense melody in the chord register;
4. accompaniment plus controlled chromatic melody;
5. oracle separated accompaniment and melody stems;
6. estimated stems from one or more source-separation baselines.

Measure separately:

- chord recognition accuracy at root, triad, seventh, and full extension
  levels;
- frame-level and event-level chord boundary errors;
- key/context inference stability;
- melody F0 precision, recall, voicing accuracy, or multi-F0 metrics;
- spectral-overlap or perceptual-masking proxies;
- source-separation quality where applicable;
- human ratings of melody salience, clarity, tension, and musical plausibility.

The key comparison for the original corpus purpose is not "melody sounds
good" versus "melody sounds bad." It is whether adding a controlled melody
changes chord-recognition evidence in a predictable way, and whether the
change is caused by the symbolic notes, the mix, or the separation front end.

## Realism versus recognition usefulness

This approach can be realistic enough to help chord-recognition models, but
that outcome is not automatic. There are two distinct objectives:

- **Musical plausibility:** a listener accepts the melody as a coherent line,
  including its rests, contour, register, and tension/resolution.
- **Recognition utility:** the melody broadens the acoustic conditions under
  which the labeled chord must be recognized without changing the label.

A melody that agrees with the chord at every onset may make rare labels easier
to recognize, especially when it exposes a DCT or altered extension. However,
if that exposure is deterministic, it creates label leakage: a recognizer can
memorize a melody pitch or density pattern instead of learning the chord
voicing.

A melody that disagrees with the chord is not necessarily unrealistic. Passing,
neighbor, approach, suspension, and enclosure tones are normal when their
meter, duration, register, and resolution make the tension intelligible.
Conversely, an arbitrary sustained clash, excessive same-register density, or
unresolved altered pitch can make the audio ambiguous rather than useful
training data. "Disagrees with the chord" therefore needs to be represented as
an explicit tension condition, not treated as one binary quality judgment.

The most useful first target is a **controlled realism envelope**:

1. generate chord-aware melodies with bounded range, density, intervals, and
   short-horizon resolution;
2. vary DCT exposure and chromatic density independently of chord rarity;
3. preserve the exact chord event and label for every variant;
4. evaluate rare-chord recall on accompaniment-only, full-mix, and oracle-stem
   conditions;
5. use human plausibility ratings or a reference melody distribution before
   treating the result as a realistic replacement for recorded melodies.

If full-mix recognition degrades in a dense or chromatic condition, that is not
necessarily a failed generator. It may be a valuable robustness condition,
provided the condition is labeled and its confusion profile is measured. The
failure is only a problem if the project intended the melody to be transparent
or if the mix no longer contains enough audible evidence to justify the chord
label.

## Decision on per-chord key estimation

Estimating a key at each chord change and treating that interval as diatonic is
not a strong default for this project:

- one chord generally supports multiple tonal interpretations;
- a single event does not establish tonic, scale, or harmonic function;
- chromatic chords often derive their meaning from neighboring events;
- resetting the scale can make a melody follow arbitrary local labels instead
  of the progression's global function;
- it conflicts with the current generator's explicit chromatic and extended
  chord vocabulary.

A better default is:

```text
global tonic + explicit mode
    -> active ChordEvent conditioning
    -> optional local-key posterior
    -> sequence-level melody scoring
```

Use per-chord key-like information only as a soft feature, such as a
secondary-dominant or tonicization hypothesis with a short duration and
neighboring-chord support. Promote it to a true key change only when a
phrase-level model finds sustained evidence and pays for the transition.

## Bottom line

The strongest implementation path is a symbolic, chord-event-conditioned
melody augmenter with a weak global tonal context, optional phrase-level
local-key inference, weighted chromatic tension/resolution, explicit sequence
scoring, and multiple recorded exposure conditions. The predecessor's rhythmic
and phrase mechanics can accelerate the first prototype, but its final-chord
anchoring should be replaced rather than copied.

For audio evaluation, keep the melody as a separately labeled stem and treat
masking, chord recognition, melody extraction, and source separation as
distinct experiments. This preserves the project's symbolic ground truth while
making acoustic interference measurable instead of guessing that every
non-diatonic note is either harmless or harmful.
