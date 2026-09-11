# Spec 15: Reproducible Sound Design and ACR Corpus Production

**Status:** Proposed implementation scope
**Depends on:** [Spec 13.5 - Multitrack Rendering](13.5-multitrack-rendering.md), [Spec 14 - Melody Generation](14-melody-generation.md)
**Audience:** Generator, data, audio-rendering, and automatic chord-recognition (ACR) work

## 1. Purpose and decision summary

The generator now has a strong symbolic foundation for creating chord-conditioned
training examples. The remaining work is not primarily another note-generation
feature. It is the controlled conversion of those symbolic examples into
reproducible, auditable, perceptually varied audio without losing the label
contract that the symbolic generator established.

The central decision of this specification is:

> Treat sound design as a versioned data-generation pipeline, not as an
> untracked post-processing step.

The symbolic chord events remain the source of truth for intended labels. An
audio render is a separately versioned observation of those labels under a
specific renderer, asset set, mix, room, and playback condition. A label that
is symbolically correct is not automatically an audio label that an ACR model
can recover reliably after masking, reverberation, distortion, or codec
processing. The corpus must preserve both facts and measure the gap.

### 1.1 Current status at a glance

| Surface | Status | What is true today |
|---|---|---|
| Chord-event labels and provenance | Implemented | `ChordEvent` labels, source ordinals, keys, and hashes are carried through the symbolic pipeline. |
| Chord realization and voicing | Implemented | Voiced chords, voicing diagnostics, relaxed/fallback behavior, and instrument-aware catalogs exist. |
| Multitrack score contract | Implemented | V0 chords/arpeggios, V1 bass, optional V2 melody, and V9 percussion are emitted with synchronized sidecars. |
| Melody generation | Implemented as an opt-in V2 augmentation | Spec 14 contracts, profiles, local-key context, candidate decoding, quotas, no-chord handling, and provenance are present. |
| Symbolic validation and curation | Implemented | Corpus validation and diversity-aware curation cover score, MIDI-oriented metadata, tracks, and melody metadata. |
| Score-to-MIDI conversion | Implemented | `render.py --stage midi` invokes the seeded `HumanizedMidiRenderer.java` and records MIDI checksums. |
| Versioned MIDI/audio renderer | MVP implemented | `audio_render.py` uses FluidSynth and a declared SoundFont to produce reproducible role stems and condition mixes. |
| Sound-library/patch registry | MVP implemented | The audio manifest records the supplied SoundFont path, content checksum, release label, renderer, and license declaration. |
| Mix/master graph | MVP implemented | The reference profile applies declared role gains, ffmpeg `amix`, and loudness/true-peak targets; richer EQ, room, and dynamics profiles remain future work. |
| Audio QA and symbolic-to-audio alignment checks | Not implemented | Existing validation does not inspect audio properties or verify frame-level alignment. |
| ACR calibration and audio robustness benchmark | Not implemented | No ACR evaluation gate establishes which sound-design conditions preserve usable recognition. |

The target is not merely to generate more files. The target is a corpus in
which every audio file can be traced to a symbolic example, an exact sound
asset and renderer configuration, a deterministic mix recipe, and an explicit
quality result.

## 2. What is already implemented

### 2.1 Symbolic source of truth

The current hierarchy is coherent:

```text
ChordEvent labels
    -> realized VoicedChord
        -> V0 chord/arpeggio
        -> V1 bass
        -> optional V2 melody
        -> V9 percussion
```

The important guarantees are:

- Chord labels are established before realization and are not inferred from a
  later audio render.
- Melody is downstream of the chord sequence. It must not mutate, split, merge,
  relabel, or shorten chord events.
- Source-event ordinals and provenance allow generated melody notes and
  diagnostics to be mapped back to the immutable chord events.
- Local-key diagnostics, voicing diagnostics, selected profiles, seeds, and
  track hashes are available for later stratification and failure analysis.
- No-chord events and relaxed/fallback voicings remain explicit rather than
  being silently converted into ordinary chord labels.

This is the correct boundary for ACR data generation: the corpus can retain the
intended chord label even when a particular acoustic condition makes that label
difficult to recognize.

### 2.2 Melody and multitrack behavior

Spec 14 already covers the symbolic behavior needed to study melody as a
recognition variable:

- opt-in V2 generation;
- chord-conditioned candidate enumeration;
- local-key context and non-diatonic diagnostic categories;
- random-walk and sequence-beam decoding;
- naturalistic profile controls;
- deterministic directory-level inclusion quotas;
- synchronized V2 rest blocks for songs omitted by the quota;
- source-event provenance for emitted notes;
- multitrack score output and manifest records.

The V0/V1/V2/V9 separation is especially valuable for sound design. It permits
the same symbolic composition to be rendered as a full mix, as role stems, or
as controlled ablations without regenerating the composition.

### 2.3 Existing renderer and catalogs

