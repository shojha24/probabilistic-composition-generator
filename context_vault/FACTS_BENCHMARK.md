# FACTS_BENCHMARK: Ground-Truth Empirical Specification Sheet
## Sections 4 ("Experimental Setup") & 5 ("Results & Discussion")
### Model: ChordFormer (Conformer Backbone + 6-Head Structured Decomposition)
### Benchmark: 1,217-Song Real Corpus (McGill Billboard, USPop2002, Isophonics) vs. 5,000-Song Synthetic Pretraining

---

## 1. Audio Preprocessing & CQT Feature Extraction

### 1.1 Audio Ingestion & Sampling Parameters
* **Audio Sampling Rate ($f_s$):** Exactly $22,050\text{ Hz}$ (`settings.py:L14`, `DEFAULT_SR = 22050`).
* **Channel Configuration & Mono Conversion:** Input audio is downmixed to single-channel mono upon loading via `librosa`/`mir` audio reader (`mir/io/implement/music_io.py:L1-L45`).
* **Frame Hop Size ($H$):** Exactly $512\text{ samples}$ (`settings.py:L15`, `DEFAULT_HOP_LENGTH = 512`; `extractors/cqt.py:L41`, `CQTV2`).
* **Frame Temporal Duration:**
  $$\Delta t = \frac{H}{f_s} = \frac{512}{22050} \approx 0.02321995\text{ s} \approx 23.22\text{ ms}$$
  (`extractors/xhmm_ismir.py:L172, L189`, `delta_time = entry.prop.hop_length / entry.prop.sr`).
* **Window Size ($N_{\text{win}}$):** $8,192\text{ samples}$ (`settings.py:L17`, `DEFAULT_WIN_SIZE = 8192`).

### 1.2 Constant-Q Transform (CQT) Parameters
* **Extraction Engine:** `librosa.core.hybrid_cqt` (`extractors/cqt.py:L41-L47`, `CQTV2`).
* **Bins per Octave ($b$):** Exactly $36\text{ bins/octave}$ (`extractors/cqt.py:L42`, `bins_per_octave = 36`).
  * Resolution: $\frac{36\text{ bins}}{12\text{ semitones}} = 3\text{ bins per semitone}$ ($33.3\text{ cents per bin}$).
* **Minimum Frequency ($f_{\min}$):**
  $$f_{\min} = \text{librosa.note\_to\_hz('F\#0')} = 440.0 \times 2^{\frac{18 - 69}{12}} \approx 23.12465\text{ Hz}$$
  (`extractors/cqt.py:L43`, `fmin = librosa.note_to_hz('F#0')`).
* **Total Frequency Bins Extracted ($F_{\text{raw}}$):** Exactly $288\text{ bins}$ (`extractors/cqt.py:L44`, `n_bins = 288`).
  * Total frequency span: $\frac{288}{36} = 8.0\text{ octaves}$, spanning from $\text{F}\sharp_0$ ($23.12\text{ Hz}$) to $\text{F}_8$ ($5,587.65\text{ Hz}$).
* **Tuning:** `tuning = None` (fixed standard pitch reference $A_4 = 440.0\text{ Hz}$) (`extractors/cqt.py:L45`).

### 1.3 Pitch Shifting Window & Effective Feature Dimension ($F$)
* **Data Augmentation Pitch Shift Range:** $[-5, +6]\text{ semitones}$ (`confor_head16.py:L38-40`, `SHIFT_LOW = -5`, `SHIFT_HIGH = 6`, `SHIFT_STEP = 3`).
* **Shift Grid:** 12 discrete semitone shifts: $\{-5, -4, -3, -2, -1, 0, +1, +2, +3, +4, +5, +6\}$.
* **Feature Window Slicing (`SPEC_DIM`):** Exactly $252\text{ bins}$ (`confor_head16.py:L41`, `SPEC_DIM = 252`).
  * Total span of sliced input: $\frac{252}{36} = 7.0\text{ octaves}$.
  * At shift $0$, the slice offset is $\text{SHIFT\_HIGH} \times \text{SHIFT\_STEP} = 6 \times 3 = 18\text{ bins}$ (`confor_head16.py:L583`).
  * 18 bins above $\text{F}\sharp_0$ (at 3 bins/semitone) is exactly 6 semitones higher: $\text{F}\sharp_0 + 6 = \text{C}_1$ ($32.703\text{ Hz}$).
  * Therefore, the unshifted $252$-bin window precisely covers $\text{C}_1$ ($32.70\text{ Hz}$) through $\text{B}_7$ ($3,951.07\text{ Hz}$).
* **Log-Amplitude Scaling & Decibel Conversion:**
  $$X_{\text{dB}} = \text{librosa.amplitude\_to\_db}(|X|, \text{ref}=\max |X|) = 20 \log_{10}\left(\frac{|X|}{\max |X|}\right)$$
  Dynamic range is clipped to $[-80.0\text{ dB}, 0.0\text{ dB}]$ (`extractors/cqt.py:L47-L48`, `cqt_specaugment.py:L12`).
* **Feature Quantization & Storage:** Spectrograms are stored as 16-bit signed integers (`np.int16`) in HDF5 archives (`storage_creation.py:L19`, `create_synth_storage.py:L68`).
* **Input Zero-Padding for Conformer Alignment:**
  In `ChordNet.forward()`, the $252$-bin input is zero-padded on the right by $4\text{ bins}$:
  $$\mathbf{x}_{\text{padded}} = \text{pad}(\mathbf{x}, (0, 4, 0, 0, 0, 0), \text{mode}='constant', \text{value}=0) \in \mathbb{R}^{B \times T \times 256}$$
  (`confor_head16.py:L557-559`). This satisfies the $16$-head Conformer input dimension requirement ($256 = 16\text{ heads} \times 16\text{ dim/head}$).

### 1.4 Temporal Chunking & Sequence Lengths
* **Training Sample Length ($T_{\text{train}}$):** Exactly $1,000\text{ frames}$ (`confor_head16.py:L42`, `LSTM_TRAIN_LENGTH = 1000`).
  $$T_{\text{train, seconds}} = 1000 \times \frac{512}{22050} \approx 23.21995\text{ s}$$
* **Training Stride / Sampling:** Chunks of 1,000 frames are sampled with 1 average sample per song per epoch (`confor_head16.py:L712`, `average_samples_per_song = 1`).
* **Validation & Evaluation Temporal Length:** Full song duration ($T_{\text{val}} = -1$, no chunking or truncation; the entire audio sequence is evaluated in a single forward pass) (`confor_head16.py:L718`, `train_sample_length = -1`; `val_chordformer_head16.py:L112-113`).
* **Production Serving Chunk Length:** Exactly $431\text{ frames}$ ($10.0\text{ seconds}$ @ $23.22\text{ ms/frame}$) (`export_ensemble_onnx.py:L18`, `benchmarks/BENCHMARK_REPORT.md:L8`).

---

## 2. ChordFormer Architecture & 6-Head Decomposition

### 2.1 Conformer Backbone Specification
The backbone consists of $N_{\text{layers}} = 4$ identical Macaron-style Conformer blocks (`confor_head16.py:L433-451, L487`, `ChordConformer`).

```
Input Feature: x in R^{B x T x 256} (252 CQT bins + 4 zero-pad bins)
       │
┌──────▼──────────────────────────────────────────────────────┐
│ Conformer Block 1..4                                       │
│   1. Half-step Feed-Forward (FFN1): x = x + 0.5 * FFN1(x)  │
│   2. Convolution Module (convolution_first = True):         │
│      LayerNorm -> Pointwise Conv1d (256 -> 512) -> GLU     │
│      -> 1D Depthwise Conv (K=31, groups=256, SAME pad 15)  │
│      -> GroupNorm (num_groups=1, num_channels=256)         │
│      -> SiLU (Swish) -> Pointwise Conv1d (256 -> 256)      │
│      -> Dropout (p=0.1) -> Residual Connection             │
│   3. Multi-Head Self-Attention (MHSA):                     │
│      LayerNorm -> 16 Attention Heads (d_k=16, d_model=256) │
│      -> Dropout (p=0.1) -> Residual Connection             │
│   4. Half-step Feed-Forward (FFN2): x = x + 0.5 * FFN2(x)  │
│   5. Final LayerNorm(256)                                  │
└──────┬──────────────────────────────────────────────────────┘
       │
Linear Projection Head: nn.Linear(256, 124)
       │
       ▼
6 Structured Sub-Vectors (124 total logits)
```

