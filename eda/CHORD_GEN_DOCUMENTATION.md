# Chord Generator (`chord_gen.py`) — Implementation Guide

This document explains the interface, data flow, and implementation details of the chord generation pipeline (Stages 1–3).

## Overview

`chord_gen.py` implements:
1. **Model Loading** (`load_genre_model`) — reads trained distribution tables
2. **Stage 1** (`backoff_sample_stage1`) — harmonic backbone (chord progressions)
3. **Stage 2** (`backoff_sample_stage2`) — bass note AR
4. **Stage 3** (`select_extension_trie`, `walk_trie_with_backoff`) — extension trie walk

Each stage uses **cascaded backoff with λ-interpolation** to handle rare contexts without crashing.

---

## Module Structure

### `HyperParams` class

Tunable parameters for all stages. All defaults are set; override in `__init__` as needed.

```python
hp = HyperParams(
    min_count_2=2,              # Stage 1 trigram threshold
    min_count_1=1,              # Stage 1 bigram threshold
    min_count_1b=2,             # Stage 2 (root, bass_prev) threshold
    discount_D=1.5,             # λ interpolation: λ = N / (N + D)
    add_k=0.1,                  # Laplace smoothing within distributions
    min_count_slot_2=3,         # Stage 3 trie node: full context
    min_count_slot_1=1,         # Stage 3 trie node: bass-only
    genres_to_temps={
        "pop_rock": 1.0,
        "jazz": 1.2
    }
)
```

**Key parameters:**

- **`min_count_*`**: Context must appear ≥ threshold times to use that backoff level
- **`discount_D`**: Controls smoothness of λ weighting. Lower D → more trust in context (sharper cliff). Recommended 1–3.
- **`genres_to_temps`**: Default Stage 1 Level-1 softmax temperature per genre (used if `--temperature` not set)

---

### `GenerationState` dataclass

Holds mutable state during a song's generation.

```python
state = GenerationState()
state.init_sequence()  # Reset for a new song

# After each chord, update:
state.update(root_t, triad_t, bass_t, ext_t, voicing_t=None)
```

**Fields:**

- `root_t_minus_1`, `root_t_minus_2`: Previous chord roots
- `triad_t_minus_1`, `triad_t_minus_2`: Previous triad qualities
- `bass_t_minus_1`: Previous bass note
- `ext_t_minus_1`: Previous extensions dict (`{seventh, ninth, eleventh, thirteenth}`)
- `voicing_t_minus_1`: Previous voicing (passed to Stage 4 voicer, optional here)
- `t`: Current timestep

---

### `GenreModel` dataclass

Complete trained model for a single genre (one of `pop_rock`, `jazz`).

```python
model = load_genre_model("pop_rock", "distributions/")
# model.genre == "pop_rock"
# model.stage1_level0, .stage1_level1, .stage1_level2: dicts of distributions
# model.stage2_level0, .stage2_level1b: dicts of distributions
# model.stage3_trie_A, .stage3_trie_B, .stage3_trie_C: trie structures
# model.meta: dataset metadata
```

---

## API: Top-Level Interface

### `ChordGenerator` class

Simplest way to generate chords; wraps model loading and generation.

```python
gen = ChordGenerator(
    genre="pop_rock",
    dist_dir="distributions/",
    seed=42  # For reproducibility
)

# Single song
chords = gen.generate_song(num_chords=48)

# Multiple songs (fresh RNG state per song)
songs = gen.generate_batch(num_songs=5, num_chords=48)
```

**Methods:**

- `generate_song(num_chords, temperature=None, self_transition_discount=None)` → list of chord events
- `generate_batch(num_songs, num_chords, ...)` → list of song lists

**Generation-time knobs:**

- `temperature` (τ_1): Softmax temperature for Stage 1 Level 1
  - `→ 0`: sharp, idiomatic (repeat progressions)
  - `→ ∞`: flat, exploratory
  - Default: genre from `GENRES_TO_TEMPS`

- `self_transition_discount` (δ): Discount for immediate chord repeats
  - `1.0` = no discount (default)
  - `0.0` = forbid repeats entirely
  - `0.5` = 50% less likely