The repository contains useful building blocks but not a complete audio
pipeline:

- `instruments.py` has separate chord, bass, arpeggio, and melody General MIDI
  catalogs.
- `HumanizedMidiRenderer.java` converts JFugue-derived score material to MIDI
  and supports timing jitter, velocity jitter, channel bias, retrigger
  handling, and special V2 handling at V0 arpeggio boundaries.
- `eda/validate_rendered_corpus.py` checks symbolic track structure, chord
  semantics, bass/percussion behavior, multitrack pairing, and melody
  provenance.
- `tools/curate_target_corpus.py` copies symbolic artifacts and metadata into a
  curated corpus.

These components should be extended rather than replaced where their contracts
remain useful.

### 2.4 Reproducibility boundary

The current `HumanizedMidiRenderer.processSong()` path derives a deterministic
per-song seed from the render seed and ordinal, and the MIDI manifest records
that seed and each MIDI checksum. The new audio stage extends the boundary by
recording the FluidSynth executable version, SoundFont checksum, ffmpeg
version, profile, and per-song audio checksums.

Canonical audio still depends on pinning the external FluidSynth/ffmpeg
versions and the SoundFont bytes in the execution environment. If a future
synthesizer or plugin cannot guarantee deterministic output, it must be
marked as a non-canonical augmentation renderer rather than silently being
used for the reference set.

## 3. What the predecessor sound-design system provides

The predecessor under `sound_design_docs/` is useful as a historical baseline
and as a list of operational failure modes. It is not a sufficient production
architecture for this corpus.

| Predecessor component | What it did | Reusable lesson | Why it is insufficient |
|---|---|---|---|
| `ArtificialSongGenerator.java` | Generated JFugue patterns, MIDI files, drum material, instrument material, and ARFF annotations. | Keep symbolic generation separate from audio rendering and preserve machine-readable annotations. | It did not define a versioned, reproducible audio asset or render contract. |
| `Config.java` | Stored instrument names, MIDI ranges, sampler identifiers, and General MIDI programs. | Instrument range and role constraints belong in configuration. | Sampler names were metadata only; they did not load a library, select a patch, or identify plugin parameters. |
| `mixbus_roboter.lua` and the Windows variant | Imported one MIDI stem at a time into a preconfigured Mixbus/Ardour session, soloed a named route, exported, and renamed a fixed output. | Named role routes and stem exports are useful concepts. | The workflow depended on GUI state, route names, fixed paths, fixed output names, delayed keystrokes, and preloaded instruments/effects. |
| `Sampling/generatemix` | Decoded MP3 stems to WAV, summed them with SoX, ran `gain -n`, padded as needed, and re-encoded MP3. | A separate offline mix step can produce a full mix from synchronized stems. | It was a straight sum with peak normalization, not a controlled mix/master; it had no source-controlled gain, pan, EQ, dynamics, room, true-peak, or loudness policy. |
| `MIXING_SOUND_DESIGN_REPORT.md` | Documented the same Java, DAW, and SoX boundaries and their limitations. | The predecessor already identified the need for explicit sound-design ownership. | Documentation did not turn the process into a deterministic pipeline. |

The predecessor's `sampler` field must therefore not be treated as evidence
that a particular sampler sound was rendered. A sound asset is only
reproducible when its bytes or immutable release, patch, engine version,
parameters, and license are recorded and the renderer actually consumes them.

## 4. Required target architecture

The production path is explicit and headless:

```text
symbolic manifest + score
        |
        v
deterministic MIDI/humanization
        |
        v
renderer profile + immutable sound assets
        |
        +--> synchronized role/instrument stems
        |
        v
versioned mix graph and master policy
        |
        +--> canonical reference mix
        +--> controlled robustness variants
        |
        v
audio QA + alignment QA + ACR evaluation
        |
        v
corpus manifest, checksums, curation, and split assignment
```

The implementation should expose one orchestration entry point that accepts a
symbolic manifest and a render profile, then writes all derived artifacts under
a unique song/variant identity. GUI automation and fixed filenames must not be
part of the canonical path.

### 4.1 Renderer interface

The project should define a renderer interface independent of a particular
engine. A render request should include:

- input MIDI hash and tempo-map hash;
- song seed and derived audio-render seed;
- role/instrument assignment;
- renderer and asset registry identifiers;
- sample rate, bit depth, channel layout, and output format;
- start-padding, tail, and silence policy;
- humanization profile;
- mix profile and variant identifier.

The result should include synchronized stems, a full mix where requested, and a
machine-readable render record. A renderer is eligible for canonical data only
if it can pass repeatability, duration, and alignment checks.

### 4.2 Ablation condition modes

The orchestration and render layers must support explicit role-selection modes
for the Spec 00 study. These modes are projections of one canonical all-role
bundle; they must not trigger independent chord, voicing, bass, percussion, or
melody generation.

The canonical source bundle should make all available roles addressable:

```text
all_roles -> V0 + V1 + V2 + V9
```

The required fixed condition modes are:

| Mode ID | Selected roles | Inclusion policy |
|---|---|---|
| `cb` | V0 + V1 | Chords and bass only; melody and percussion excluded. |
| `cbp` | V0 + V1 + V9 | Chords, bass, and percussion; fixed percussion inclusion. |
| `cbm` | V0 + V1 + V2 | Chords, bass, and melody; fixed melody inclusion. |
| `cbmp` | V0 + V1 + V2 + V9 | All four roles; fixed melody and percussion inclusion. |

The required naturalistic condition mode is:

| Mode ID | Selected roles | Inclusion policy |
|---|---|---|
| `naturalistic` | Per-song subset of V0 + V1 + V2 + V9 | V0/V1 always present; V2 and V9 use exact, manifest-recorded quotas and synchronized rest/absence records. |

The fixed modes correspond to the 0%/100% policy:

```text
cb    -> melody=0%, percussion=0%
cbp   -> melody=0%, percussion=100%
cbm   -> melody=100%, percussion=0%
cbmp  -> melody=100%, percussion=100%
```

The naturalistic mode initially uses the configured melody and percussion
quotas, with 70% as the current pilot default for each. The resolved
per-song mask, quota selection stream, joint inclusion counts, and omission
reasons must be recorded. A naturalistic mode is not a fifth fixed factorial
cell.

Every condition view must retain the same source song identity, chord labels,
event timing, V0/V1 decisions, and role hashes for any selected role. It must
produce a condition manifest that records `condition_id`, `selected_roles`,
`selected_voices`, `role_presence`, source-manifest hash, role hashes, and
render-profile hash.

The reference implementation should render the all-role score once and then
invoke `tools/project_condition_corpus.py` for the requested condition IDs.
That projector reads the canonical role sidecars and manifest, applies only
the declared role mask, and records the canonical source linkage. Calling
`render.py --condition` separately for each arm is not an equivalent
implementation: it repeats symbolic generation even when the seed and source
cohort are unchanged.

The implementation must support three independently stoppable stages:

1. **Score-only:** write canonical and condition-specific JFugue scores and
   manifests.
2. **MIDI-only:** pass any condition score through the deterministic
   `HumanizedMidiRenderer` and write MIDI plus hashes, without requiring audio.
3. **Audio:** render or mix audio from the declared condition/MIDI inputs and
   write stems, mixes, audio metadata, and QA results.

Stopping after score or MIDI is a valid successful intermediate result.
`audio_rendered=false` must be represented explicitly rather than treated as a
missing-artifact failure. A condition-specific audio renderer must consume the
declared role mask and must not infer inclusion from empty files, filenames, or
available MIDI channels.

### 4.3 Renderer rollout

The repository's MVP uses FluidSynth because it can be run in batch, pinned in
an environment, and paired with a SoundFont whose checksum and license are
recorded. It is invoked by `render.py --stage audio` or
`tools/project_condition_corpus.py --stage audio`; ffmpeg encodes retained
FLAC stems and applies the reference mix graph. FluidSynth will not by itself
provide the most realistic production sound, so the interface remains open to
a later plugin-host or sample-library renderer.

The corpus should not depend on a proprietary DAW session for its reference
set. Higher-fidelity libraries or plugins may be added as separately versioned
render profiles after their determinism, licensing, and headless operation are
proven.

## 5. Sound asset and renderer registry

Every playable sound must be selected through a registry rather than a free
text field. At minimum, each asset/patch record should contain:

- stable `asset_id` and immutable asset release/version;
- source URL or package identity, checksum, and redistribution license;
- renderer engine and engine version;
- SoundFont, sample-library, plugin, or preset identifier;
- MIDI program/bank and articulation mapping;
- playable pitch range and preferred register;
- velocity curve, envelope, round-robin, and retrigger behavior where
  applicable;
- sample rate/channel requirements and any resampling behavior;
- parameter values and effect-chain identifiers;
- intended role(s), genre/profile constraints, and known masking risks;
- a reproducibility classification: canonical, deterministic augmentation, or
  exploratory/non-deterministic.

The registry should support at least these initial role families:

- chord pad/keyboard/comping;
- chord arpeggio;
- acoustic/electric bass;
- melody lead/keyboard/plucked sound;
- percussion/drum kit;
- optional room/ambience/noise layers.

General MIDI program numbers remain useful as a portable fallback, but they are
not enough to identify an audible result. A patch record must still identify
the actual library and engine.

## 6. Mix, spatial, and master policy

The first canonical audio profile should favor label transparency and
diagnosability over maximal production polish. Additional profiles can test
robustness. Every profile must be represented by a versioned configuration
rather than implicit DAW state.

### 6.1 Required mix controls

The mix graph should explicitly define, per role or instrument:

