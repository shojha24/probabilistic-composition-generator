# Spec 12 - Voiced Chord Arpeggio Rendering

- **Status:** proposed implementation specification
- **Applies to:** `chord_module.py`, `render.py`, `instruments.py`, tests, and
  rendered-corpus validation
- **Parents:** specs 07, 09, and 11

## 1. Decision

Implement `--mode arpeggios` as a **temporal rendering of the existing
`VoicedChord` sequence**, not as a second voicer, a chord-event parser, or a
new accompaniment generator.

The required pipeline is:

```text
ChordEvent labels -> existing Engine / selected policy -> VoicedChord MIDI lists
                                                    -> pad or arpeggio serializer
```

Consequently, for the same label, seed, voicer order, and bass-active context,
pad and arpeggio modes must use exactly the same selected policy and the same
per-event `VoicedChord.midi`, roles, DCT, omissions, and diagnostics. Only
the V0 onset pattern, note durations, and arpeggio-specific provenance differ.

Directory rendering may also use `--mode mixed` with
`--arpeggio-percent P` and `--pad-percent Q`. The renderer chooses one mode
per source song using the render seed, allocating the nearest whole-song
counts; the percentages must sum to 100, and either percentage may be omitted
as the complement. Single-mode rendering remains unchanged.

This is the smallest change that satisfies the generator's existing
responsibility boundary:

- `voicing/` determines chord content, octave placement, permitted omissions,
  DCT exposure, voice-leading, and guitar legality.
- `ChordModule` serializes that already determined content into JFugue.
- `BassModule` remains the sole owner of V1 bass behavior.
- `PercussionModule` remains the sole owner of V9 groove behavior.

No chord-generation probabilities, voicer policies, bass decisions, or drum
decisions are changed by this feature.

## 2. Findings that drive the design

### 2.1 Current repository seam

`ChordModule.render()` already runs one of the six existing policy engines and
stores the authoritative realization in `last_voiced_midis`,
`last_voiced_roles`, and `last_voicing_diagnostics`. Its current final step is
only:

```text
VoicedChord.midi + source duration token -> simultaneous "+" JFugue token
```

Replacing that final serialization step is sufficient. Reconstructing chord
tones from `ChordEvent`, Harte labels, or human-readable root names would
duplicate and potentially contradict the policy engine. In particular, it
would lose policy decisions such as jazz root omission, fifth omission,
collision merges, guitar extension drops, absolute-root conversion, DCT
placement, and the selected register.

`render.py` creates the chord module before it independently invokes bass and
percussion. It gives the bass module V0 MIDI only for its low-register
collision adjustment. `pad_mode=mode == "pads"` already confines pad-timbre
collapse to pad mode. It follows that arpeggio mode must keep this call shape:
it may pass the voiced MIDI lists for the existing collision check, but must
not alter V1 notes, V1 instrument selection, V9 inclusion, or V9 feel.

### 2.2 Timing contract

The supported generated duration tokens are `ww`, `w.`, `w`, `h.`, `h`,
`q.`, and `q`; their beat lengths are all integral multiples of a sixteenth
note. `PercussionModule` already establishes a synchronized sixteenth-note
grid from `chord_gen.DURATION_BEATS`.

The historical `ChordArpeggios` reference emits repeated eighth notes and
preserves a triad's total duration, but it assumes exactly three notes and an
eight-position style. That is not valid for this project: a selected voicing
can contain one bare-root voice, two `5`-chord voices, merged degrees, omitted
fifths, four-to-six piano/synth voices, or a legal guitar shape. The reference
also folds notes into a new one-octave register; doing that here would discard
the voicer's chosen pitch placement.

### 2.3 Instrument-selection defect to avoid

`CHORD_INSTRUMENTS` currently has no synth instrument marked `arpeggios`.
The existing `ChordModule` filters families and instruments by `mode` before
or during policy selection. If arpeggio mode retained that behavior, it could
choose a different voicer from pad mode, and an explicitly ordered synth
attempt could reach an empty instrument list. This violates the central
mode-invariant voicing requirement.