---

## Lower-Level Functions

### `load_genre_model(genre, dist_dir)`

Loads all four JSON files for a genre.

```python
model = load_genre_model("jazz", "distributions/")
```

**Files read:**
- `distributions/{genre}/stage1_backbone.json`
- `distributions/{genre}/stage2_bass.json`
- `distributions/{genre}/stage3_extensions.json`
- `distributions/{genre}/meta.json`

**Returns:** `GenreModel` with all tables loaded.

**Raises:** `FileNotFoundError` if any file missing, `ValueError` if genre unknown.

---

### `backoff_sample_stage1(model, state, hyperparams, rng, generation_temperature, self_transition_discount)`

Sample next chord using 3-level backoff.

```python
root_t, triad_t = backoff_sample_stage1(
    model, state, hyperparams, rng,
    generation_temperature=1.0,
    self_transition_discount=1.0
)
```

**Context (from state):**
- `state.root_t_minus_1, triad_t_minus_1, root_t_minus_2, triad_t_minus_2`

**Backoff cascade:**

```
Level 2 (trigram):  (Root_{t-2}, Triad_{t-2}, Root_{t-1}, Triad_{t-1})
    ↓
Level 1 (bigram):   (Root_{t-1}, Triad_{t-1})  [tempered by generation_temperature]
    ↓
Level 0 (unigram):  Marginal P(Root_t, Triad_t)
```

**Interpolation formula:**

If using Level 2:
```
λ_2 = N_2 / (N_2 + D)
P_final = λ_2 · P_2 + (1 - λ_2) · [λ_1 · P_1_tempered + (1 - λ_1) · P_0]
```

**Self-transition discount:**

After interpolation, if `Root_t == Root_{t-1}` AND `Triad_t == Triad_{t-1}`:
- Remove `(1 - δ) · P(repeat)` probability mass
- Redistribute uniformly to other chords
- Allows smooth rather than hard forbiddance of repeats

**Returns:** `(root_t: int, triad_t: str)` where `root_t ∈ [0, 11]`

---

### `backoff_sample_stage2(model, root_t, state, hyperparams, rng)`

Sample bass note for current root, conditioned on previous bass.

```python
bass_t = backoff_sample_stage2(model, root_t, state, hyperparams, rng)
```

**Context:**
- `root_t`: Current root (from Stage 1)
- `state.bass_t_minus_1`: Previous bass note

**Backoff cascade:**

```
Level 1:  P(Bass_t | Root_t, Bass_{t-1})
    ↓
Level 0:  P(Bass_t | Root_t)
```

**Returns:** `bass_t: int` — bass interval relative to root, ∈ [0, 11]

---

### `select_extension_trie(model, root_t, triad_t, hyperparams)`

Select which trie to walk via A→B→C backoff.

```python
trie = select_extension_trie(model, root_t, triad_t, hyperparams)
```

**Levels:**

```
Level A:  (Root_t, Triad_t)-specific trie
    ↓
Level B:  Triad_t-aggregated trie (across all roots)
    ↓
Level C:  Genre-wide trie (guaranteed fallback)
```

**Returns:** Trie dict structure ready for `walk_trie_with_backoff()`.

---

### `walk_trie_with_backoff(trie, ext_t_minus_1, bass_t, hyperparams, rng)`

Walk the trie one slot at a time, sampling each extension with backoff.

```python
ext_t = walk_trie_with_backoff(trie, state.ext_t_minus_1, bass_t, hyperparams, rng)
# → {"seventh": "7", "ninth": "N", "eleventh": "#11", "thirteenth": "N"}
```

**Walks 4 slots in order:** seventh → ninth → eleventh → thirteenth

**At each node, uses 3-level backoff to score children:**

```
Level 2 (full context):  P(child | trie_node, Ext_{t-1}, Bass_t)
    ↓
Level 1 (bass only):     P(child | trie_node, Bass_t)
    ↓
Level 0 (marginal):      P(child | trie_node)
```

**Returns:** `ext_t: dict` — `{seventh, ninth, eleventh, thirteenth}` as sampled slot values