- gain and velocity-to-level mapping;
- pan, width, and channel layout;
- EQ or filtering;
- compression, transient shaping, and saturation;
- reverb/delay sends and room/early-reflection parameters;
- sidechain or masking behavior;
- mute/solo/ablation state;
- master-bus gain, limiting, and clipping policy.

The canonical reference should avoid uncontrolled hard limiting and should
retain enough dynamic range for analysis. A reasonable initial target to test
in a pilot is -18 LUFS integrated with a true-peak ceiling of -1 dBTP; the
final target must be selected from measured ACR behavior and documented in the
profile. `gain -n` peak normalization alone is not a substitute for this
policy.

### 6.2 Proposed role-leveling policy

The first leveling policy should establish a stable role hierarchy while
allowing bounded variation. The values below are pilot starting points, not
universal mix truths. They are relative **active-content loudness** targets
after sound-asset calibration, with V0 as the reference. They are not raw MIDI
velocities, full-song LUFS values, or peak-amplitude targets.

| Role | Nominal level relative to V0 | Initial variation range | Primary rule |
|---|---:|---:|---|
| V0 chords/arpeggios | 0 dB | -1.5 to +1.5 dB | Preserve clear chord-quality and extension evidence. |
| V1 bass | +3 dB | +1.5 to +4.5 dB | Be clearly audible in the low band without dominating the whole mix. |
| V2 melody | -1 dB | -4 to +2 dB | Remain audible and natural without always exceeding harmonic evidence. |
| V9 percussion | -6 dB | -10 to -3 dB | Have lower average energy than the harmonic roles while retaining useful transients. |

The level metric should be measured on active windows or with a gated/short-term
method so that rests, held notes, sparse arpeggios, and melody omissions do not
cause the renderer to apply excessive makeup gain. The registry and render
manifest must retain both the calibrated stem measurement and the applied role
gain.

The following guardrails apply:

- Bass should generally have 2-5 dB more useful low-band energy than V0 in an
  active passage, initially measured over an auditable bass region such as
  40-200 Hz. This does not require bass to have the highest full-band peak.
- V0 must retain enough midrange energy for chord quality and extension
  recognition. A mix in which the root is obvious from bass but the chord
  quality is buried is not a label-transparent reference mix.
- Melody level should be coupled to register and density. A sparse,
  high-register melody can sit below V0 and remain prominent; dense,
  mid-register material should normally use the lower part of its range.
- Percussion should be transient-rich but average-quiet relative to harmony.
  Kick and other low percussion must not consistently mask bass fundamentals.
- Pads and arpeggios should use separate asset/mode calibration where needed:
  sustained pads accumulate energy, while sparse arpeggios have lower duty
  cycle. Their faders should not be adjusted arbitrarily to compensate for
  those differences.
- MIDI velocity remains a musical-dynamics control. It must not be treated as
  a substitute for role-level calibration because patches have different
  velocity curves and loudness responses.

Variation must come from versioned mix profiles rather than independent,
unbounded random faders. At minimum, define:

- a transparent/reference profile centered near the nominal values with a
  narrow spread;
- a balanced/naturalistic profile that samples within the stated ranges; and
- a dense/robustness profile that allows more melody and percussion overlap
  while remaining inside audio QA limits.

The selected profile and realized per-role values must be recorded. Register,
note density, instrument family, and role activity should be considered when
sampling a profile so that all role combinations do not sound identical, but
the role hierarchy must remain auditable.

For ablation studies, the same role-level profile and per-role gains must be
used for CB, CBP, CBM, CBMP, and naturalistic condition views. Removing a role
must not independently boost the remaining roles. If overall loudness
matching is needed, apply one global gain to the completed mix and record it;
do not normalize each remaining stem separately. The unnormalized fixed-gain
render should remain available for diagnostic analysis.

Spatial defaults should reinforce, rather than replace, level control:

- bass and kick are centered, with low frequencies predominantly mono;
- chords may be moderately wide but must remain phase-safe;
- melody is centered or only mildly offset; and
- percussion may use natural stereo width while preserving a centered
  low-frequency anchor.

### 6.3 Output and timing contract

Freeze one canonical lossless representation and do not mix formats within a
split. A recommended baseline is:

- stereo PCM FLAC;
- 44.1 kHz;
- 24-bit source/render representation;
- a documented frame-accurate start offset;
- deterministic pre-roll and tail policy;
- no truncation of release tails;
- no silent padding beyond the declared policy.

WAV may be retained as an intermediate when required by an engine. MP3/AAC
should be generated only as explicit codec-robustness variants, never as the
only canonical source. The chosen sample rate, format, and loudness target
remain configuration decisions that must be frozen before a large corpus
release.

### 6.4 Stems and mixes to retain

For each canonical song, retain at least:

1. a synchronized full mix;
2. V0 chord/arpeggio stem;
3. V1 bass stem;
4. V2 melody stem, including an explicit rest/absent record;
5. V9 percussion stem;
6. any additional instrument stems needed to reproduce the mix;
7. the mix profile and an ablation/role map.

