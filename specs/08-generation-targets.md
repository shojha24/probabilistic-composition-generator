# Training-Corpus Size and Rare-Feature Targets

## 0. Goal

Define the size and composition target for the generated symbolic chord corpus used
to train automatic chord-recognition models. The corpus must provide learnable
coverage of rare triads and extensions without making the genre distributions or
local progressions implausible.

All targets in this document are **minimum coverage goals**, not instructions to
globally multiply a probability table. Natural generation should provide the
majority of events; targeted generation may fill projected deficits.

## 1. Counting conventions

- A **chord event** is one generated chord record.
- A triad count is one event for its selected triad.
- An extension count is one event containing that extension token in its
  corresponding slot. A chord with several extensions counts once for each active
  extension.
- Extension counts are therefore overlapping and must not be summed as disjoint
  chord totals.
- Root intervals are key-relative. Root-balance checks must use the encoded
  interval, not the absolute rendered pitch.

## 2. Current baseline

The current generated labels contain:

| Genre | Songs | Chord events |
|---|---:|---:|
| Jazz | 233 | 13,990 |
| Pop/rock | 238 | 14,346 |
| **Total** | **471** | **28,336** |

The extracted source distributions contain 300,100 chord events, but those are
source-corpus observations rather than generated samples.

The current generated corpus contains 14,998 events with at least one active
extension, 1,879 events with at least two active extensions, and 252 events with
all four extension slots active.

## 3. Final corpus size

The target generated corpus is:

- **250,000 chord events total**
- **125,000 jazz events**
- **125,000 pop/rock events**

The current genre-specific rates are projected to the final size using:

```text
jazz scale factor     = 125,000 / 13,990 = 8.935
pop/rock scale factor = 125,000 / 14,346 = 8.713
```

These projections are planning estimates only. Stage 1 temperature,
self-transition suppression, and context backoff can cause the realized rates to
move as more songs are generated.

## 4. Rare-triad targets

| Triad | Jazz current | Jazz projected | Jazz target | Pop current | Pop projected | Pop target |
|---|---:|---:|---:|---:|---:|---:|
| `sus2` | 16 | 143 | **1,000** | 57 | 497 | **1,000** |
| `augmented` | 184 | 1,644 | **1,500** | 43 | 375 | **750** |
| `diminished` | 742 | 6,630 | **5,000** | 150 | 1,307 | **1,500** |
| `1` | 0 | 0 | 0 | 203 | 1,769 | **2,000** |
| `5` | 82 | 733 | **1,000** | 220 | 1,917 | **2,500** |
| `sus4` reference floor | 365 | 3,261 | **3,000** | 576 | 5,019 | **3,000** |

The projected targeted shortfalls are therefore approximately:

- Jazz: 857 `sus2` and 267 `5` events.
- Pop/rock: 503 `sus2`, 375 `augmented`, 193 `diminished`, 231 `1`, and
  583 `5` events.

`diminished`, jazz `augmented`, and `sus4` are expected to meet their floors
through natural continuation. The targets for jazz `diminished` and pop `sus4`
are floors, not caps.

Jazz `1` is not targeted because it is absent from the extracted jazz vocabulary.
Unsupported roots should not be added solely to satisfy a count target; observed
rare-triad roots should be balanced before extrapolating to unsupported roots.

## 5. Rare-extension targets

| Extension | Jazz current | Jazz projected | Jazz target | Pop current | Pop projected | Pop target |
|---|---:|---:|---:|---:|---:|---:|
| `b9` | 444 | 3,967 | **3,500** | 11 | 96 | **200** |
| `#9` | 111 | 992 | **1,000** | 44 | 383 | **750** |
| `11` | 232 | 2,073 | **2,500** | 108 | 941 | **1,250** |
| `#11` | 226 | 2,019 | **2,000** | 14 | 122 | **200** |
| `13` | 132 | 1,179 | **1,500** | 39 | 340 | **500** |
| `b13` | 97 | 867 | **1,000** | 9 | 78 | **150** |

After natural scaling, the main projected deficits are:

- Jazz: 427 `11`, 321 `13`, and 133 `b13` events.
- Pop/rock: 104 `b9`, 367 `#9`, 309 `11`, 78 `#11`, 160 `13`, and 72
  `b13` events.

Common extensions should remain near their source-corpus rates rather than
receiving rare-feature boosts:

| Genre | `b7` | `7` | `9` | `bb7` |
|---|---:|---:|---:|---:|
| Jazz | approximately 60% | 12% | 8% | 6% |
| Pop/rock | approximately 20% | 3% | 5% | 1.5% |

These are per-slot rates and are not mutually exclusive with other extensions.

## 6. Dense-extension coverage

The final corpus should contain at least:

- **15,000–20,000** events with two or more active extensions.
- **3,000–5,000** events with three or more active extensions.
- **1,000** events with all four extension slots active.
- **300–500** examples of selected dense tuples such as:
  `b7+9`, `b7+b9`, `b7+#9`, `b7+9+11+13`, and
  `b7+9+#11+b13`.

The corpus does not need 1,000 examples of every possible full extension tuple.

## 7. Generation requirements

1. The remaining corpus should be generated primarily with the existing
   genre-specific model. Targeted rare-feature generation should fill projected
   deficits rather than globally multiplying rare-state probabilities.
2. Rare targets must use observed or backoff-supported local predecessor and
   successor contexts. A rare chord should not be inserted after an arbitrary
   state solely to satisfy a quota.
3. Extension targets must respect genre- and triad-specific support:
   jazz `sus2` should remain overwhelmingly bare; pop `sus2` should remain mostly
   bare; `1` and `5` should not acquire artificial extensions.
4. Rare-triad extension selection must not silently replace a reliable
   triad-specific distribution with a genre-wide distribution when that creates
   unsupported combinations. Low-count triad distributions should instead be
   smoothed toward the genre distribution.
5. Targeted rare events should be root-balanced within observed support and
   separated in time unless repeated rare motifs are supported by the corpus.
6. A natural held-out validation set must remain separate from the quota-driven
   training corpus.

## 8. Acceptance checks

Generation is complete only when the final corpus is checked for:

- 250,000 total events with the intended genre split.
- All triad and extension floors in §§4–5.
- Root coverage and root-frequency balance for each rare feature.
- Predecessor/successor transition divergence from the source distributions.
- Repeated-state and clustered-rare-event rates.
- Extension tuple coverage and genre-appropriate extension rates.
- Voicing/DCT/extension-retention validity after symbolic events are rendered.
