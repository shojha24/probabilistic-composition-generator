# Domain Knowledge Base: AAM & Chordformer Pipeline

This document contains the strict music theory, formatting, and syntax rules for the pipeline. ALL agents must adhere to these rules. Do not hallucinate external music theory logic.

## 1. Chordformer Component Vocabulary
Chords are represented as a factored tuple: `(root, triad, bass, 7th, 9th, 11th, 13th)`.
"N" always denotes the absence of a component or a "no chord" state.

* **Root/Bass:** {N, C, C#/Db, D, D#/Eb, E, F, F#/Gb, G, G#/Ab, A, A#/Bb, B}
* **Triad:** {N, major, minor, sus4, sus2, diminished, augmented}
* **7th:** {N, 7, b7, bb7}
* **9th:** {N, 9, #9, b9}
* **11th:** {N, 11, #11}
* **13th:** {N, 13, b13}

**CRITICAL RULE:** The `bb7` (double-flat 7th) is strictly used for fully diminished 7th chords. It is theoretically distinct from `b7` and must NOT be collapsed to `b7`.

---

## 2. Harte String Decomposition Mapping
When decomposing simplified Harte strings or Weimar Leadsheet notation into components:

### Triad Mapping
* `maj`, `maj7`, `maj9`, `maj6`, `maj(9)`, `6` -> **major**
* `min`, `min7`, `min9`, `min6`, `m`, `-` -> **minor**
* `dim`, `dim7`, `hdim7` -> **diminished**
* `aug`, `aug7` -> **augmented**
* `sus4`, `7sus4` -> **sus4**
* `sus2` -> **sus2**

### 7th Mapping
* `maj7` -> **7** (major 7th interval)
* `dom7`, plain `7`, `min7`, `hdim7` -> **b7** (minor 7th interval)
* `dim7` -> **bb7** (diminished 7th interval)

### Extensions
* **9th:** `maj9`, `min9`, `9`, `add9` -> **9** | `#9`, `7#9` -> **#9** | `b9`, `7b9` -> **b9**
* **11th:** `11`, `min11`, `maj11` -> **11** | `#11` -> **#11**
* **13th:** `13`, `dom13` -> **13** | `b13`, `7b13` -> **b13**

### Bass & Slash Chords
Slash notation (e.g., `C:maj/5` or `C:maj/G`) dictates the bass component.
* If a bass note is generated via algorithmic voice-leading, it overrides the root in the `bass` column. 
* Never default the bass column to "N" unless the entire chord is "N". Default to the Root.

---

## 3. JFugue Rendering Syntax
The downstream Java renderer natively supports a wide vocabulary of complex chords. Do NOT collapse 9ths, 11ths, or 13ths to their 7th chord bases unless dealing with an unsupported edge case. Map them to JFugue's explicit syntax:

### Explicit JFugue Mappings
* **Dominants:** `7` -> `dom7`, `9` -> `dom9`, `11` -> `dom11`, `13` -> `dom13`
* **Major Extensions:** `maj9` -> `maj9`, `maj13` -> `maj13`
* **Minor Extensions:** `min9` -> `min9`, `min11` -> `min11`, `min13` -> `min13`
* **Altered Dominants:** Use angle brackets for alterations (e.g., `#5` -> `>5`, `b5` -> `<5`). 
  * Example: `7b5` -> `dom7<5`
  * Example: `7#5#9` -> `dom7>5>9`
* **Half-Diminished:** JFugue does not have an explicit `hdim7` or `min7b5` named preset in its core table. Map Harte `hdim7` explicitly by modifying a minor 7th: `min7<5`.

### Bass Inversions
JFugue uses the `^` symbol for bass notes. Slash notation like `C:maj/E` MUST be formatted as `Cmaj^E`. If mapping an extended chord with a bass note, the format is `Root` + `JFugue_Quality` + `^` + `Bass` (e.g., `Gdom13^B`).

### Edge-Case Overdubbing (The Multi-Voice Hack)
If a chord combination cannot be mapped safely to JFugue's dictionary (e.g., "add" chords like `add11` where JFugue would falsely inject 7ths and 9ths, or mutually exclusive dual-alterations), do NOT use a single JFugue preset. Instead, use JFugue's multi-voice syntax to overlay the isolated extension on a separate channel.
* **Format:** Render the safe base triad on Voice 0 (`V0`), and calculate the literal note name for the extension to render on Voice 1 (`V1`).
* **Example (`Cadd11`):** `"V0 Cmaj V1 F5"` (This plays the C major triad and an F note simultaneously).

---

## 4. Annotation Format Specification
The output `.lab` file must follow this exact 10-column tab-separated or space-separated format:
`time_start  time_end  root  triad  bass  7th  9th  11th  13th  harte_shorthand`

*Example:*
`2.0000  3.0000  D  minor  D  b7  9  N  N  D:min9`
*(Note: If bass is root position, explicitly write the root note in the bass column, do not use N).*

---

## 5. MIDI Humanization Rules (Renderer)
When applying Gaussian jitter to MIDI ticks in the renderer:
1. **Tempo Proportional:** Jitter amount must scale dynamically with the BPM. Do not use a static tick value.
2. **Note Pairing:** The exact jitter offset applied to a `NOTE_ON` event MUST be cached and applied identically to the corresponding `NOTE_OFF` event. Do not randomize them independently, or duration artifacts will occur.