#### Detailed Layer Hyperparameters:
* **Model Hidden Dimension ($d_{\text{model}}$):** Exactly $256$ (`confor_head16.py:L487`, `input_dim = 256`).
* **Number of Conformer Layers ($N_L$):** Exactly $4$ (`confor_head16.py:L487`, `num_layers = 4`).
* **Number of Attention Heads ($h$):** Exactly $16$ (`confor_head16.py:L487`, `num_heads = 16`).
  * Per-head projection dimension: $d_k = \frac{d_{\text{model}}}{h} = \frac{256}{16} = 16$.
* **Feed-Forward Expansion Dimension ($d_{\text{ffn}}$):** Exactly $1,024$ ($4\times$ expansion factor) (`confor_head16.py:L487`, `ffn_dim = 1024`).
* **Depthwise Convolution Kernel Size ($K$):** Exactly $31\text{ frames}$ (`confor_head16.py:L487`, `depthwise_conv_kernel_size = 31`).
  * Temporal receptive field of depthwise convolution: $31 \times 23.22\text{ ms} \approx 719.8\text{ ms}$.
  * Padding: $\frac{K - 1}{2} = 15\text{ frames}$ on each side (`SAME` padding) (`confor_head16.py:L196`).
* **Convolution Module Execution Order:** `convolution_first = True` (convolution module is applied before the multi-head self-attention module) (`confor_head16.py:L449, L327-328`).
* **Normalization in Conv Module:** `use_group_norm = True` (`confor_head16.py:L448`). Uses `torch.nn.GroupNorm(num_groups=1, num_channels=256)` (equivalent to LayerNorm across channel dimension) rather than BatchNorm1d (`confor_head16.py:L200-201`), ensuring inference determinism independent of batch size.
* **Dropout Probabilities:** $p = 0.10$ across all attention, convolution, and feed-forward modules (`confor_head16.py:L447`, `dropout = 0.1`).
* **Positional Encoding:** No static sinusoidal positional embeddings are injected into inputs. Local temporal ordering and relative translation invariance are provided natively by the $K=31$ depthwise convolution module (`confor_head16.py:L460-464`).

### 2.2 Parameter Count & Module Breakdown
* **Active Trainable Parameters (Forward Graph):** Exactly **$6,123,644$ parameters** (`confor_head16.py:L487`).
* **Defined Module Total Parameters:** Exactly **$6,147,576$ parameters** (including an auxiliary uncalled linear layer `final_fc1` of $23,932$ parameters instantiated at `confor_head16.py:L488` but bypassed during `forward()`).

#### Exact Layer-by-Layer Parameter Breakdown:
| Sub-Module | Layer Components | Tensor Shapes | Parameter Count |
| :--- | :--- | :--- | :--- |
| **FFN1 (per layer $\times 4$)** | LayerNorm(256), Linear(256, 1024), Linear(1024, 256) | $(256) \times 2$, $(1024, 256)$, $(1024)$, $(256, 1024)$, $(256)$ | $526,848 \times 4 = 2,107,392$ |
| **MHSA (per layer $\times 4$)** | LayerNorm(256), In-Proj (Q, K, V), Out-Proj | $(256) \times 2$, $(768, 256)$, $(768)$, $(256, 256)$, $(256)$ | $263,936 \times 4 = 1,055,744$ |
| **ConvModule (per layer $\times 4$)** | LayerNorm(256), Pointwise1, Depthwise(K=31), GroupNorm, Pointwise2 | $(256) \times 2$, $(512, 256, 1)$, $(512)$, $(256, 1, 31)$, $(256)$, $(256) \times 2$, $(256, 256, 1)$, $(256)$ | $271,872 \times 4 = 1,087,488$ |
| **FFN2 (per layer $\times 4$)** | LayerNorm(256), Linear(256, 1024), Linear(1024, 256) | $(256) \times 2$, $(1024, 256)$, $(1024)$, $(256, 1024)$, $(256)$ | $526,848 \times 4 = 2,107,392$ |
| **Final LayerNorm (per layer $\times 4$)** | LayerNorm(256) | $(256) \times 2$ | $512 \times 4 = 2,048$ |
| **Classification Projection** | nn.Linear(256, 124) | $(124, 256)$, $(124)$ | $31,744 + 124 = 31,868$ |
| **Active Subtotal** | **All 4 Conformer Layers + Output Head** | — | **$6,123,644$** |
| *Auxiliary Layer* | *nn.Linear(192, 124) (unused in forward)* | $(124, 192)$, $(124)$ | $23,808 + 124 = 23,932$ |
| **Total Instantiated** | **`ChordNet` Total Parameters** | — | **$6,147,576$** |

### 2.3 Structured 6-Head Output Space (124 Total Logits)
The final linear layer maps each frame's $256$-dimensional embedding to $124$ logits, which are partitioned into $6$ structurally independent categorical heads (`confor_head16.py:L564-574`):

$$\hat{\mathbf{z}} = \mathbf{W}_{\text{out}} \mathbf{h}_t + \mathbf{b}_{\text{out}} \in \mathbb{R}^{124}$$
$$\hat{\mathbf{z}} = \left[ \hat{\mathbf{z}}_{\text{root+triad}}^{(0..96)} \,\Big|\, \hat{\mathbf{z}}_{\text{bass}}^{(97..109)} \,\Big|\, \hat{\mathbf{z}}_{7\text{th}}^{(110..113)} \,\Big|\, \hat{\mathbf{z}}_{9\text{th}}^{(114..117)} \,\Big|\, \hat{\mathbf{z}}_{11\text{th}}^{(118..120)} \,\Big|\, \hat{\mathbf{z}}_{13\text{th}}^{(121..123)} \right]$$

```
Logit Range:  [0 ────────── 96][97 ────── 109][110 ── 113][114 ── 117][118 ── 120][121 ── 123]
Output Head:   Head 0: Triad   Head 1: Bass    Head 2: 7th Head 3: 9th Head 4: 11th Head 5: 13th
Cardinality:     97 classes     13 classes      4 classes   4 classes   3 classes   3 classes
```

#### Detailed Head Vocabulary and Index Mappings:
1. **Head 0: Root + Triad Quality ($97\text{ classes}$, logits $0..96$):**
   * Class $0$: `N` (No-chord / silence / unpitched) (`complex_chord.py:L237-240, L250`).
   * Classes $1..96$: 12 Root Pitch Classes $\times$ 8 Triad Qualities (`complex_chord.py:L33-43, L253`):
     $$\text{Class Index} = 1 + 12 \times (q - 1) + r \quad (r \in [0, 11], \, q \in [1, 8])$$
     * $q=1$ (`maj`): Indices $1..12$ (`C:maj` through `B:maj`).
     * $q=2$ (`min`): Indices $13..24$ (`C:min` through `B:min`).
     * $q=3$ (`sus4`): Indices $25..36$ (`C:sus4` through `B:sus4`).
     * $q=4$ (`sus2`): Indices $37..48$ (`C:sus2` through `B:sus2`).
     * $q=5$ (`dim`): Indices $49..60$ (`C:dim` through `B:dim`).
     * $q=6$ (`aug`): Indices $61..72$ (`C:aug` through `B:aug`).
     * $q=7$ (`5` / power chord): Indices $73..84$ (`C:5` through `B:5`).
     * $q=8$ (`1` / single root tone): Indices $85..96$ (`C:1` through `B:1`).
2. **Head 1: Bass Pitch Class ($13\text{ classes}$, logits $97..109$):**
   * Class $0$ (target index $-1 + 1 = 0$): `N` (no distinct bass note / silence) (`confor_head16.py:L533`).
   * Classes $1..12$ (target index $r + 1$): Absolute bass pitch classes $C, C\sharp, D, E\flat, E, F, F\sharp, G, A\flat, A, B\flat, B$ (`complex_chord.py:L24, L227-234`).
3. **Head 2: 7th Extension Degree ($4\text{ classes}$, logits $110..113$):**
   * Class $0$: `none` (unextended triad).
   * Class $1$: `add_7` (Major 7th, interval 11 semitones).
   * Class $2$: `add_b7` (Minor 7th, interval 10 semitones).
   * Class $3$: `add_bb7` (Diminished 7th / Major 6th, interval 9 semitones) (`complex_chord.py:L46-52`).
4. **Head 3: 9th Extension Degree ($4\text{ classes}$, logits $114..117$):**
   * Class $0$: `none` (no 9th degree).
   * Class $1$: `add_9` (Major 9th, interval 14 semitones / 2 semitones compound).
   * Class $2$: `add_#9` (Augmented 9th, interval 15 semitones / 3 semitones compound).
   * Class $3$: `add_b9` (Minor 9th, interval 13 semitones / 1 semitone compound) (`complex_chord.py:L54-60`).