Keeping stems is essential for diagnosing whether an ACR error comes from the
chord voicing, bass, melody masking, percussion, room, or master chain. A
full-mix-only corpus cannot support that analysis.

## 7. Provenance and package contract

The audio manifest should extend the existing symbolic manifest, not duplicate
it with a second source of truth. Each render record should include:

### 7.1 Symbolic identity

- generator version and source commit;
- specification versions;
- canonical song seed and composition-family identifier;
- score, symbolic manifest, and MIDI checksums;
- chord-event ordinals, labels, roots, qualities, inversions, keys, and
  no-chord states;
- track-role and instrument assignments;
- melody profile, inclusion/omission reason, local-key diagnostics, and
  source-event provenance;
- tempo map, meter, ticks-per-beat, and event boundaries.

### 7.2 Audio identity

- render profile and variant identifiers;
- derived humanization and audio-render seeds;
- renderer name/version and execution environment/container identity;
- asset IDs, versions, checksums, licenses, patches, and parameters;
- mix graph/master configuration checksum;
- stem calibration measurements, role-level profile, and realized per-role
  gains;
- completed-mix global gain applied for loudness matching, if any;
- output format, sample rate, bit depth, channels, frame count, and duration;
- stem and full-mix checksums;
- loudness, peak, true peak, clipping count, silence ratio, DC offset, and
  other QA results;
- declared alignment offset and measured symbolic-to-audio drift;
- reproducibility class and repeat-render result.

### 7.3 Label scope

The manifest must state whether a label applies to:

- a complete chord event;
- a frame interval inside that event;
- a transition-excluded center window;
- a no-chord interval;
- an ambiguous or intentionally masked interval.

Transition guards are important. A frame that contains the release of one
chord and the attack of the next should not be silently treated as an
unambiguous single-chord example. The label mask and any excluded boundary
frames must be available to training and evaluation code.

### 7.4 Parameter-complete render manifest

The render manifest must be a closed-world snapshot of every effective input
that can change a symbolic score, MIDI file, stem, mix, audio file, or
validation result. "Complete" means that a generated byte cannot change
without at least one of the following changing:

- a recorded effective parameter value;
- an input/asset/configuration checksum;
- a recorded random seed or seed-derivation version;
- a renderer/environment version; or
- the generator/source revision.

The existing `command`, `seed`, voicer summaries, quota summaries, and track
hashes remain useful, but they do not constitute a complete parameter record.
The implementation must add the resolved configuration and upstream
provenance described below rather than expecting consumers to reconstruct
hidden defaults from source code.

The command line alone is not sufficient. Defaults, code-level constants,
registry lookups, inherited configuration, and derived quota decisions must
be resolved into the manifest. Every parameter must identify its source
(`cli`, `default`, `source_manifest`, `registry`, `derived`, or `code`) and
whether it was explicitly supplied or defaulted.

Parameters required at render time but only known during upstream chord or
song generation must be propagated through the source manifest. If the input
song does not carry the upstream provenance needed to explain its labels or
timing, the render must either produce a non-canonical/incomplete record with
an explicit reason or fail the canonical render gate. It must not silently
invent or omit the provenance.

The top-level manifest should contain a versioned structure equivalent to:

```json
{
  "parameter_manifest": {
    "schema_version": 1,
    "completeness": "closed_world",
    "effective_config_sha256": "...",
    "unresolved_parameters": [],
    "defaulted_parameters": [],
    "source_manifests": [],
    "parameter_sources": {
      "render_mode": "cli",
      "voicing_policy": "registry",
      "melody_profile": "default",
      "sound_asset": "asset_registry"
    }
  },
  "source_generation": {},
  "randomness": {},
  "effective_generation": {},
  "sound_design": {},
  "outputs": {},
  "validation": {}
}
```

The exact JSON nesting may evolve, but the following categories are required.

#### Upstream source and generation identity

Record the complete source identity that produced the input progression:

- source song path/ID, source file checksum, composition-family ID, and
  parent-manifest checksum;
- generator revision, specification versions, command/API invocation, and
  dirty-worktree state;
- source corpus, normalized-data version, distribution/table paths and
  checksums, or an explicit declaration that the input was externally
  supplied;
- genre, tonic, mode, scale, BPM, meter, duration/timing policy, chord count,
  chord-event sequence, event durations, and no-chord events;
- source-generation seed, seed namespace, and any generation-level quotas or
  selection plans; and
- the complete label tuple used for each chord event, not only a summary
  count.

The render manifest may reference a content-addressed source manifest instead
of duplicating large chord arrays, but the reference must be immutable and
verifiable.

#### Randomness and deterministic decisions

Record:

- root seed and seed-derivation algorithm/version;
- every child seed domain that can affect output, including voicing, bass,
  percussion, melody inclusion, melody profile/instrument/collapse, melody
  decoding/tie-breaking, render-mode allocation, humanization, sound design,
  and augmentation;
- the resolved per-song seed and output ordinal;
- exact quota requests, target counts, selected source IDs, and realized
  counts; and
- random-library/runtime versions where they can affect generated output.

A seed domain must be recorded even when the resulting role is omitted. This
makes an omitted role distinguishable from a role that was never requested.
Worker ordering must not change the resolved decisions.

#### Effective symbolic generation and realization

Record the resolved values, not just requested values, for:

- render mode, pad/arpeggio allocation, target percentages, and per-song
  resolved mode;
- selected voicer, preferred voicer/family, voicer order, voicing-engine
  revision, policy/configuration hash, range and spread constraints, DCT and
  masking constraints, relaxation/fallback level, and per-event voicing
  diagnostics;
- chord instrument/catalog entry, MIDI program/bank/channel, selected
  register/range, and any pad-collapse decision;
- bass instrument/catalog entry, program/bank/channel, bass policy, register,
  rhythm, and pad-collapse decision;
- arpeggio profile, pattern family, schedule, gate/timing parameters,
  boundary markers, and humanization inputs;
- percussion inclusion decision, quota, feel/pattern family, kit/program,
  channel, timing/velocity policy, and rest behavior; and
- melody condition, inclusion decision, omission reason, profile, decoder,
  candidate/search settings, local-key settings, label-safety policy,
  collapse probability and decision, selected instrument/catalog entry,
  program/channel, register/rhythm/density settings, and melody provenance.

Per-event or per-note decisions that affect emitted score text must be
available in the source/role provenance or in a referenced content-addressed
sidecar. A single aggregate such as `voicing_summary` is not sufficient to
reconstruct a canonical render.

#### Sound design and audio rendering

Record:

- renderer/engine name and version, execution environment/container, platform,
  Java/Python/runtime versions, and dependency or lockfile checksums;
- sound-asset registry version, asset IDs/releases/checksums/licenses,
  SoundFont/sample-library/plugin identifiers, patches, banks, articulations,
  tuning, resampling, and all non-default parameters;
- MIDI renderer/humanizer version, humanization profile, timing/velocity
  distributions, channel biases, retrigger policy, and humanization seed;
- output sample rate, bit depth, channel layout, format, pre-roll, tail,
  silence, tempo-map, and alignment policies;
- mix profile, role-level calibration measurements, realized V0/V1/V2/V9
  gains, pan/width, EQ, compression, saturation, sidechain, reverb/delay,
  room, mute/solo/ablation state, and master-bus settings;
- global loudness-matching gain, if applied, and the loudness/true-peak
  target; and
- every robustness/codec/playback augmentation and its parameter seed.

#### Output and validation identity

Record:

- all score, MIDI, stem, mix, label, provenance, and metadata paths;
- complete-file and per-block/per-track checksums;
- frame count, duration, sample rate, channels, loudness, peak, true peak,
  clipping, silence, DC offset, and alignment measurements;
- structural, symbolic, audio, and ACR-validation tool versions;
- validation status, warnings, hard failures, excluded artifacts, and explicit
  exclusion reasons; and
- split assignment, composition-family grouping, curation decision, and
  release/protocol version.

The manifest should include an `unresolved_parameters` list even when empty.
Any non-empty list disqualifies the artifact from the canonical corpus unless
the release policy explicitly classifies it as exploratory.

## 8. Validation and ACR evaluation

### 8.1 Deterministic and structural QA

Before audio is admitted to the canonical corpus, validate that:

- identical inputs and profile versions produce identical MIDI and audio
  hashes, or fail the canonical reproducibility gate;
- every audio file decodes and has the declared sample rate, channels, bit
  depth, and frame count;
- all stems and the full mix have the expected duration and start offset;
- tempo-map changes do not introduce drift;
- note attacks and event boundaries map to the expected audio time;
- the full mix can be reconstructed from the declared stems within a defined
  tolerance;
- no role stem contains an unintended missing or duplicated block;
- V2 omission/rest behavior and no-chord events remain explicit;
- manifest checksums match the files on disk.

### 8.2 Audio-quality QA

Check, record, and gate on:

- decoder errors, NaN/infinite samples, and all-zero files;
- unexpected leading/trailing silence and release-tail truncation;
- clipping and true-peak violations;
- loudness outliers and excessive dynamic-range collapse;
- DC offset, discontinuities, and sample-rate conversion errors;
- channel imbalance, accidental mono collapse, and phase/pathology checks
  where stereo is expected;
- excessive noise or silence relative to the selected profile;
- stem leakage or route/mute mistakes for profiles that promise isolation.

Failures must be surfaced in the manifest and curation report. They must not
be converted into a successful-looking record by silently dropping the file.

### 8.3 ACR-specific validation