Policy/voicer selection therefore must be independent of render mode.
Instrument selection is a later, mode-specific step. The catalog must provide
at least one arpeggio-capable instrument for every currently supported voicer
family, including synth, or fail clearly during catalog validation before
rendering.

## 3. Scope and non-goals

### 3.1 Included

1. Make `ChordModule(mode="arpeggios")` render playable V0 JFugue output.
2. Arpeggiate all valid selected voicing cardinalities, including root-only,
   power-chord, collision-merged, rootless, dense-extension, and guitar
   voicings.
3. Preserve every source event's exact total duration, including `N` rests.
4. Make arpeggio output reproducible under the existing render seed.
5. Add concise, machine-readable arpeggio provenance to the score manifest.
6. Extend validation so arpeggiated V0 is checked as a temporal expansion of
   the selected voicing rather than incorrectly as one token per source event.

### 3.2 Explicitly excluded

- altering `Engine`, a `VoicerPolicy`, candidate enumeration, tone selection,
  DCT selection, or voice-leading;
- adding melody-like non-chord tones, chromatic passing tones, or random pitch
  classes not present in the selected voicing;
- changing V1 bass pitches, the bass overlap adjustment, V1 program choice,
  or pad-collapse probability;
- changing V9 percussion tokens, inclusion probability, kit, or feel;
- changing source JSON labels, duration distribution, source-event count, or
  score-block ordering;
- adding a simultaneous pad beneath the arpeggio. `arpeggios` remains one
  harmonic V0 texture, not a fourth accompaniment track.

## 4. Required implementation structure

### 4.1 Introduce a pure arpeggio serializer

Add a small module-local helper or a focused `ArpeggioRenderer` in
`chord_module.py`. It accepts:

```python
voiced: Sequence[VoicedChord | None]
events: Sequence[ChordEvent | dict]
rng: random.Random
```

and returns:

```python
tokens: list[str]
diagnostics: list[dict]
```

It must not accept `Song`, resolve degrees, inspect raw root fields, select a
voicer, or mutate the supplied MIDI lists. Keeping this boundary narrow makes
it impossible for arpeggios to change chord voicing by accident.

`ChordModule.render()` first performs its existing policy fallback loop and
sets all existing `last_*` voicing fields. It then calls either the current
pad serializer or the new arpeggio serializer. The complete V0 prefix remains
`T<bpm> V0 I<program>`.

### 4.2 Make policy selection mode-invariant

Build `candidate_specs` from the supplied `voicer_order` or the current
genre/family randomization before selecting an instrument, and without
filtering families on `self.mode`. Given identical progression, seed,
`preferred_family`, `voicer_order`, and `bass_module_active`, the policy
attempt order, per-attempt engine seed, successful policy, and `VoicedChord`
output must be identical for pads and arpeggios.

Use distinct deterministic random streams (or deterministic child seeds) for:

1. voicer ordering/engine seeds;
2. render-mode instrument selection;
3. arpeggio pattern selection.

This prevents a different mode-specific catalog size or a new arpeggio pattern
draw from perturbing the voicing engine's random stream. Derive child seeds
from the existing module seed with stable, explicit labels rather than Python's
process-randomized `hash()`.

Select the V0 program only after a policy family succeeds, from instruments
marked for the requested mode in that family. Add at least one musically
credible `arpeggios` role to the synth family (and retain existing piano and
guitar coverage). A startup/catalog helper must reject a mode/family with no
eligible instrument using a clear `ValueError`, rather than allowing
`random.choice([])` to fail.

The mode may legitimately select a different *instrument program*. It must
not select a different voicer or MIDI pitches.

### 4.3 Arpeggio rhythm

Use one sixteenth-note event (`s`) for every sixteenth position in the source
event:

```text
slot_count = int(DURATION_BEATS[duration_token] * 4)
```

Reject unknown duration tokens and non-integral slot counts with a clear error,
matching the percussion module's error behavior. A source `q.` event produces
six V0 sixteenths, `w` produces sixteen, and `ww` produces thirty-two.