5. **Head 4: 11th Extension Degree ($3\text{ classes}$, logits $118..120$):**
   * Class $0$: `none` (no 11th degree).
   * Class $1$: `add_11` (Perfect 11th, interval 17 semitones / 5 semitones compound).
   * Class $2$: `add_#11` (Augmented 11th, interval 18 semitones / 6 semitones compound) (`complex_chord.py:L62-67`).
6. **Head 5: 13th Extension Degree ($3\text{ classes}$, logits $121..123$):**
   * Class $0$: `none` (no 13th degree).
   * Class $1$: `add_13` (Major 13th, interval 21 semitones / 9 semitones compound).
   * Class $2$: `add_b13` (Minor 13th, interval 20 semitones / 8 semitones compound) (`complex_chord.py:L69-75`).

### 2.4 Inference Decoding & Harte Recombination
* **Frame-Level Multi-Head Logit Extraction:**
  $$\mathbf{p}_h(t) = \text{Softmax}(\hat{\mathbf{z}}_h(t)) \quad \forall h \in \{0, 1, 2, 3, 4, 5\}$$
* **Decoding Algorithm (CRF Viterbi Transition Smoothing):**
  A dynamic programming Viterbi decoder computes the optimal state path over the sequence (`extractors/xhmm_ismir.py:L107-135`, `calibrate_biases.py:L16-48`):
  $$S^*(t) = \arg\max_{c} \left\{ \max_{c'} \left( V(t-1, c') - \tau \cdot \mathbb{I}(c' \neq c) \right) + \log P(c \mid \mathbf{x}_t) \right\}$$
  * **Transition Penalty Parameter ($\tau$):**
    * Multi-Head Evaluation scripts: $\tau = 2.0$ (`calibrate_biases.py:L50`, `eval_heads.py:L15`).
    * Full MIREX pipeline: $\tau = 30.0$ (`extractors/xhmm_ismir.py:L7`, `diff_trans_penalty = 30.0`).
* **Harte Recombination Rules:**
  Candidate chord dictionary templates (`data/ismir2017_chord_list.txt` or `data/full_chord_list.txt`) define permissible pitch component tuples:
  $$\text{Chord} = (\text{Root}, \text{TriadQuality}, \text{Bass}, \text{Ext}_7, \text{Ext}_9, \text{Ext}_{11}, \text{Ext}_{13})$$
  The composite log-probability for candidate template $k$ at frame $t$ is summed across active sub-heads (`extractors/xhmm_ismir.py:L63-78`):
  $$\log P_k(t) = \log p_{\text{triad}}(t, k_{\text{triad}}) + \mathbb{I}(\text{use\_bass}) \log p_{\text{bass}}(t, k_{\text{bass}}) + \sum_{e \in \{7, 9, 11, 13\}} \log p_e(t, k_e)$$
  Templates are formatted as standard Christopher Harte chord label strings (e.g. `C:maj`, `A:min7`, `G:7(#9)`, `D:sus4(b7,9)/4`, `F#:min(11)`) for evaluation via `mir_eval.chord` (`test_for_all_mirex.py:L34, L101-106`).

---

## 3. Datasets, Splits & Cross-Validation Protocol

### 3.1 Real Benchmark Dataset (1,217 Songs)
The real evaluation corpus consists of 1,217 audio tracks compiled from three canonical MIR reference datasets (`data/all_1217.csv`, `chord_data_1217/references_v2/`):
1. **McGill Billboard Dataset:** 731 songs (60.07% of corpus) curated by John Ashley Burgoyne (`chord_data_1217/references_v2/*.jams`).
2. **USPop2002 / MARL Dataset:** 287 songs (23.58% of corpus) curated by Tae Min Cho (`chord_data_1217/references_v2/*.jams`).
3. **Isophonics Dataset (Beatles, Queen, Zweieck):** 199 songs (16.35% of corpus) curated by the Centre for Digital Music (C4DM, Queen Mary University of London) (`chord_data_1217/references_v2/*.jams`).

#### 5-Fold Cross-Validation Partition (`train_eval_test_split.py:L10-25`):
Splits were generated using pseudo-random seed `20190326` (`np.random.seed(20190326)`). The test partitions are mutually exclusive and collectively exhaustive ($\sum \text{Test} = 1,217$ songs). Validation subsets are partitioned as $25\%$ of the non-test split:

| Cross-Validation Fold | Train Songs Count | Validation Songs Count | Test Songs Count | Total Songs | Total Audio Frames Evaluated |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **Fold 0 (`s0`)** | 730 | 243 | 244 | 1,217 | $2,298,416$ |
| **Fold 1 (`s1`)** | 730 | 243 | 244 | 1,217 | $2,291,008$ |
| **Fold 2 (`s2`)** | 731 | 243 | 243 | 1,217 | $2,284,544$ |
| **Fold 3 (`s3`)** | 731 | 243 | 243 | 1,217 | $2,277,120$ |
| **Fold 4 (`s4`)** | 731 | 243 | 243 | 1,217 | $2,285,269$ |
| **5-Fold Combined Total** | — | — | **1,217** | **1,217** | **$11,436,357\text{ frames}$** |

### 3.2 Synthetic Pretraining Dataset (5,000 Songs)
* **Corpus Source:** 5,000 full synthetic audio tracks generated by `probabilistic-composition-generator` under `gen/acr-conditions-5k/naturalistic/audio/song_0` through `song_4999` with multi-instrument MIDI arrangement, FluidSynth soundfont rendering, and random acoustic mix augmentation (`create_synth_storage.py:L14-25`).
* **Storage Footprint:**
  * `dataset/synth_naturalistic_cqt.h5d`: $21.8\text{ GB}$ (288-bin CQT matrices).
  * `dataset/synth_naturalistic_xchord.h5d`: $424.6\text{ MB}$ (frame annotations).
* **Pretraining Partition:** 4,500 train tracks / 500 held-out validation tracks (`pretrain_synthetic.py:L40-48`, seed `42`).

### 3.3 Class Imbalance: Real vs. Synthetic Distributions
In the real 1,217-song dataset (Fold 0 train partition, `data/cross_subpart_weight0.pkl`), natural harmonic conventions create severe class skew. The synthetic generator drastically flattens these disparities:

| Structural Head | Class / Harmonic Degree | Real Dataset Frame Count (Fold 0) | Real Dataset Ratio (vs. Majority) | Synthetic Dataset Sampling Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Triad Quality** | `maj` | $4,465,981\text{ frames}$ | $1.0 : 1\text{ (Anchor)}$ | Naturalistic prior ($0.450$) |
| **Triad Quality** | `min` | $1,586,913\text{ frames}$ | $2.8 : 1$ | Naturalistic prior ($0.270$) |
| **Triad Quality** | `sus4` | $146,527\text{ frames}$ | $30.5 : 1$ | Oversampled ($0.080$, $4.3\times$ boost) |
| **Triad Quality** | `sus2` | $20,531\text{ frames}$ | **$217.5 : 1$** | Oversampled ($0.050$, $10.9\times$ boost) |
| **Triad Quality** | `dim` | $33,534\text{ frames}$ | **$133.2 : 1$** | Oversampled ($0.060$, $8.0\times$ boost) |
| **Triad Quality** | `aug` | $15,272\text{ frames}$ | **$292.4 : 1$** | Oversampled ($0.040$, $11.7\times$ boost) |
| **Triad Quality** | `5` (power) | $180,371\text{ frames}$ | $24.8 : 1$ | Controlled representation ($0.050$) |
| **7th Extension** | `N` (unextended) | $4,871,540\text{ frames}$ | $1.0 : 1\text{ (Anchor)}$ | Naturalistic unextended triads |
| **7th Extension** | `b7` (dominant/minor 7th) | $1,433,839\text{ frames}$ | $3.4 : 1$ | Diatonic & blues progressions |
| **7th Extension** | `7` (major 7th) | $240,318\text{ frames}$ | $20.3 : 1$ | Diatonic jazz/pop voicings |
| **7th Extension** | `bb7` (diminished 7th / 6th) | $4,378\text{ frames}$ | **$1,112.7 : 1$** | Guaranteed insertion on `dim7` |
| **9th Extension** | Natural `9` | $333,926\text{ frames}$ | $18.5 : 1$ | Extended chord voicings |
| **9th Extension** | Altered `#9` | $27,864\text{ frames}$ | **$221.8 : 1$** | Oversampled on dominant chords |
| **9th Extension** | Altered `b9` | $8,763\text{ frames}$ | **$705.2 : 1$** | Oversampled on dominant chords |
| **11th Extension** | Natural `11` | $84,122\text{ frames}$ | $76.8 : 1$ | Comping & pad voicings |
| **11th Extension** | Altered `#11` | $5,804\text{ frames}$ | **$1,113.1 : 1$** | Lydian dominant voicings |
| **13th Extension** | Natural `13` | $141,165\text{ frames}$ | $45.4 : 1$ | Extended dominant voicings |
| **13th Extension** | Altered `b13` | $5,522\text{ frames}$ | **$1,159.3 : 1$** | Altered dominant voicings |

---

## 4. Loss Function & Class-Imbalance Reweighting

### 4.1 Multi-Task Loss Formulation
The overall training objective is a multi-task sum of conditional cross-entropy losses across all 6 heads (`confor_head16.py:L122-138`):

$$\mathcal{L}_{\text{total}} = \sum_{h=0}^5 \lambda_h \cdot \mathcal{L}_h(\hat{\mathbf{z}}_h, \mathbf{y}_h; \mathbf{w}_h)$$

Where each head loss $\mathcal{L}_h$ applies weighted cross-entropy over valid annotated frames ($y_h \ge 0$):
$$\mathcal{L}_h(\hat{\mathbf{z}}_h, \mathbf{y}_h; \mathbf{w}_h) = -\frac{1}{\sum_{t} \mathbb{I}(y_{h,t} \ge 0)} \sum_{t: y_{h,t} \ge 0} w_{h, y_{h,t}} \log \left( \frac{\exp(\hat{z}_{h,t,y_{h,t}})}{\sum_{c} \exp(\hat{z}_{h,t,c})} \right)$$

### 4.2 Class-Reweighting Mathematical Formula
Class weights $\mathbf{w}_h$ are derived from inverse class frequencies in the real training partition (`confor_head16.py:L58-69`):

$$w_{h, c} = \min \left( \max \left( \left( \frac{\max_k N_{h, k}}{\max(N_{h, c}, 1.0)} \right)^\alpha, C_{\min} \right), C_{\max} \right)$$

#### Active Benchmark Hyperparameters (Used for Paper Results, commit `5767249`):
* **Power Exponent ($\alpha$):** Exactly $0.50$ (square-root inverse frequency) (`confor_head16.py:L55`, `power = 0.5`).
* **Maximum Weight Clip ($C_{\max}$):** Exactly $10.0$ (`confor_head16.py:L55`, `max_clip = 10.0`).
* **Minimum Weight Clip ($C_{\min}$):** `None` (unbounded below; $w_{\text{majority}} = 1.0$) (`confor_head16.py:L55`).
* **Head Loss Multipliers ($\boldsymbol{\lambda}$):** $\boldsymbol{\lambda} = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]$ (`confor_head16.py:L132-137`).
* **Heuristic Clamping Rules:**
  1. **`b7_clamp = True` (Head 2: 7th Extension):**
     $$w_{2, \text{b7}} = \min\left( \max(0.80 \times w_{2, \text{7}}, 3.5), 0.85 \times w_{2, \text{7}} \right) \approx \mathbf{3.602}$$
     (`confor_head16.py:L74-79`).
  2. **`ninth_clamp = True` (Head 3: 9th Extension):**
     $$w_{3, \text{9}} = \max\left( w_{3, \text{9}}, \min(6.0, 0.60 \times \min(w_{3, \text{\#9}}, w_{3, \text{b9}})) \right) = \mathbf{6.000}$$
     (`confor_head16.py:L84-88`).
  3. **`triad_clamp = True` (Head 0: Triad Quality):**
     * $w_{\text{sus4}} \ge \min(6.8, 0.95 \times \min_{\text{rare}}) = \mathbf{6.800}$ (`confor_head16.py:L100-104`).
     * $w_5 \ge \min(6.0, 0.85 \times \min_{\text{rare}}) = \mathbf{6.000}$ (`confor_head16.py:L105-109`).
     * $w_{\min} \ge \mathbf{2.000}$ (`confor_head16.py:L110-114`).

#### Spec 01 Calibrated Hyperparameters (`specs/01-multi-head-loss-calibration-and-debiasing.md`):
* **Calibrated Exponent ($\alpha$):** $0.25$ (fourth-root inverse frequency, dampens extreme weight gradients).
* **Calibrated Clip ($C_{\max}$):** $4.0$.
* **Calibrated Clamps:** All disabled (`b7_clamp = False`, `triad_clamp = False`, `ninth_clamp = False`).
* **Calibrated Task Multipliers:** $\boldsymbol{\lambda} = [1.00, 1.25, 0.80, 0.50, 0.30, 0.20]$.

---

## 5. Training Regimens: Baseline vs. Sim-to-Real (Pretrain $\to$ Finetune)

### 5.1 Side-by-Side Training Specification Table

| Hyperparameter / Component | Stage 0: Baseline (Real 1,217 Only) | Stage 1: Synthetic Pretraining | Stage 2: Proposed Fine-Tuning |
| :--- | :--- | :--- | :--- |
| **Script Entrypoint** | `confor_head16.py` (`mir/nn/train.py`) | `pretrain_synthetic.py` | `finetune_real_fold.py` |
| **Training Corpus** | 1,217 real songs (5-fold CV) | 5,000 synthetic random-mix tracks | 1,217 real songs + Synthetic Replay |
| **Train / Val Partition** | 730/731 train, 243 val per fold | 4,500 train, 500 val holdout | 730/731 train, 243 val per fold |
| **Optimizer** | `AdamW` (`betas=(0.9, 0.999)`) | `AdamW` (`betas=(0.9, 0.999)`) | `AdamW` (`betas=(0.9, 0.999)`) |
| **Mixed Precision (AMP)** | FP16 with `GradScaler` | FP16 with `GradScaler` | FP16 with `GradScaler` |
| **Batch Size ($B$)** | 48 (or 36) | 36 | 36 |
| **Sequence Chunk Length ($T$)** | 1,000 frames ($23.22\text{ s}$) | 1,000 frames ($23.22\text{ s}$) | 1,000 frames ($23.22\text{ s}$) |
| **Data Augmentation** | Pitch shifting $[-5, +6]$ semitones | Pitch shift + SpecAugment | Pitch shift (real) + SpecAugment (replay) |
| **SpecAugment: Freq Masks** | None | 2 masks, width $\le 24\text{ bins}$ | Applied to synthetic replay stream only |
| **SpecAugment: Time Masks** | None | 2 masks, width $\le 60\text{ frames}$ | Applied to synthetic replay stream only |
| **SpecAugment: Mask Value** | — | $-80.0\text{ dB}$ | $-80.0\text{ dB}$ |
| **Initial Learning Rate** | $1 \times 10^{-3}$ | $1 \times 10^{-3}$ | $1 \times 10^{-4}$ |
| **LR Schedule (Epochs)** | $\{10^{-3}: 12, 10^{-4}: 8, 10^{-5}: 4\}$ | $\{10^{-3}: 4, 10^{-4}: 2, 10^{-5}: 1\}$ | $\{10^{-4}: 12, 10^{-5}: 8, 10^{-6}: 4\}$ |
| **Total Epochs** | 24 epochs per fold | **7 epochs** ($\approx 10,500$ mini-batches) | **24 epochs** per fold |
| **Discriminative LR** | Disabled ($1.0\times$ all layers) | Disabled ($1.0\times$ all layers) | **Enabled** (Inverted: 0.2x/0.5x/1.0x) |
| — *Bottom Layers (0 & 1)* | $1.0\times \eta$ | $1.0\times \eta$ | **$0.2\times \eta$** (`bottom_layers: 0.2`) |
| — *Top Layers (2 & 3)* | $1.0\times \eta$ | $1.0\times \eta$ | **$0.5\times \eta$** (`top_layers: 0.5`) |
| — *Classification Heads* | $1.0\times \eta$ | $1.0\times \eta$ | **$1.0\times \eta$** (`head: 1.0`) |
| **Synthetic Replay Buffer** | None ($0\%$) | $100\%$ synthetic | **Floor Curriculum Replay** |
| — *High LR ($\ge 10^{-4}$)* | — | — | **$15.0\%$ synthetic / $85.0\%$ real** |
| — *Mid LR ($= 10^{-5}$)* | — | — | **$12.5\%$ synthetic / $87.5\%$ real** |
| — *Low LR ($\le 10^{-6}$)* | — | — | **$10.0\%$ floor synthetic / $90.0\%$ real** |
| **Loss Function Reweighting** | `ReweightedLoss(0.5, 10.0)` | Unweighted Cross-Entropy | `ReweightedLoss(0.5, 10.0)` |
| **Checkpoint Selection** | `val_metric = 'loss'` | `val_metric = 'loss'` | `val_metric = 'loss'` |

### 5.2 Mechanics of Inverted Discriminative Learning Rates
During Stage 2 fine-tuning (`confor_head16.py:L493-526`), Conformer parameters are segregated into three learning-rate tiers:
1. **Bottom Conformer Blocks (Layers 0 & 1):** Scaled by $\mathbf{0.2\times}$ ($\eta_{\text{bottom}} = 0.2 \times \eta$). Low-level spectral pattern extractors, harmonic pitch filters, and fundamental frequency tracking acquired from synthetic multi-instrument mixes are preserved with minimal drift.
2. **Top Conformer Blocks (Layers 2 & 3):** Scaled by $\mathbf{0.5\times}$ ($\eta_{\text{top}} = 0.5 \times \eta$). High-level temporal context aggregators adapt moderately to real-world musical phrasing and arrangement styles.
3. **Classification Heads (`chordfor.output_layer`):** Scaled by $\mathbf{1.0\times}$ ($\eta_{\text{head}} = 1.0 \times \eta$). Output projections adapt fully to real recording balances and acoustic mix conventions.

### 5.3 Mechanics of Floor Curriculum Replay
To avoid catastrophic forgetting of rare harmonic extensions while adapting to real acoustic distributions, `MixedReplayDataProvider` (`mir/nn/data_provider.py:L110-142`) injects synthetic training samples dynamically linked to learning rate phases:
$$\rho(\eta) = \begin{cases} 0.150 & \text{if } \eta \ge 10^{-4} \\ 0.125 & \text{if } \eta = 10^{-5} \\ 0.100 & \text{if } \eta \le 10^{-6} \quad (\text{Strict Floor, never drops to } 0\%) \end{cases}$$
(`finetune_real_fold.py:L33-35, L113-119`).

---

## 6. Benchmark Results & Side-by-Side Comparison

### 6.1 Multi-Head Evaluation Summary (11,436,357 Total Evaluated Frames)
Evaluated across all 1,217 test tracks via 5-fold cross-validation with CRF Viterbi decoding ($\tau = 2.0$) (`eval_heads_report.txt` vs. `eval_heads_synth_ft_report.txt`):

| Head / Evaluation Scope | Evaluation Metric | Baseline Model (1,217 Real Only) | Proposed Model (5k Pretrain $\to$ FT) | Absolute Gain | Relative Gain |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Head 0: Full Root/Triad (97)** | Frame Accuracy | $0.7497$ | $0.7281$ | $-0.0216$ | $-2.88\%$ |
| | **Macro Class Accuracy (Recall)** | **$0.4404$** | **$0.4734$** | **$+0.0330$** | **$+7.49\%$** |
| | Macro Precision | $0.3692$ | $0.3602$ | $-0.0090$ | $-2.44\%$ |
| | Macro F1-Score | $0.3854$ | $0.3929$ | $+0.0075$ | $+1.95\%$ |
| **Triad Quality (Isolated 9)** | Frame Accuracy | $0.8045$ | $0.7881$ | $-0.0164$ | $-2.04\%$ |
| | **Macro Class Accuracy (Recall)** | **$0.5089$** | **$0.5470$** | **$+0.0381$** | **$+7.49\%$** |
| | Macro Precision | $0.4303$ | $0.4321$ | $+0.0018$ | $+0.42\%$ |
| | Macro F1-Score | $0.4575$ | $0.4675$ | $+0.0100$ | $+2.19\%$ |
| **Head 1: Bass Pitch Class (13)** | Frame Accuracy | $0.8537$ | $0.8400$ | $-0.0137$ | $-1.60\%$ |
| | Macro Class Accuracy (Recall) | $0.8460$ | $0.8296$ | $-0.0164$ | $-1.94\%$ |
| | Macro Precision | $0.8459$ | $0.8329$ | $-0.0130$ | $-1.54\%$ |
| | Macro F1-Score | $0.8458$ | $0.8311$ | $-0.0147$ | $-1.74\%$ |
| **Head 2: 7th Extension (4)** | Frame Accuracy | $0.7718$ | $0.7440$ | $-0.0278$ | $-3.60\%$ |
| | **Macro Class Accuracy (Recall)** | **$0.6276$** | **$0.6304$** | **$+0.0028$** | **$+0.45\%$** |
| | Macro Precision | $0.5374$ | $0.5394$ | $+0.0020$ | $+0.37\%$ |
| | Macro F1-Score | $0.5721$ | $0.5692$ | $-0.0029$ | $-0.51\%$ |
| **Head 3: 9th Extension (4)** | Frame Accuracy | $0.9152$ | $0.8822$ | $-0.0330$ | $-3.61\%$ |
| | **Macro Class Accuracy (Recall)** | **$0.3639$** | **$0.4440$** | **$+0.0801$** | **$+22.01\%$** |
| | Macro Precision | $0.3318$ | $0.3552$ | $+0.0234$ | $+7.05\%$ |
| | **Macro F1-Score** | **$0.3406$** | **$0.3819$** | **$+0.0413$** | **$+12.13\%$** |
| **Head 4: 11th Extension (3)** | Frame Accuracy | $0.9687$ | $0.9730$ | $+0.0043$ | $+0.44\%$ |
| | **Macro Class Accuracy (Recall)** | **$0.4265$** | **$0.4657$** | **$+0.0392$** | **$+9.19\%$** |
| | Macro Precision | $0.4061$ | $0.4398$ | $+0.0337$ | $+8.30\%$ |
| | **Macro F1-Score** | **$0.4143$** | **$0.4513$** | **$+0.0370$** | **$+8.93\%$** |
| **Head 5: 13th Extension (3)** | Frame Accuracy | $0.9929$ | $0.9935$ | $+0.0006$ | $+0.06\%$ |
| | **Macro Class Accuracy (Recall)** | **$0.3466$** | **$0.3573$** | **$+0.0107$** | **$+3.09\%$** |
| | Macro Precision | $0.3738$ | $0.3808$ | $+0.0070$ | $+1.87\%$ |
| | Macro F1-Score | $0.3521$ | $0.3644$ | $+0.0123$ | $+3.49\%$ |

### 6.2 Rare Harmonic Extension & Alteration Recall Breakdown
Detailed per-class metrics across all underrepresented triads, extensions, and alterations (`eval_heads_report.txt` vs. `eval_heads_synth_ft_report.txt`):

| Harmonic Class | Harmonic Category | Ground-Truth Frame Support | Baseline Recall | Proposed Recall | Absolute Recall Gain | Relative Recall Gain | Proposed Precision | Proposed F1-Score |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`aug`** | Augmented Triad | $23,733$ | $0.4337$ | **$0.6102$** | **$+0.1765$** | **$+40.69\%$** | $0.3338$ | $0.4315$ |
| **`sus2`** | Suspended 2nd Triad | $39,010$ | $0.1732$ | **$0.3025$** | **$+0.1293$** | **$+74.65\%$** | $0.1483$ | $0.1990$ |
| **`b7`** | Minor 7th / Dominant | $2,319,989$ | $0.7031$ | **$0.8090$** | **$+0.1059$** | **$+15.06\%$** | $0.5095$ | $0.6253$ |
| **`9`** | Natural 9th Extension | $559,986$ | $0.3164$ | **$0.5680$** | **$+0.2516$** | **$+79.52\%$** | $0.2656$ | $0.3619$ |
| **`#9`** | Altered Sharp-9th | $64,159$ | $0.0007$ | **$0.0553$** | **$+0.0546$** | **$+7,800.00\%$ ($79\times$)** | $0.0452$ | $0.0497$ |
| **`b9`** | Altered Flat-9th | $14,310$ | $0.1839$ | **$0.2471$** | **$+0.0632$** | **$+34.37\%$** | $0.1419$ | $0.1802$ |
| **`11`** | Natural 11th Extension | $142,562$ | $0.1575$ | **$0.1824$** | **$+0.0249$** | **$+15.81\%$** | $0.1465$ | $0.1625$ |
| **`#11`** | Altered Sharp-11th | $14,824$ | $0.1415$ | **$0.2302$** | **$+0.0887$** | **$+62.69\%$** | $0.1849$ | $0.2050$ |
| **`13`** | Natural 13th Extension | $46,690$ | $0.0335$ | $0.0076$ | $-0.0259$ | $-77.31\%$ | $0.0303$ | $0.0122$ |
| **`b13`** | Altered Flat-13th | $9,325$ | $0.0084$ | **$0.0658$** | **$+0.0574$** | **$+683.33\%$ ($7.8\times$)** | $0.1171$ | $0.0843$ |
| **`5`** | Power Chord | $290,416$ | $0.2876$ | **$0.4191$** | **$+0.1315$** | **$+45.72\%$** | $0.2276$ | $0.2950$ |
| **`dim`** | Diminished Triad | $56,223$ | $0.6561$ | **$0.6797$** | **$+0.0236$** | **$+3.60\%$** | $0.3685$ | $0.4779$ |
| **`sus4`** | Suspended 4th Triad | $252,057$ | $0.5119$ | **$0.5372$** | **$+0.0253$** | **$+4.94\%$** | $0.2671$ | $0.3568$ |
| **`bb7`** | Dim 7th / Major 6th | $185,746$ | $0.3135$ | **$0.3171$** | **$+0.0036$** | **$+1.15\%$** | $0.2094$ | $0.2522$ |
| **`7`** | Major 7th Extension | $406,898$ | $0.6872$ | $0.6562$ | $-0.0310$ | $-4.51\%$ | $0.5184$ | $0.5792$ |

### 6.3 Full MIREX Weighted Chord Symbol Recall (WCSR) Scores (Control vs. Proposed)
Evaluated across all 1,217 songs using canonical `mir_eval.chord` rules (`test_for_all_mirex.py:L34, L105`).
* **Control Model:** Trained only on the 1,217 real songs without synthetic pretraining (`pretrained-benchmark:eval_results.txt`, commit `49c4ec92`).
* **Proposed Model:** Pretrained on 5,000 procedural random-mix synthetic tracks $\to$ fine-tuned on the 1,217 real songs with floor curriculum replay and discriminative learning rates (`random-mix-finetuned-second-benchmark:eval_results.txt`, commit `fa84d36`).

| MIREX Evaluation Metric | mir_eval Evaluation Function | Strict Inversion? | Control Model (Real Only) | Proposed Model (5k Pretrain $\to$ FT) | Difference (Abs / Rel) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Root** | `mir_eval.chord.root` | No | $83.75\%$ | **$82.29\%$** | $-1.46\%$ ($-1.74\%$) |
| **Thirds** | `mir_eval.chord.thirds` | No | $80.49\%$ | **$79.32\%$** | $-1.17\%$ ($-1.45\%$) |
| **MajorMinor (MajMin)** | `mir_eval.chord.majmin` | No | $79.78\%$ | **$77.03\%$** | $-2.75\%$ ($-3.45\%$) |
| **Triads** | `mir_eval.chord.triads` | No | $75.00\%$ | **$72.87\%$** | $-2.13\%$ ($-2.84\%$) |
| **Sevenths** | `mir_eval.chord.sevenths` | No | $66.46\%$ | **$62.46\%$** | $-4.00\%$ ($-6.02\%$) |
| **Tetrads** | `mir_eval.chord.tetrads` | No | $61.61\%$ | **$58.20\%$** | $-3.41\%$ ($-5.53\%$) |
| **MIREX Standard** | `mir_eval.chord.mirex` | No | $81.48\%$ | **$79.58\%$** | $-1.90\%$ ($-2.33\%$) |
| **Thirds (with Bass)** | `mir_eval.chord.thirds_inv` | Yes | $78.23\%$ | **$77.12\%$** | $-1.11\%$ ($-1.42\%$) |
| **MajMin (with Bass)** | `mir_eval.chord.majmin_inv` | Yes | $77.76\%$ | **$75.17\%$** | $-2.59\%$ ($-3.33\%$) |
| **Triads (with Bass)** | `mir_eval.chord.triads_inv` | Yes | $73.14\%$ | **$71.12\%$** | $-2.02\%$ ($-2.76\%$) |
| **Sevenths (with Bass)** | `mir_eval.chord.sevenths_inv` | Yes | $65.11\%$ | **$61.18\%$** | $-3.93\%$ ($-6.04\%$) |
| **Tetrads (with Bass)** | `mir_eval.chord.tetrads_inv` | Yes | $60.37\%$ | **$57.02\%$** | $-3.35\%$ ($-5.55\%$) |

#### Analytical Insight on MIREX vs. Macro Recall Trade-Off:
The MIREX WCSR metric is duration-weighted and dominated by majority diatonic triads (`maj` alone accounts for $44.93\%$ of total real duration, and `min` accounts for $11.95\%$; unextended triads represent $>70\%$ of the corpus). 
* In the Control model, the network heavily defaults to majority `maj` and `min` templates, achieving slightly higher frame-wise weighted overlap on standard pop songs ($81.48\%$ vs. $79.58\%$ MIREX) at the expense of severe blindness to complex chords.
* In the Proposed model, procedural pretraining breaks this majority collapse:
  * **Root+Quality Class-wise Accuracy:** increases from **$2.90\% \to 4.17\%$** (**$+43.8\%$ relative gain**; `eval_detailed_breakdown.txt`).
  * **Quality-Only Class-wise Accuracy:** increases from **$3.20\% \to 4.58\%$** (**$+43.1\%$ relative gain**; `eval_detailed_breakdown.txt`).
  * Under composite Harte label decoding (`eval_detailed_breakdown.txt`), rare chords achieve massive recall increases:
    * `sus2`: $5.64\% \to \mathbf{19.36\%}$ ($+13.72\%$ abs, $\mathbf{3.4\times\text{ boost}}$; F1 $6.83\% \to \mathbf{15.56\%}$)
    * `maj9`: $14.52\% \to \mathbf{43.04\%}$ ($+28.52\%$ abs, $\mathbf{3.0\times\text{ boost}}$; F1 $11.45\% \to \mathbf{19.23\%}$)
    * `min9`: $20.64\% \to \mathbf{49.71\%}$ ($+29.07\%$ abs, $\mathbf{2.4\times\text{ boost}}$; F1 $18.83\% \to \mathbf{20.92\%}$)
    * `9` (dominant): $8.09\% \to \mathbf{19.33\%}$ ($+11.24\%$ abs, $\mathbf{2.4\times\text{ boost}}$; F1 $8.67\% \to \mathbf{12.39\%}$)
    * `sus4(b7,9)`: $36.84\% \to \mathbf{47.53\%}$ ($+10.69\%$ abs)
    * `5` (power): $20.75\% \to \mathbf{31.44\%}$ ($+10.69\%$ abs; F1 $11.65\% \to \mathbf{13.50\%}$)
    * `7(#9)` (altered 9th): $0.00\% \to \mathbf{1.23\%}$ (previously undetected by control)
    * `11`: $0.35\% \to \mathbf{1.67\%}$ ($\mathbf{4.8\times\text{ boost}}$)

---

## 7. Production Deployment & Latency Telemetry (GKE / Triton / TensorRT)

### 7.1 Production Platform Architecture
* **Target Environment:** Google Kubernetes Engine (GKE) (`k8s/triton-deployment.yaml`).
* **Accelerator:** Single NVIDIA L4 GPU (Ada Lovelace architecture, $24\text{ GB}$ GDDR6 memory, Compute Capability `sm_89`, 242 TFLOPs FP16 Tensor Core peak throughput) on Google Cloud `g2-standard-4` machine type.
* **Serving Stack:** NVIDIA Triton Inference Server 24.08 (`tritonserver:24.08-py3`).
* **Inference Engine:** TensorRT 10.x compiled execution plan (`tensorrt_plan`, FP16 precision).
* **Multi-Instance Execution:** $2$ parallel execution instances allocated across independent CUDA streams on GPU 0 (`model_repository/chordformer_trt/config.pbtxt:L43-51`):
  ```protobuf
  instance_group [
    {
      count: 2
      kind: KIND_GPU
      gpus: [ 0 ]
    }
  ]
  ```
  Instance 0 executes Tensor Core matrix multiplications for batch $k$ while Instance 1 concurrently handles Host-to-Device (H2D) PCIe feature copies and batch assembly for batch $k+1$, completely overlapping data transfer latency with compute execution.

### 7.2 5-Fold Fused Ensemble ONNX Optimization
* **Fusion Architecture:** All 5 cross-validation fold models (`chordformer_head16_synth_ft_loss_s0` through `s4`) are fused into a single unified `EnsembleChordFormer` `nn.Module` (`export_ensemble_onnx.py:L38-95`).
* **Input Tensor Shape:** `cqt_input`: $[B, 431, 252]$ (dynamic batch dimension $B$, fixed $T=431\text{ frames} = 10.0\text{ s}$, $F=252\text{ bins}$).
* **Graph Optimizations:**
  * Zero-padding $252 \to 256$ is embedded directly into the model graph (`export_ensemble_onnx.py:L74`).
  * Conformer layers run across parallel streams and are fused into a single TensorRT engine graph, eliminating Triton multi-model scheduling overhead.
  * Internal Softmax and ensembling scale ($1/5$) are computed natively inside the graph (`export_ensemble_onnx.py:L84-95`).
* **ONNX Artifact Properties:**
  * ONNX Opset: $17$ (`export_ensemble_onnx.py:L116`).
  * File size: Exactly **$117.64\text{ MB}$** (`export_ensemble_onnx.py:L142`).
  * Numerical Parity: Maximum absolute error between PyTorch eager execution and ONNX Runtime FP32 across all 6 heads and batch sizes $B \in \{1, 4, 8\}$ is strictly $\le \mathbf{3.81 \times 10^{-6}}$ (`export_ensemble_onnx.py:L177`).

### 7.3 Arithmetic Workload Derivation (FLOPs per 10.0s Audio)
For input $X \in \mathbb{R}^{B \times 431 \times 256}$, each Conformer block executes:
* $\text{FFN1} + \text{FFN2}$: $2 \times (2 \times 431 \times 256 \times 1024 + 2 \times 431 \times 1024 \times 256) \approx 0.904\text{ GFLOPs}$.
* $\text{MHSA}$ (16 heads): Projections ($0.170\text{ GFLOPs}$) + Attention scores ($0.095\text{ GFLOPs}$) + Context ($0.095\text{ GFLOPs}$) + Out-Proj ($0.056\text{ GFLOPs}$) $= 0.416\text{ GFLOPs}$.
* $\text{ConvModule}$ ($K=31$): Pointwise1 ($0.113\text{ GFLOPs}$) + Depthwise ($0.007\text{ GFLOPs}$) + Pointwise2 ($0.056\text{ GFLOPs}$) $= 0.176\text{ GFLOPs}$.
* **Total per Conformer Block:** $1.496\text{ GFLOPs}$.
* **Total Ensemble Workload ($B=1$):**
  $$\text{FLOPs}_{\text{ensemble}}(B=1) = 5\text{ folds} \times (4\text{ blocks} \times 1.496\text{ GFLOPs}) + 0.140\text{ GFLOPs (Heads)} = \mathbf{30.06\text{ GFLOPs}}$$
  $$\text{FLOPs}(B) = B \times 30.06\text{ GFLOPs}$$

### 7.4 Dynamic Batching Telemetry & Throughput Scaling
Benchmarked via `perf_analyzer` over gRPC with $10,000\text{ ms}$ measurement windows (`scripts/run_benchmarks.sh:L19-35`, `benchmarks/baseline.csv`, `benchmarks/tuned_dynamic_batching.csv`):

* **Dynamic Batching Configuration (`config.pbtxt:L26-32`):**
  ```protobuf
  dynamic_batching {
    preferred_batch_size: [ 4, 8, 16 ]
    max_queue_delay_microseconds: 5000
  }
  ```

| Concurrency ($L$) | Throughput (infer/sec) | Throughput Speedup | Server Queue Delay | GPU Compute Input | GPU Compute Infer Latency | GPU Compute Output | Client p50 Latency | Client Avg Latency |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1 (Baseline)** | **$92.52$** | $1.00\times$ | $5.13\text{ ms}$ | $0.11\text{ ms}$ | **$2.83\text{ ms}$** | $0.21\text{ ms}$ | $10.34\text{ ms}$ | **$10.61\text{ ms}$** |
| **5** | **$257.09$** | $2.78\times$ | $5.24\text{ ms}$ | $0.33\text{ ms}$ | **$7.33\text{ ms}$** | $0.28\text{ ms}$ | $17.27\text{ ms}$ | **$19.26\text{ ms}$** |
| **9** | **$325.54$** | $3.52\times$ | $8.24\text{ ms}$ | $0.53\text{ ms}$ | **$9.54\text{ ms}$** | $0.39\text{ ms}$ | $25.71\text{ ms}$ | **$27.45\text{ ms}$** |
| **13 (Tuned Peak)** | **$395.75$** | **$\mathbf{4.28\times}$** | $9.29\text{ ms}$ | $0.77\text{ ms}$ | **$10.93\text{ ms}$** | $0.50\text{ ms}$ | $30.45\text{ ms}$ | **$32.56\text{ ms}$** |

#### Verification of Production Claims:
1. **$4.2\times$ Throughput Gain:**
   $$\text{Speedup} = \frac{395.75\text{ infer/sec}}{92.52\text{ infer/sec}} = \mathbf{4.277\times \ge 4.2\times}$$
2. **Sub-$40\text{ms}$ Compute Latency:**
   * At concurrency 1: $t_{\text{compute}} = \mathbf{2.83\text{ ms}}$ ($2,827\,\mu\text{s}$).
   * At concurrency 13 (peak throughput): $t_{\text{compute}} = \mathbf{10.93\text{ ms}}$ ($10,926\,\mu\text{s}$).
   * Margin of safety: $40.0\text{ ms} - 10.93\text{ ms} = \mathbf{29.07\text{ ms}}$ ($72.7\%$ below the $40\text{ms}$ SLA ceiling).
   * Total client end-to-end average round-trip latency (including gRPC framing, network transport, and 5ms queue coalescing): $\mathbf{32.56\text{ ms}} < 40.0\text{ ms}$.

---

## 8. Day 2A & 2B Generated Artifacts Summary

All quantitative deliverables for Day 2A (LaTeX Tables) and Day 2B (Publication-Grade Figures) have been extracted reproducibly from the source dataset repositories, cross-validated across all 5 evaluation splits ($s_0$--$s_4$), and exported to `paper/tables/`, `paper/figures/`, and `context_vault/raw_metrics/`.

### 8.1 Intermediate Raw Metrics (`context_vault/raw_metrics/`)
1. **`head_metrics_comparison.csv`**: Contains fold-by-fold ($s_0$--$s_4$) and aggregated 5-fold `mean ± std` macro Recall, Precision, and $F_1$ across all 6 structural heads, the 9-class Triad Quality subset, Common Triads (`maj`, `min`), and Rare Triads (`sus2`, `sus4`, `dim`, `aug`).
2. **`mir_eval_wcsr_comparison.csv`**: Contains fold-by-fold ($s_0$--$s_4$) and aggregated 5-fold `mean ± std` Weighted Chord Symbol Recall across all 10 standard MIR evaluation rules (`Root`, `MajMin`, `MajMinInv`, `Triads`, `TriadsInv`, `Sevenths`, `SeventhsInv`, `Tetrads`, `TetradsInv`, and `MIREX`).
3. **`per_class_recall_comparison.csv`**: Ground-truth frame support counts and per-class recall for rare suspended triads, diminished/augmented triads, 7th extensions, and upper alterations.
4. **`chord_distributions_1217_vs_synth.csv`**: Full acoustic duration profile comparing the 1,217 Real Song Corpus (73.72 hours total audio) against the 5,000 Synthetic Song Corpus (460.33 hours total audio), including class proportions and upweighting ratios ($\times$).

---

### 8.2 Deliverable 2A: Auto-Generated LaTeX Tables (`paper/tables/`)

#### Table 1: `paper/tables/tab1_head_metrics.tex`
* **Caption:** Macro Recall and $F_1$-score across the 6 structural classification heads and isolated triad quality subsets over 5-fold cross-validation (mean $\pm$ standard deviation across splits $s_0$--$s_4$, sample std with $K-1=4$ degrees of freedom). Control denotes baseline ChordFormer trained purely on the 1,217 real songs; Experimental denotes pretraining on 5,000 synthetic songs followed by real finetuning. $\Delta = \text{Experimental} - \text{Control}$. Superior values in each pair are **bolded**.
* **Markdown Rendering:**

| Structural Output Head | Control Rec. (%) | Exp. Rec. (%) | $\Delta$ Rec. | Control $F_1$ (%) | Exp. $F_1$ (%) | $\Delta$ $F_1$ |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| *Primary 6-Head Decomposition:* | | | | | | |
| **Root + Triad (97 classes)** | $41.21 \pm 3.12$ | **$\mathbf{42.66 \pm 2.59}$** | $+1.45$ | $36.57 \pm 2.07$ | **$\mathbf{36.70 \pm 1.68}$** | $+0.13$ |
| **Bass Pitch (13 classes)** | **$\mathbf{84.81 \pm 1.73}$** | $82.93 \pm 1.51$ | $-1.88$ | **$\mathbf{84.72 \pm 1.77}$** | $83.01 \pm 1.57$ | $-1.70$ |
| **7th Extension (4 classes)** | **$\mathbf{62.69 \pm 4.27}$** | $62.55 \pm 3.14$ | $-0.14$ | **$\mathbf{57.67 \pm 2.73}$** | $56.99 \pm 1.48$ | $-0.67$ |
| **9th Extension (4 classes)** | $35.33 \pm 2.68$ | **$\mathbf{43.58 \pm 7.54}$** | **$+8.25$** | $34.98 \pm 2.96$ | **$\mathbf{39.01 \pm 6.25}$** | **$+4.03$** |
| **11th Extension (3 classes)** | $38.74 \pm 7.85$ | **$\mathbf{41.71 \pm 8.03}$** | **$+2.97$** | $37.35 \pm 3.60$ | **$\mathbf{42.14 \pm 7.04}$** | **$+4.79$** |
| **13th Extension (3 classes)** | **$\mathbf{35.36 \pm 3.74}$** | $34.26 \pm 1.95$ | $-1.10$ | **$\mathbf{36.09 \pm 4.82}$** | $34.47 \pm 2.47$ | $-1.62$ |
| *Macro Summary:* | | | | | | |
| **Macro Average (6 Heads)** | $49.69 \pm 2.10$ | **$\mathbf{51.28 \pm 1.94}$** | **$+1.59$** | $47.90 \pm 1.69$ | **$\mathbf{48.72 \pm 1.86}$** | **$+0.82$** |
| *Targeted Triad Quality Breakdown:* | | | | | | |
| **Triad Quality (all 9 classes)** | $50.23 \pm 4.53$ | **$\mathbf{53.42 \pm 3.96}$** | **$+3.19$** | $45.52 \pm 3.40$ | **$\mathbf{46.91 \pm 2.57}$** | **$+1.39$** |
| **Common Triads (`maj`, `min`)** | **$\mathbf{84.74 \pm 1.97}$** | $83.08 \pm 1.44$ | $-1.66$ | **$\mathbf{86.99 \pm 1.19}$** | $86.05 \pm 0.85$ | $-0.94$ |
| **Rare Triads (`sus2`, `sus4`, `dim`, `aug`)** | $41.67 \pm 7.30$ | **$\mathbf{48.31 \pm 6.47}$** | **$+6.64$** | $53.11 \pm 8.62$ | **$\mathbf{60.94 \pm 6.26}$** | **$+7.83$** |

---

#### Table 2: `paper/tables/tab2_wcsr_mirex.tex`
* **Caption:** Standard MIR evaluation benchmark: Weighted Chord Symbol Recall (WCSR, %) across 5-fold cross-validation on 1,217 real songs (mean $\pm$ std across splits $s_0$--$s_4$, sample std with $K-1=4$ degrees of freedom). Control: 1,217-song real baseline. Experimental: Synthetic pretraining + real finetuning. Superior values are **bolded**.
* **Markdown Rendering:**

| Evaluation Rule | Control (%) | Experimental (%) | $\Delta$ (pp) |
| :--- | :---: | :---: | :---: |
| **Root Pitch Only** | **$\mathbf{83.77 \pm 1.88}$** | $82.30 \pm 1.58$ | $-1.47$ |
| **Major / Minor** | **$\mathbf{79.78 \pm 2.38}$** | $77.04 \pm 1.71$ | $-2.74$ |
| **Major / Minor + Inversions** | **$\mathbf{77.77 \pm 2.22}$** | $75.17 \pm 1.71$ | $-2.60$ |
| **Triads (Full Quality)** | **$\mathbf{75.01 \pm 2.72}$** | $72.88 \pm 1.97$ | $-2.13$ |
| **Triads + Inversions** | **$\mathbf{73.15 \pm 2.58}$** | $71.13 \pm 1.95$ | $-2.02$ |
| **Sevenths (Standard)** | **$\mathbf{66.46 \pm 2.46}$** | $62.46 \pm 1.73$ | $-4.00$ |
| **Sevenths + Inversions** | **$\mathbf{65.12 \pm 2.42}$** | $61.18 \pm 1.72$ | $-3.94$ |
| **Tetrads (Extended)** | **$\mathbf{61.62 \pm 2.73}$** | $58.20 \pm 1.74$ | $-3.42$ |
| **Tetrads + Inversions** | **$\mathbf{60.38 \pm 2.69}$** | $57.02 \pm 1.75$ | $-3.36$ |
| **MIREX Metric (Overall)** | **$\mathbf{81.49 \pm 2.56}$** | $79.59 \pm 1.85$ | $-1.90$ |

---

### 8.3 Deliverable 2B: Publication-Grade Figures (`paper/figures/`)

1. **Figure 1: `paper/figures/fig1_chord_distribution.pdf` (& `fig1_chord_distribution.png`)**
   * **Subject:** Log-scale class distribution comparing the 1,217 Real Song Corpus (73.72 h) vs. the 5,000 Synthetic Song Corpus (460.33 h).
   * **Visual Structure:** Divided into 3 partitioned semantic brackets: (1) Triad Qualities & Power Chords (`maj`, `min`, `sus4`, `sus2`, `dim`, `aug`, `5`, `1`), (2) 7th Extensions (`7`, `b7`, `bb7`), and (3) Upper & Altered Extensions (`9`, `#9`, `b9`, `11`, `#11`, `13`, `b13`).
   * **Layout Optimization:** Horizontal two-column legend anchored above the plot area (`bbox_to_anchor=(0.5, 1.015)`) with $y$-axis scaled to $300\%$ headroom, completely preventing any collision with the "Upper & Altered Extensions" category banner at $y=95$.
   * **Empirical Upweighting Highlights:**
     * `sus2`: $0.34\%$ (Real) $\to 5.31\%$ (Synth) — $\mathbf{15.55\times}$ upweighting
     * `b9`: $0.13\%$ (Real) $\to 1.54\%$ (Synth) — $\mathbf{12.28\times}$ upweighting
     * `dim`: $0.49\%$ (Real) $\to 4.53\%$ (Synth) — $\mathbf{9.20\times}$ upweighting
     * `aug`: $0.21\%$ (Real) $\to 1.77\%$ (Synth) — $\mathbf{8.50\times}$ upweighting
     * `b13`: $0.08\%$ (Real) $\to 0.68\%$ (Synth) — $\mathbf{8.34\times}$ upweighting
     * `#11`: $0.13\%$ (Real) $\to 0.92\%$ (Synth) — $\mathbf{7.12\times}$ upweighting
   * **Aesthetics:** LaTeX serif font (`Times New Roman`), vector PDF (Type 42), colorblind-safe Slate Navy (`#1f4e78`) vs. Ochre Orange (`#d95f02`), top/right despined.

2. **Figure 2: `paper/figures/fig2_per_class_recall_delta.pdf` (& `fig2_per_class_recall_delta.png`)**
   * **Subject:** Two-panel horizontal evaluation of rare chord recall.
   * **Panel (a):** Side-by-side horizontal bars contrasting Baseline (Real-only) Recall vs. Synthetic Pretrained + Finetuned Recall across rare triads, power chords, 7th extensions, and upper/altered extensions.
   * **Panel (b):** Diverging horizontal delta bars ($\Delta \text{Recall} = \text{Exp} - \text{Base}$) with zero-reference line, highlighting the massive gains from synthetic pretraining:
     * `9` (9th Extension): $\mathbf{+25.16\%}$ ($31.64\% \to 56.80\%$)
     * `aug` (Augmented Triad): $\mathbf{+17.65\%}$ ($43.37\% \to 61.02\%$)
     * `5` (Power Chord): $\mathbf{+13.15\%}$ ($28.76\% \to 41.91\%$)
     * `sus2` (Suspended Triad): $\mathbf{+12.93\%}$ ($17.32\% \to 30.25\%$)
     * `b7` (7th Extension): $\mathbf{+10.59\%}$ ($70.31\% \to 80.90\%$)
     * `#11` (Altered 11th): $\mathbf{+8.87\%}$ ($14.15\% \to 23.02\%$)
     * `b9` (Altered 9th): $\mathbf{+6.32\%}$ ($18.39\% \to 24.71\%$)
     * `b13` (Altered 13th): $\mathbf{+5.74\%}$ ($0.84\% \to 6.58\%$)
     * `#9` (Altered 9th): $\mathbf{+5.46\%}$ ($0.07\% \to 5.53\%$)
     * `sus4` (Suspended Triad): $\mathbf{+2.53\%}$ ($51.19\% \to 53.72\%$)
     * `11` (11th Extension): $\mathbf{+2.49\%}$ ($15.75\% \to 18.24\%$)
     * `dim` (Diminished Triad): $\mathbf{+2.36\%}$ ($65.61\% \to 67.97\%$)
     * `bb7` (6th/bb7 Extension): $\mathbf{+0.36\%}$ ($31.35\% \to 31.71\%$)
     * `7` (Major 7th Extension): $\mathbf{-3.10\%}$ ($68.72\% \to 65.62\%$)
     * `13` (13th Extension): $\mathbf{-2.59\%}$ ($3.35\% \to 0.76\%$)
     * `1` (Root-Only Chord): $\mathbf{-3.61\%}$ ($15.08\% \to 11.47\%$)