---

### `generate_chord_sequence(model, num_chords, hyperparams, rng, generation_temperature, self_transition_discount)`

Full generation loop for one song (Stages 1–3 only).

```python
chords = generate_chord_sequence(
    model,
    num_chords=48,
    hyperparams=None,  # Uses defaults if None
    rng=np.random.default_rng(42),
    generation_temperature=1.0,
    self_transition_discount=1.0
)

# chords[0] = {
#     "root": 0,
#     "triad": "major",
#     "bass": 0,
#     "seventh": "N",
#     "ninth": "N",
#     "eleventh": "N",
#     "thirteenth": "N",
#     "timestep": 0
# }
```

**Returns:** List of chord events (one per timestep).

---

## Utility Functions

### `_compute_lambda(context_count, discount_D)`

Compute interpolation weight λ = N / (N + D).

```python
lambda_2 = _compute_lambda(context_count=15, discount_D=1.5)
# → 15 / (15 + 1.5) ≈ 0.91 (high trust in this context)
```

---

### `_apply_softmax_temperature(distribution, temperature)`

Apply softmax tempering to a distribution.

```python
P_tempered = _apply_softmax_temperature(P_level1, temperature=0.7)
# temperature=0.7 → sharpen (lower entropy, less exploration)
# temperature=1.0 → no change (identity)
# temperature=2.0 → flatten (higher entropy, more exploration)
```

---

### `_sample_from_distribution(distribution, rng)`

Sample a single symbol from a probability distribution.

```python
symbol = _sample_from_distribution({"C": 0.6, "D": 0.3, "E": 0.1}, rng)
```

---

## Data Flow Diagram

```
User calls:  gen.generate_song(num_chords=48, temperature=1.0, self_transition_discount=1.0)
                 ↓
            ChordGenerator.generate_song()
                 ↓
            generate_chord_sequence(model, 48, hp, rng, ...)
                 ↓
            [For t = 0 to 47]:
            
            ┌─ STAGE 1 ────────────────────────────────────┐
            │ backoff_sample_stage1(model, state, hp, rng) │
            │                                                │
            │  Level 2 (trigram)?  ──→  Yes: weight λ_2    │
            │                                                │
            │  Level 1 (bigram)?   ──→  Yes: weight λ_1    │
            │                       ──→  Temper with τ_1    │
            │                                                │
            │  Level 0 (unigram):  Always available         │
            │                                                │
            │  Result: root_t, triad_t                      │
            └────────────────────────────────────────────────┘
                        ↓
            ┌─ STAGE 2 ────────────────────────────────────┐
            │ backoff_sample_stage2(model, root_t, ...)     │
            │                                                │
            │  Level 1 (root + bass_prev)? ──→ weight λ_1  │
            │                                                │
            │  Level 0 (root only):        Always available │
            │                                                │
            │  Result: bass_t                               │
            └────────────────────────────────────────────────┘
                        ↓
            ┌─ STAGE 3a ────────────────────────────────────┐
            │ select_extension_trie(model, root_t, triad_t) │
            │                                                │
            │  Level A ((root, triad)-specific)?  ──→ Yes   │
            │  Level B (triad-only)?              ──→ Yes   │
            │  Level C (genre-wide):        Fallback        │
            │                                                │
            │  Result: trie                                 │
            └────────────────────────────────────────────────┘
                        ↓
            ┌─ STAGE 3b ────────────────────────────────────┐
            │ walk_trie_with_backoff(trie, ext_prev, bass_t)│
            │                                                │
            │  For each slot [7th, 9th, 11th, 13th]:        │
            │                                                │
            │    Level 2 (ext + bass)?    ──→ weight λ_2   │
            │    Level 1 (bass only)?     ──→ weight λ_1   │
            │    Level 0 (trie margin):   Always available  │
            │                                                │
            │    Sample slot value, descend trie            │
            │                                                │
            │  Result: ext_t                                │
            └────────────────────────────────────────────────┘
                        ↓
            Emit chord event:
            {root, triad, bass, seventh, ninth, eleventh, thirteenth, timestep}
                        ↓
            Update state:
            state.update(root_t, triad_t, bass_t, ext_t)
                        ↓
            [Next t]
```