The corpus needs an ACR evaluation layer in addition to symbolic validation.
At minimum, evaluate a fixed baseline recognizer on:

- chord stems and clean reference mixes;
- full mixes with and without V2 melody;
- bass/percussion ablations;
- alternate voicings, inversions, extensions, and no-chord events;
- reverb/room and spatial variants;
- gain, dynamic, noise, codec, and playback-condition variants;
- rare and relaxed/fallback voicings.

Report more than one aggregate accuracy:

- frame/event root accuracy;
- chord-quality and extension accuracy;
- no-chord precision and recall;
- transition-window error rate;
- macro metrics by chord type, key/mode, voicing, instrument, and render
  profile;
- calibration/confidence and abstention behavior;
- confusion matrices and per-stratum failure counts.

The purpose is not to tune the generator to one recognizer. The purpose is to
detect conditions in which the intended label is systematically unobservable,
to keep those conditions explicitly labeled, and to avoid claiming robustness
from symbolic provenance alone.

## 9. Coverage, augmentation, and split strategy

### 9.1 Required coverage axes

Corpus planning should stratify or report coverage over:

- chord roots, qualities, extensions, inversions, and no-chord events;
- local/global key and mode;
- progression families and phrase lengths;
- voicing profile, range, spread, density, and relaxed/fallback status;
- comping versus arpeggio realization;
- tempo, meter, rhythmic density, and humanization profile;
- bass role and register;
- percussion on/off and pattern family;
- melody inclusion, profile, density, register, instrument, local-key
  behavior, and omission reason;
- sound asset, renderer version, patch, and instrument family;
- per-role gain/pan/effect/mix profiles;
- room, reverb, stereo, noise, codec, and playback variants.

Existing deterministic melody quotas should be retained as symbolic controls.
Audio variants must not change the quota by accident or be counted as
independent compositions.

### 9.2 Reference versus robustness variants

Separate the corpus into at least two semantic classes:

- **Reference renders:** deterministic, lossless, label-transparent, and
  suitable for baseline training/evaluation.
- **Robustness variants:** deliberately altered gain, timbre, room, dynamics,
  noise, codec, or playback conditions, with every alteration recorded.

Do not mix those classes without metadata. A codec-distorted or heavily
reverberated observation can be valuable training data, but it should not be
mistaken for a clean reference.

### 9.3 Leakage-safe splits

Assign train/validation/test at the composition-family level. All variants,
stems, melody-inclusion variants, and renderer/mix variants derived from one
base composition must remain in the same split. Otherwise a model can memorize
the progression, timing, or symbolic seed instead of learning audio
recognition.

Also consider separate challenge splits that hold out:

- sound assets or instrument families;
- renderer versions;
- voicing profiles;
- progression families;
- room/mix profiles.

Use symbolic fingerprints, MIDI hashes, and audio similarity checks to detect
near duplicates before publishing split assignments.

## 10. Implementation plan

### Phase 0: Freeze contracts and registry schema

- Define the audio manifest, render-profile, asset, mix, and label-mask schemas.
- Define the parameter-complete manifest schema, upstream source-manifest
  propagation rules, effective-default recording, seed-domain registry, and
  unresolved-parameter failure policy.
- Choose the canonical lossless format, sample rate, loudness/true-peak policy,
  padding, tail, and split rules.
- Define canonical versus augmentation renderer classes.
- Add a small, licensed fixture asset set for tests.

**Exit gate:** A sample song can be described completely without relying on
implicit DAW state or an untracked file path.

### Phase 1: Make MIDI production deterministic

- Add explicit seeded RNG support to `HumanizedMidiRenderer.java`.
- Pass the derived seed from the Python orchestration layer.
- Make the Java conversion an explicit, documented pipeline step.
- Require and propagate the upstream source-generation manifest, including
  effective defaults, parent hashes, and source-label/timing provenance.
- Serialize the resolved render configuration and every child seed domain
  before dispatching work to render workers.
- Record Java/humanizer versions, inputs, outputs, and configuration hashes.
- Add repeat-render tests for MIDI byte and note-level equality.

**Exit gate:** Repeating a canonical symbolic render with the same profile
produces the same MIDI and manifest identity.

### Phase 2: Add a headless reference renderer

- Implement a renderer adapter, initially for a pinned open engine/SoundFont
  profile or another approved deterministic engine.
- Render synchronized role/instrument stems and a raw, unmastered reference
  mix.
- Register assets, patches, licenses, ranges, and checksums.
- Store sample-accurate timing and frame metadata.

**Exit gate:** A clean reference song can be rendered from a fresh environment
without opening a DAW or relying on preloaded session state.

### Phase 3: Add versioned mixing and audio QA

- Implement declarative gain, pan, EQ, dynamics, room, and master profiles.
- Add full-mix reconstruction and stem alignment checks.
- Extend `eda/validate_rendered_corpus.py` or a dedicated audio validator with
  decode, duration, loudness, peak, silence, clipping, and drift checks.