Each slot emits exactly one selected MIDI note at duration `s`; do not emit
rests in a playable chord's arpeggio. This deliberately gives the V0 texture
a stable pulse, guarantees exact total duration, avoids arbitrary silence
rules in short `q`/`q.` events, and remains musically plausible from 2 to 6+
voices. A no-chord event emits the single source-duration rest (`R<token>`)
and starts a new arpeggio phrase at the next playable event.

Do not use the historical triad-only fixed-eight style, and do not rewrite a
voicing into a one-octave fold. Every emitted non-rest pitch must be an exact
MIDI member of that event's `VoicedChord.midi`.

### 4.4 Phrase pattern for arbitrary voice counts

For each contiguous playable phrase (bounded by `N` rests), choose one
deterministic pattern family and retain it until the next boundary:

| Family | Index sequence over sorted `VoicedChord.midi` | Intended character |
|---|---|---|
| `up` | `0, 1, ..., n-1`, cyclic | transparent rising broken chord |
| `down` | `n-1, ..., 1, 0`, cyclic | descending answer |
| `up_down` | `0, 1, ..., n-1, n-2, ..., 1`, cyclic; `0` for `n=1` | common continuous accompaniment |
| `outside_in` | alternates lowest remaining / highest remaining, cyclic | wider, less scalar motion |

Choose a starting phase once per event from the same pattern cycle, then
advance one position per slot. Preserve the phrase family across chord changes
but recompute its cycle for each event's actual number of voices. This
continues the texture without requiring the same number of selected voices in
consecutive chords. `n=1` always emits the sole note; `n=2` remains a
meaningful alternation; any `n >= 3` includes every selected voice before
cycling.

The cycle must not select the same *index* in adjacent slots when `n > 1`.
Repeated absolute pitches across an event boundary remain permissible: they
can be the intentional result of voice-leading or a one-voice chord, and the
Java renderer already owns retrigger-overlap handling.

All pattern choices must use only the sorted `VoicedChord.midi` order. Roles,
hands, shape IDs, and DCT data remain provenance and validation data; they
must not be used to reorder or omit pitches.

## 5. Rendering, metadata, and validation contracts

### 5.1 Track isolation

In both `render_song()` and `_render_source()`:

- Call `ChordModule` with the same `bass_module_active=True` context as pads.
- Pass its original `last_voiced_midis` to `BassModule.render()` so its
  existing low-register overlap rule sees the same values under both modes.
- Continue `pad_mode=mode == "pads"` exactly as today, so only pads can
  collapse the bass timbre.
- Invoke `PercussionModule` with the unmodified progression and same seed.

The following must be byte-identical between pad and arpeggio rendering for
the same input/seed: V1 note/rest token sequence, V1 program selection when
not pad-collapsed, V9 token sequence, percussion inclusion, and percussion
feel. The V0 program may differ by mode.

### 5.2 Manifest

Retain all existing manifest fields and add each record's:

```json
"render_mode": "arpeggios",
"arpeggio": {
  "subdivision": "sixteenth",
  "pattern_family": "up_down",
  "event_slot_counts": [16, 8, 6],
  "source_voicing_midis": [[48, 55, 60], [50, 57, 62]]
}
```

For pad mode, use `"render_mode": "pads"` and either omit `"arpeggio"` or set
it to `null` consistently. `source_voicing_midis` is a compact serialized copy
of the selected V0 MIDI lists before temporal expansion; it makes validation
and later corpus analysis independent of a second engine run. It is not a new
authority: `VoicedChord` remains the source while rendering, and label plus
policy semantics remain the harmonic authority.

Record no-chord events as an empty MIDI list and zero arpeggio slots. Their
source rest duration remains represented in score text.

The top-level command already includes `--mode`; no manifest-version bump is
needed if consumers tolerate additional fields. Bump the version only if the
validator cannot preserve backward-compatible pad-manifest parsing.

For mixed directory renders, the manifest additionally records:

```json
"render_mode": "mixed",
"render_mode_counts": {"arpeggios": 30, "pads": 70},
"render_mode_percentages": {"arpeggios": 30.0, "pads": 70.0},
"render_mode_targets": {"arpeggios": 30.0, "pads": 70.0}
```

Each record's existing `render_mode` remains authoritative for validating its
V0 track.

### 5.3 Validator changes

`eda/validate_rendered_corpus.py` currently expects one V0 event per source
chord, so it must branch on manifest `render_mode`:

- **Pads:** preserve the existing one-source-event/one-V0-token validation.
- **Arpeggios:** partition V0 sixteenth-note tokens by
  `event_slot_counts`; map each non-rest slot back to exactly one source
  event; verify each emitted MIDI is a member of that event's
  `source_voicing_midis`; and verify the slot count and total duration.
- **No-chord:** require a harmonic rest for the source duration, with no V0
  or V1 pitches, while preserving existing V9-independent semantics.

The validator must still validate the original selected voicing content against
the label using the existing active-degree, DCT, rootless, omission, and
unrequested-pitch-class rules. It should apply those content checks to
`source_voicing_midis`, not demand all required chord degrees in every
single sixteenth note. It must continue to validate bass separately.

## 6. Required tests

Add focused tests without duplicating engine coverage:

1. **Mode-invariant voicing:** render a representative extended, non-C,
   inverted jazz label in both modes with the same explicit voicer order and
   seed. Assert equal selected voicer, `last_voiced_midis`, roles, and
   diagnostics; permit only the program/token serialization to differ.
2. **Arbitrary cardinalities:** serialize one-, two-, three-, and six-voice
   fixtures. Every playable sixteenth must be a selected input MIDI value,
   every cycle must visit all input voices, and no adjacent index repeats for
   `n > 1`.
3. **Duration conservation:** exercise every supported duration token and
   assert that V0 occupies the source duration exactly. Include dotted and
   double-whole durations.
4. **Dense/collision content:** use an extended selected voicing and a
   collision-merged source event; assert no extra pitch class appears and no
   selected MIDI is re-octaved or silently excluded.
5. **No-chord boundary:** assert a no-chord emits a duration-sized rest, does
   not call the playable arpeggio scheduler, and resets the following phrase
   pattern.
6. **Bass/percussion isolation:** for a fixed progression and seed, compare
   pads to arpeggios. Assert V1 notes and percussion output are unchanged,
   `collapsed_to_pad` is false in arpeggio mode, and V9 remains synchronized.
7. **Catalog completeness:** assert every selectable voicer family has at
   least one instrument for both `pads` and `arpeggios`, or assert the
   documented early configuration error.
8. **Manifest/validator integration:** directory-render a short mixed
   duration fixture in arpeggio mode, validate it successfully, and assert
   source ordering, hashes, voicing summary, and added arpeggio provenance.
9. **Mixed mode:** directory-render a seeded percentage split and assert the
   exact whole-song counts, per-record modes, and manifest split metadata.

## 7. Acceptance criteria

The feature is complete when:

1. `python render.py ... --mode arpeggios --seed N` produces synchronized
   score text instead of `NotImplementedError`.
2. For equivalent runs, pad and arpeggio modes select the same voicer and
   exact source voicing MIDI sequence.
3. Every playable V0 arpeggio note is an exact member of the selected source
   voicing for its chord event; no arbitrary non-chord tone is introduced.
4. Every source event consumes exactly its labeled duration, and a complete
   V0 phrase consumes exactly the score's harmonic timeline.
5. All existing bass and percussion behavior is unchanged except the already
   intended absence of pad-timbre collapse in arpeggio mode.
6. Root omission, fifth omission, collision merging, DCT exposure, guitar
   extension-drop diagnostics, source labels, and policy fallback provenance
   remain the existing engine results.
7. The rendered-corpus validator supports both modes and reports an
   arpeggio-mode hard failure for an out-of-source-voicing pitch, incorrect
   slot count, or timeline mismatch.
8. Mixed mode assigns the requested pad/arpeggio percentages deterministically
   across source songs without changing the single-mode behavior.