---

## Hyperparameter Tuning

### Recommended Ranges

| Parameter | Typical range | Effect |
|-----------|--------------|--------|
| `min_count_2` | 1–5 | Higher = stricter, fewer high-order contexts used |
| `min_count_1` | 1–2 | Higher = more backoff to unigram |
| `discount_D` | 0.5–3.0 | Lower = sharper cliff when backoff, higher = smoother |
| `add_k` | 0.05–0.5 | Laplace smoothing; shouldn't affect much if ≥ min counts are met |
| `generation_temperature` | 0.5–2.0 | Lower = repeat idioms, higher = explore |
| `self_transition_discount` | 0.0–1.0 | 1.0 = allow, 0.5 = 50% less likely, 0.0 = forbid |

### Tuning Strategy

1. **Start with defaults** from `HyperParams()`
2. **If output is too repetitive:** raise `generation_temperature` or lower `min_count_2`
3. **If output is nonsensical:** lower `discount_D` or raise `min_count_*`
4. **For variety without losing plausibility:** raise `generation_temperature` before lowering `min_count_*`

---

## Error Handling

### Common Errors

**`FileNotFoundError: Missing stage1_backbone.json`**
→ Run `extract_distributions.py` to populate the `distributions/` directory.

**`ValueError: Cannot sample from empty distribution`**
→ Dataset is missing a chord type or bass note for a given root. Check corpus coverage.

**`KeyError: chord_symbol`**
→ Chord symbol format mismatch. Ensure format is `"{root}_{quality}"` (e.g., `"0_major"`, `"7_minor"`).

---

## Integration with Stage 4 (Voicer)

This module outputs **symbolic chords** only:

```python
{
    "root": 0,           # 0–11
    "triad": "major",
    "bass": 0,           # 0–11 (relative to root)
    "seventh": "7",      # or "b7", "N", etc.
    "ninth": "N",
    "eleventh": "#11",
    "thirteenth": "b13"
}
```

**Stage 4 (voicer)** receives this and:
1. Enumerates valid voicings (actual MIDI pitches)
2. Ranks by voice-leading distance to previous voicing
3. Softmax-samples the final voicing

The voicing state (`voicing_t_minus_1`) is passed through `GenerationState` but not used in Stages 1–3.

---

## Example Usage

### Basic generation

```python
from chord_gen import ChordGenerator

gen = ChordGenerator(genre="pop_rock", seed=123)
chords = gen.generate_song(num_chords=32)

for chord in chords:
    print(f"{chord['root']} {chord['triad']} (bass {chord['bass']})")
```

### Custom hyperparameters

```python
from chord_gen import ChordGenerator, HyperParams

hp = HyperParams(
    min_count_2=3,  # Stricter stage 1 requirement
    discount_D=2.0,  # Smoother interpolation
    genres_to_temps={"pop_rock": 0.8, "jazz": 1.5}
)

gen = ChordGenerator(genre="jazz", hyperparams=hp, seed=42)
chords = gen.generate_song(
    num_chords=48,
    temperature=1.2,  # More exploration
    self_transition_discount=0.7  # Fewer repeats
)
```

### Multiple genres

```python
for genre in ["pop_rock", "jazz"]:
    gen = ChordGenerator(genre=genre)
    for song_idx in range(3):
        chords = gen.generate_song(num_chords=48)
        # Render chords...
```

---

## Testing Checklist

- [ ] Model loads without errors
- [ ] Generated chords parse correctly (root ∈ [0,11], valid triad names)
- [ ] No NaN or Inf in probabilities
- [ ] Bass notes are sensible (mostly 0, occasional inversions)
- [ ] Extensions are plausible (e.g., `b9` co-occurs with `b7`, not `maj7`)
- [ ] State updates correctly between timesteps
- [ ] Output is deterministic given same seed
- [ ] Varying `temperature` changes progressions smoothly
- [ ] No out-of-memory issues on long sequences (e.g., 1000 chords)