- Extend `tools/curate_target_corpus.py` to copy audio, stems, render records,
  and QA results together.

**Exit gate:** Every accepted audio file is traceable, decodable, aligned, and
  accompanied by machine-readable QA.

### Phase 4: Add controlled robustness variation

- Add deterministic timbre, register, gain, pan, room, noise, codec, and
  playback-condition variants.
- Keep variant seeds and profiles separate from base composition seeds.
- Enforce composition-family split grouping and duplicate detection.
- Measure how each variation changes ACR behavior rather than assuming benefit.

**Exit gate:** Each augmentation family has a declared distribution, coverage
  report, and measurable effect on recognition.

### Phase 5: Calibrate against ACR

- Select a fixed baseline recognizer and evaluation protocol.
- Run clean, stem, full-mix, ablation, and robustness benchmarks.
- Add acceptance thresholds by stratum, not only one global score.
- Review systematic failures and adjust sound-design profiles or label masks.
- Publish a corpus release manifest with known limitations.

**Exit gate:** The project can state which conditions are reference-quality,
which are deliberately difficult, and how well the baseline recognizer
performs in each.

## 11. Acceptance criteria for a production corpus

A corpus release is not production-ready until all of the following are true:

- Every audio example has an immutable symbolic source identity and checksums.
- Every canonical manifest is parameter-complete, references the immutable
  upstream source manifest, records effective defaults and all seed domains,
  and has an empty `unresolved_parameters` list.
- The canonical all-role bundle can produce `cb`, `cbp`, `cbm`, `cbmp`, and
  `naturalistic` condition views without regenerating the underlying song.
- Score-only and MIDI-only execution succeed without requiring audio conversion,
  while audio status is recorded explicitly in the manifest.
- Canonical MIDI and audio renders are repeatable from recorded inputs,
  versions, assets, and seeds.
- The sound assets and licenses are sufficient for another environment to
  reproduce the declared profile.
- Full mixes and synchronized role stems are available for the promised
  subset.
- Sample rate, format, channels, duration, start offset, and tail policy are
  explicit and validated.
- Every canonical render applies a declared role-leveling profile, records
  active-content stem measurements and realized per-role gains, and remains
  within the profile's bounded ranges.
- The reference hierarchy keeps bass audibly present in the low band, preserves
  V0 chord-quality evidence, and prevents percussion or melody from being
  consistently dominant.
- Any overall loudness matching is a recorded global gain applied after the
  fixed role balance; per-condition stem re-normalization is not used.
- Audio QA rejects or flags decode failures, clipping, loudness outliers,
  drift, accidental silence, and unintended route/mute behavior.
- Label masks identify event interiors, transitions, no-chord regions, and
  intentionally ambiguous windows.
- Reference and robustness variants are distinguishable in metadata.
- Train/validation/test splits prevent composition-family and near-duplicate
  leakage.
- Coverage reports show the intended distribution across symbolic, timbral,
  mixing, and augmentation axes.
- A baseline ACR evaluation reports root, quality, extension, no-chord, and
  per-stratum performance.
- Any known non-deterministic renderer or proprietary dependency is excluded
  from the canonical set or explicitly classified as non-canonical.

## 12. Open decisions to resolve before implementation

1. Which headless renderer and sound libraries are acceptable for the
   canonical profile, and what licenses permit redistribution?
2. Should the reference set use FluidSynth/SoundFonts, a plugin host, or
   multiple renderer families from the first release?
3. What exact sample rate, bit depth, channel layout, loudness target, and
   true-peak ceiling will be frozen?
4. How much EQ, compression, reverb, stereo width, and mastering is allowed in
   the label-transparent reference profile?
5. Which active-content loudness metric and sound-asset calibration fixture
   should finalize the proposed role-level ranges?
6. Which audio augmentations belong in training only, and which belong in
   validation/test challenge splits?
7. Which ACR baseline, label time resolution, transition guard, and acceptance
   thresholds define the first calibration gate?
8. Which renderer/environment versions must be held out to measure
   cross-renderer generalization?

## 13. Bottom line

The project has implemented most of the difficult symbolic controls needed to
make a useful ACR corpus: immutable chord labels, voicing and instrument
choices, synchronized roles, optional melody, deterministic symbolic quotas,
provenance, and symbolic validation. The predecessor sound-design code
demonstrates stem-oriented rendering but relies on external sampler state,
fragile DAW automation, and straight-sum normalization.

The missing production layer is therefore substantial but well bounded:
deterministic MIDI, a versioned headless renderer, licensed sound assets, a
declarative mix/master graph, audio metadata and QA, leakage-safe curation, and
an ACR calibration loop. Implementing those pieces will turn the current
symbolic corpus generator into a reproducible audio corpus system without
confusing intended chord labels with guaranteed recognizer predictions.
