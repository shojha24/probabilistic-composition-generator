# STYLE_GUIDE: Academic Music Information Retrieval (ISMIR) Writing Specification

**Target Venue:** International Society for Music Information Retrieval (ISMIR) Conference & Transactions  
**Paper Scope:** Sections 1 ("Introduction"), 2 ("Related Work"), 3 ("Probabilistic Composition Generator"), 4 ("Experimental Setup"), 5 ("Results & Discussion"), 6 ("Conclusion")  
**Target Architecture:** ChordFormer (Conformer Backbone + 6-Head Structured Decomposition)  
**Primary Dataset Innovation:** Sim-to-Real Pretraining via 5,000-Song Procedural Audio Generation & Random-Mix Augmentation  

---

## Note: A full ISMIR template to defer to is located in context_vault/style_guide

---

## 1. Core Operating Principles

1. **Zero Hallucination / Zero Speculation:**
   * Every single numerical quantity (hyperparameters, layer counts, learning rates, sample rates, bin counts, frame counts, decibel ranges, accuracy and recall percentages) MUST match the verified ground-truth values in `FACTS_GENERATOR.md` and `FACTS_BENCHMARK.md`.
   * Never state "approximately", "standard values", "various instruments", or "around $80\%$". Cite the exact figures (e.g. "$22,050\text{ Hz}$", "$252\text{ CQT bins}$", "$6,123,644\text{ parameters}$", "$79.58\%\text{ MIREX WCSR}$").

2. **Code & Artifact Provenance:**
   * When documenting technical mechanisms or referencing benchmarks in the text, preserve exact code grounding so reviewers can audit the codebase directly.
   * State exact source files, configuration parameters, and script entrypoints (e.g. `confor_head16.py`, `pretrain_synthetic.py`, `finetune_real_fold.py`, `extractors/xhmm_ismir.py`).

3. **Engineering & Scientific Tone:**
   * Maintain the voice of a Staff Research Engineer: objective, technically rigorous, concise, and analytically uncompromising.
   * Focus on causal mechanisms: explain *why* an architectural choice or data augmentation works in terms of signal processing, optimization dynamics, gradient variance, and acoustic representation.
   * Acknowledge trade-offs honestly: explicitly state that oversampling rare chords and penalizing minority class misclassifications introduces false-positive pressure on majority classes, and explain how the curriculum replay floor ($10\%$) and loss calibration ($\alpha=0.25, C_{\max}=4.0$) bound this trade-off.

---

## 2. Formatting & Page Budget Constraints

* **Page Limit:** ISMIR standard **6+N format** (strictly $\le 6$ pages for all narrative text, figures, and tables; references and ethics statements may extend onto page 7+ without page limits).
* **Section Allocation Budget (Target Pages):**
  * **Section 1: Introduction** — $\sim 0.75\text{ pages}$
  * **Section 2: Related Work** — $\sim 0.75\text{ pages}$
  * **Section 3: Probabilistic Composition Generator** — $\sim 1.50\text{ pages}$ (Core contribution: formal sampling distributions, rare-chord oversampling, multi-track symbolic arrangement, FluidSynth rendering, random-mix acoustic augmentation)
  * **Section 4: Experimental Setup** — $\sim 1.25\text{ pages}$ (CQT feature extraction, Conformer 6-head architecture, 1,217-song real corpus splits, multi-task loss formulation, floor curriculum replay, discriminative learning rates)
  * **Section 5: Results & Discussion** — $\sim 1.50\text{ pages}$ (Multi-head recall vs. frame accuracy breakdown, rare chord alteration gains, MIREX WCSR comparison, ablation studies, production deployment latency on GKE/Triton/TensorRT)
  * **Section 6: Conclusion & Future Work** — $\sim 0.25\text{ pages}$
* **Two-Column Layout:** Design all LaTeX equations, figure floats, and tables to fit neatly within either a single column ($3.25\text{ inches} / 82.5\text{ mm}$) or a full-width two-column span ($6.875\text{ inches} / 175\text{ mm}$).

---

## 3. Banned AI Puffery & Stylistic Constraints

To ensure the manuscript reads as an authentic, high-impact scientific contribution written by senior domain experts, avoid generic generative AI boilerplate, ungrounded promotional language, and dramatic narrative transitions.

### 3.1 Banned Words & Recommended Technical Replacements

| Banned AI Trope / Cliché | Why It Is Unacceptable | Mandatory Technical Replacement |
| :--- | :--- | :--- |
| *Delve / Delving* | Pervasive AI marker; vague narrative filler. | "We investigate", "We analyze", "We formalize" |
| *Tapestry / Mosaic* | Metaphorical fluff with zero scientific meaning. | "distribution", "feature space", "acoustic structure" |
| *Testament to / Stands as* | Rhetorical cheerleading without quantitative backing. | "demonstrates", "empirically confirms", "verifies" |
| *Crucial / Pivotal / Paramount* | Overused dramatic hyperbole. | "necessary", "foundational", "critical for convergence" |
| *Game-changing / Revolutionize* | Marketing buzzwords inappropriate for peer review. | "substantially improves", "establishes a new benchmark" |
| *Beacon / Catalyst* | Literary metaphor. | "baseline", "prior", "inductive bias" |
| *Leveraging / Utilizing* | Overused passive filler verbs. | "using", "applying", "parameterizing", "training with" |
| *Cutting-edge / State-of-the-art (SOTA)* | Cliché when used adjectivally without direct citations. | "competitive with recent architectures", or cite specific papers |
| *In essence / At its core* | Colloquial reductionism. | "Specifically", "Formally", "By definition" |
| *A myriad of / Plethora* | Imprecise quantifiers hiding missing empirical numbers. | State the exact count: "12 pitch classes", "14 rare classes" |
| *Unparalleled / Unrivaled* | Superlative claim that invites reviewer rejection. | "achieving a $79\times$ increase in recall ($0.07\% \to 5.53\%$)" |
| *Novel paradigm* | Pretentious self-aggrandizement. | "proposed methodology", "sim-to-real training framework" |
| *Foster / Nurture* | Anthropomorphic framing. | "facilitates", "enables", "promotes gradient flow" |

### 3.2 Grammatical & Phrasing Standards
* **Voice:** Use active voice with first-person plural for author actions ("We generate...", "We train...", "We observe...") and objective passive voice for experimental behavior ("Features were extracted...", "Loss was weighted...").
* **Numbers & Units:** Always place a non-breaking space between numerical values and their physical units (e.g. `$22{,}050\,\text{Hz}$`, `$512\,\text{samples}$`, `$23.22\,\text{ms}$`, `$-80\,\text{dB}$`, `$10.93\,\text{ms}$`).
* **Equations:** Every displayed equation must be punctuated as part of the surrounding sentence (ending with a comma or period as grammatically appropriate). Define every variable immediately following the equation.

---

## 4. Unified Mathematical Notation & LaTeX Macros

All manuscript sections must share a single, mathematically consistent notation schema. Use the following unified macro conventions:

### 4.1 Signal Processing & Audio Features

| Entity | LaTeX Symbol | Code / Empirical Value | Definition |
| :--- | :--- | :--- | :--- |
| Sampling Rate | $f_s$ | $22,050\text{ Hz}$ | Audio temporal sampling frequency |
| Hop Length | $H$ | $512\text{ samples}$ | Step size between consecutive CQT analysis frames |
| Frame Interval | $\Delta t$ | $23.22\text{ ms}$ | Frame duration $\Delta t = H / f_s$ |
| Analysis Window | $N_{\text{win}}$ | $8,192\text{ samples}$ | Temporal FFT window size for hybrid CQT |
| Minimum Frequency | $f_{\min}$ | $23.12\text{ Hz}$ | Center frequency of lowest CQT bin ($\text{F}\sharp_0$) |
| Bins per Octave | $b$ | $36\text{ bins/octave}$ | Frequency resolution ($3\text{ bins/semitone}$) |
| Raw CQT Bins | $F_{\text{raw}}$ | $288\text{ bins}$ | Total frequency bins across 8 extracted octaves |
| Sliced Feature Bins | $F$ | $252\text{ bins}$ | Effective input feature dimension ($\text{C}_1$ to $\text{B}_7$, 7 octaves) |
| Model Input Dimension | $d_{\text{in}}$ | $256\text{ channels}$ | Feature dimension after right-zero padding ($252 + 4$) |
| Training Sequence Length | $T$ | $1,000\text{ frames}$ | Segment duration during supervised training ($23.22\text{ s}$) |
| Batch Size | $B$ | $36\text{ (or } 48\text{)}$ | Mini-batch sample count |

### 4.2 Chord Vocabulary & Structured Heads

| Entity | LaTeX Symbol | Range / Cardinality | Definition |
| :--- | :--- | :--- | :--- |
| Root Pitch Class | $r$ | $\mathbb{Z}_{12} = \{0, \dots, 11\}$ | Absolute chromatic root ($\text{C}=0, \dots, \text{B}=11$) |
| Triad Quality | $q$ | $\{1, \dots, 8\}$ | Triad type: `maj`, `min`, `sus4`, `sus2`, `dim`, `aug`, `5`, `1` |
| Silence / No-Chord | $\text{N}$ | Index $0$ | Absence of harmonic structure |
| Head 0 (Root+Triad) | $\hat{\mathbf{z}}_0 \in \mathbb{R}^{97}$ | $97\text{ classes}$ | Class $0$: $\text{N}$; Classes $1..96$: $1 + 12(q-1) + r$ |
| Bass Pitch Class | $b$ | $\mathbb{Z}_{12} \cup \{-1\}$ | Slash bass note ($-1$ if identical to root or $\text{N}$) |
| Head 1 (Bass) | $\hat{\mathbf{z}}_1 \in \mathbb{R}^{13}$ | $13\text{ classes}$ | Class $0$: $\text{N}$; Classes $1..12$: $b + 1$ |
| Head 2 (7th Extension) | $\hat{\mathbf{z}}_2 \in \mathbb{R}^{4}$ | $4\text{ classes}$ | `none` ($0$), `add_7` ($1$), `add_b7` ($2$), `add_bb7` ($3$) |
| Head 3 (9th Extension) | $\hat{\mathbf{z}}_3 \in \mathbb{R}^{4}$ | $4\text{ classes}$ | `none` ($0$), `add_9` ($1$), `add_#9` ($2$), `add_b9` ($3$) |
| Head 4 (11th Extension) | $\hat{\mathbf{z}}_4 \in \mathbb{R}^{3}$ | $3\text{ classes}$ | `none` ($0$), `add_11` ($1$), `add_#11` ($2$) |
| Head 5 (13th Extension) | $\hat{\mathbf{z}}_5 \in \mathbb{R}^{3}$ | $3\text{ classes}$ | `none` ($0$), `add_13` ($1$), `add_b13` ($2$) |
| Total Output Space | $\hat{\mathbf{z}} \in \mathbb{R}^{124}$ | $124\text{ total logits}$ | Sum of all 6 structured categorical heads |

### 4.3 Architecture & Conformer Backbone

| Entity | LaTeX Symbol | Value | Definition |
| :--- | :--- | :--- | :--- |
| Model Dimension | $d_{\text{model}}$ | $256$ | Hidden representation dimension |
| Conformer Blocks | $N_L$ | $4\text{ layers}$ | Total depth of Conformer encoder |
| Attention Heads | $h$ | $16\text{ heads}$ | Multi-head self-attention parallel heads |
| Head Dimension | $d_k$ | $16$ | Per-head projection dimension $d_k = d_{\text{model}} / h$ |
| Feed-Forward Dimension | $d_{\text{ffn}}$ | $1,024$ | Intermediate expansion dimension ($4\times d_{\text{model}}$) |
| Conv Kernel Size | $K$ | $31\text{ frames}$ | 1D depthwise convolution temporal receptive field ($720\text{ ms}$) |
| Dropout Probability | $p_{\text{drop}}$ | $0.10$ | Module dropout rate |
| Parameter Count | $\Theta$ | $6,123,644$ | Total trainable parameters in forward inference graph |

### 4.4 Optimization, Curriculum & Loss

| Entity | LaTeX Symbol | Value / Formula | Definition |
| :--- | :--- | :--- | :--- |
| Learning Rate | $\eta$ | $10^{-3} \to 10^{-6}$ | Optimizer step size |
| Multi-Task Weights | $\boldsymbol{\lambda}$ | $[1.0, 1.0, 1.0, 1.0, 1.0, 1.0]$ | Relative loss multipliers across 6 heads |
| Reweighting Exponent | $\alpha$ | $0.50\text{ (active)}, 0.25\text{ (calibrated)}$ | Power scaling of inverse class frequency |
| Maximum Weight Clip | $C_{\max}$ | $10.0\text{ (active)}, 4.0\text{ (calibrated)}$ | Ceiling for minority class loss weights |
| Replay Ratio | $\rho(\eta)$ | $15\% \to 12.5\% \to 10\%$ | Synthetic sample injection proportion in curriculum |
| Replay Floor | $\rho_{\min}$ | $10.0\%$ | Minimum bound preventing catastrophic forgetting |
| Discriminative LR | $\mathbf{s}_{\text{lr}}$ | $[0.2, 0.5, 1.0]$ | Multipliers for bottom layers, top layers, and heads |
| CRF Transition Penalty | $\tau$ | $2.0\text{ (heads)}, 30.0\text{ (mirex)}$ | Viterbi cost for state transitions between frames |

---

## 5. Metric Terminology & Canonical Evaluation Definitions

To prevent reviewer confusion between frame-level metrics and chord-symbol evaluations, strictly adhere to the following terminology:

1. **Frame-wise Accuracy (`acc_frame`):**
   $$\text{Acc}_{\text{frame}} = \frac{\sum_{t} \mathbb{I}(\hat{y}_t = y_t)}{\sum_{t} 1}$$
   The proportion of total audio frames correctly classified. Inherently dominated by majority classes (`maj`, unextended triads, dominant 7ths).

2. **Class-wise Macro Accuracy / Macro Recall (`acc_class`):**
   $$\text{Acc}_{\text{class}} = \frac{1}{|C|} \sum_{c \in C} \text{Recall}(c) = \frac{1}{|C|} \sum_{c \in C} \frac{\text{TP}_c}{\text{TP}_c + \text{FN}_c}$$
   The unweighted arithmetic mean of per-class recall. **This is the primary figure of merit** for evaluating rare chord recognition and bias mitigation.

3. **Macro F1-Score:**
   $$\text{F1}_{\text{macro}} = \frac{1}{|C|} \sum_{c \in C} \frac{2 \cdot \text{Precision}_c \cdot \text{Recall}_c}{\text{Precision}_c + \text{Recall}_c}$$
   The harmonic mean of precision and recall averaged uniformly across all classes.

4. **MIREX Weighted Chord Symbol Recall (WCSR):**
   Standard ISMIR/MIREX evaluation metric computed via `mir_eval.chord.weighted_accuracy`:
   $$\text{WCSR} = \frac{\sum_{i} d_i \cdot \mathbb{I}(\text{eval\_func}(\text{ref}_i, \text{est}_i))}{\sum_{i} d_i}$$
   Where $d_i$ is the temporal duration of interval $i$. The paper must report all 12 canonical MIREX categories:
   * **Triad & Dyad Levels:** Root ($82.29\%$), Thirds ($79.32\%$), MajMin ($77.03\%$), Triads ($72.87\%$).
   * **Tetrad Levels:** Sevenths ($62.46\%$), Tetrads ($58.20\%$).
   * **Inversion Levels:** Thirds inv ($77.12\%$), MajMin inv ($75.17\%$), Triads inv ($71.12\%$), Sevenths inv ($61.18\%$), Tetrads inv ($57.02\%$).
   * **Standard Benchmark:** MIREX ($79.58\%$).

---

## 6. Required Paper Tables & LaTeX Blueprints

When drafting Section 4 ("Experimental Setup") and Section 5 ("Results & Discussion"), use the following exact table templates.

### 6.1 Table 1: Multi-Head Benchmark Comparison (Baseline vs. Proposed)

```latex
\begin{table*}[t]
\centering
\small
\caption{Cross-validated evaluation on the 1,217-song real corpus (11,436,357 frames). Proposed model incorporates 5,000-song procedural pretraining with floor curriculum replay ($\rho_{\min}=10\%$) and inverted discriminative learning rates.}
\label{tab:multi_head_results}
\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lcccccc@{}}
\toprule
\textbf{Output Head} & \textbf{Class Count} & \textbf{Metric} & \textbf{Baseline (Real Only)} & \textbf{Proposed (Pretrain $\to$ FT)} & \textbf{Absolute Gain} & \textbf{Relative Gain} \\
\midrule
\multirow{2}{*}{\textbf{Head 0: Root + Triad}} & \multirow{2}{*}{97} & Frame Accuracy & $74.97\%$ & $72.81\%$ & $-2.16\%$ & $-2.88\%$ \\
 & & \textbf{Macro Recall} & $\mathbf{44.04\%}$ & $\mathbf{47.34\%}$ & $\mathbf{+3.30\%}$ & $\mathbf{+7.49\%}$ \\
\midrule
\multirow{2}{*}{\textbf{Triad Quality (Isolated)}} & \multirow{2}{*}{9} & Frame Accuracy & $80.45\%$ & $78.81\%$ & $-1.64\%$ & $-2.04\%$ \\
 & & \textbf{Macro Recall} & $\mathbf{50.89\%}$ & $\mathbf{54.70\%}$ & $\mathbf{+3.81\%}$ & $\mathbf{+7.49\%}$ \\
\midrule
\multirow{2}{*}{\textbf{Head 1: Bass Pitch}} & \multirow{2}{*}{13} & Frame Accuracy & $85.37\%$ & $84.00\%$ & $-1.37\%$ & $-1.60\%$ \\
 & & Macro Recall & $84.60\%$ & $82.96\%$ & $-1.64\%$ & $-1.94\%$ \\
\midrule
\multirow{2}{*}{\textbf{Head 2: 7th Extension}} & \multirow{2}{*}{4} & Frame Accuracy & $77.18\%$ & $74.40\%$ & $-2.78\%$ & $-3.60\%$ \\
 & & \textbf{Macro Recall} & $\mathbf{62.76\%}$ & $\mathbf{63.04\%}$ & $\mathbf{+0.28\%}$ & $\mathbf{+0.45\%}$ \\
\midrule
\multirow{2}{*}{\textbf{Head 3: 9th Extension}} & \multirow{2}{*}{4} & Frame Accuracy & $91.52\%$ & $88.22\%$ & $-3.30\%$ & $-3.61\%$ \\
 & & \textbf{Macro Recall} & $\mathbf{36.39\%}$ & $\mathbf{44.40\%}$ & $\mathbf{+8.01\%}$ & $\mathbf{+22.01\%}$ \\
\midrule
\multirow{2}{*}{\textbf{Head 4: 11th Extension}} & \multirow{2}{*}{3} & Frame Accuracy & $96.87\%$ & $97.30\%$ & $+0.43\%$ & $+0.44\%$ \\
 & & \textbf{Macro Recall} & $\mathbf{42.65\%}$ & $\mathbf{46.57\%}$ & $\mathbf{+3.92\%}$ & $\mathbf{+9.19\%}$ \\
\midrule
\multirow{2}{*}{\textbf{Head 5: 13th Extension}} & \multirow{2}{*}{3} & Frame Accuracy & $99.29\%$ & $99.35\%$ & $+0.06\%$ & $+0.06\%$ \\
 & & \textbf{Macro Recall} & $\mathbf{34.66\%}$ & $\mathbf{35.73\%}$ & $\mathbf{+1.07\%}$ & $\mathbf{+3.09\%}$ \\
\bottomrule
\end{tabular*}
\end{table*}
```

### 6.2 Table 2: Underrepresented Chord Recall Breakdown

```latex
\begin{table}[t]
\centering
\small
\caption{Per-class recall on rare triads, upper extensions, and alterations across all 11.4M evaluated real frames.}
\label{tab:rare_chord_recall}
\begin{tabular*}{\columnwidth}{@{\extracolsep{\fill}}lccccc@{}}
\toprule
\textbf{Class} & \textbf{Category} & \textbf{Support} & \textbf{Baseline} & \textbf{Proposed} & \textbf{Gain (Abs / Rel)} \\
\midrule
\texttt{aug} & Triad & 23,733 & $43.37\%$ & $\mathbf{61.02\%}$ & $+17.65\%$ ($+40.7\%$) \\
\texttt{sus2} & Triad & 39,010 & $17.32\%$ & $\mathbf{30.25\%}$ & $+12.93\%$ ($+74.7\%$) \\
\texttt{5} & Power & 290,416 & $28.76\%$ & $\mathbf{41.91\%}$ & $+13.15\%$ ($+45.7\%$) \\
\texttt{sus4} & Triad & 252,057 & $51.19\%$ & $\mathbf{53.72\%}$ & $+2.53\%$ ($+4.9\%$) \\
\texttt{dim} & Triad & 56,223 & $65.61\%$ & $\mathbf{67.97\%}$ & $+2.36\%$ ($+3.6\%$) \\
\midrule
\texttt{b7} & 7th & 2,319,989 & $70.31\%$ & $\mathbf{80.90\%}$ & $+10.59\%$ ($+15.1\%$) \\
\texttt{bb7} & 7th / 6th & 185,746 & $31.35\%$ & $\mathbf{31.71\%}$ & $+0.36\%$ ($+1.2\%$) \\
\midrule
\texttt{9} & 9th & 559,986 & $31.64\%$ & $\mathbf{56.80\%}$ & $+25.16\%$ ($+79.5\%$) \\
\texttt{\#9} & Alt 9th & 64,159 & $0.07\%$ & $\mathbf{5.53\%}$ & $+5.46\%$ ($79\times$) \\
\texttt{b9} & Alt 9th & 14,310 & $18.39\%$ & $\mathbf{24.71\%}$ & $+6.32\%$ ($+34.4\%$) \\
\midrule
\texttt{11} & 11th & 142,562 & $15.75\%$ & $\mathbf{18.24\%}$ & $+2.49\%$ ($+15.8\%$) \\
\texttt{\#11} & Alt 11th & 14,824 & $14.15\%$ & $\mathbf{23.02\%}$ & $+8.87\%$ ($+62.7\%$) \\
\midrule
\texttt{b13} & Alt 13th & 9,325 & $0.84\%$ & $\mathbf{6.58\%}$ & $+5.74\%$ ($7.8\times$) \\
\bottomrule
\end{tabular*}
\end{table}
```

### 6.3 Table 3: Triton & TensorRT Production Deployment Telemetry

```latex
\begin{table}[t]
\centering
\small
\caption{Serving telemetry on Google Cloud GKE (1x NVIDIA L4 GPU, FP16 TensorRT fused 5-fold ensemble, 10.0s audio segment @ 431 frames).}
\label{tab:deployment_telemetry}
\begin{tabular*}{\columnwidth}{@{\extracolsep{\fill}}lcccc@{}}
\toprule
\textbf{Configuration} & \textbf{Concurrency} & \textbf{Throughput} & \textbf{GPU Compute} & \textbf{Client Latency} \\
\midrule
Baseline (Unbatched) & $L = 1$ & $92.52\text{ inf/s}$ & $\mathbf{2.83\text{ ms}}$ & $10.61\text{ ms}$ \\
Dynamic Batching & $L = 5$ & $257.09\text{ inf/s}$ & $7.33\text{ ms}$ & $19.26\text{ ms}$ \\
Dynamic Batching & $L = 9$ & $325.54\text{ inf/s}$ & $9.54\text{ ms}$ & $27.45\text{ ms}$ \\
\textbf{Tuned Batching (Peak)} & $\mathbf{L = 13}$ & $\mathbf{395.75\text{ inf/s}}$ & $\mathbf{10.93\text{ ms}}$ & $\mathbf{32.56\text{ ms}}$ \\
\midrule
\textbf{Measured Speedup} & — & $\mathbf{4.28\times}$ & Sub-$40\text{ms}$ & Sub-$40\text{ms}$ \\
\bottomrule
\end{tabular*}
\end{table}
```

### 6.4 Table 4: MIREX Weighted Chord Symbol Recall (Control vs. Proposed)

```latex
\begin{table}[t]
\centering
\small
\caption{Duration-weighted chord symbol recall (WCSR) on the 1,217-song real corpus evaluated using canonical \texttt{mir\_eval.chord} definitions. Control model is trained purely on the real corpus (\texttt{pretrained-benchmark}); Proposed model incorporates 5,000-song procedural pretraining with floor curriculum replay.}
\label{tab:mirex_wcsr}
\begin{tabular*}{\columnwidth}{@{\extracolsep{\fill}}lcccc@{}}
\toprule
\textbf{Evaluation Rule} & \textbf{Inversion?} & \textbf{Control (\%)} & \textbf{Proposed (\%)} & \textbf{$\Delta$ (Abs / Rel)} \\
\midrule
\texttt{root} & No & $83.75\%$ & $82.29\%$ & $-1.46\%$ ($-1.74\%$) \\
\texttt{thirds} & No & $80.49\%$ & $79.32\%$ & $-1.17\%$ ($-1.45\%$) \\
\texttt{majmin} & No & $79.78\%$ & $77.03\%$ & $-2.75\%$ ($-3.45\%$) \\
\texttt{triads} & No & $75.00\%$ & $72.87\%$ & $-2.13\%$ ($-2.84\%$) \\
\texttt{sevenths} & No & $66.46\%$ & $62.46\%$ & $-4.00\%$ ($-6.02\%$) \\
\texttt{tetrads} & No & $61.61\%$ & $58.20\%$ & $-3.41\%$ ($-5.53\%$) \\
\midrule
\textbf{MIREX Standard} & No & $\mathbf{81.48\%}$ & $\mathbf{79.58\%}$ & $-1.90\%$ ($-2.33\%$) \\
\midrule
\texttt{thirds\_inv} & Yes & $78.23\%$ & $77.12\%$ & $-1.11\%$ ($-1.42\%$) \\
\texttt{majmin\_inv} & Yes & $77.76\%$ & $75.17\%$ & $-2.59\%$ ($-3.33\%$) \\
\texttt{triads\_inv} & Yes & $73.14\%$ & $71.12\%$ & $-2.02\%$ ($-2.76\%$) \\
\texttt{sevenths\_inv} & Yes & $65.11\%$ & $61.18\%$ & $-3.93\%$ ($-6.04\%$) \\
\texttt{tetrads\_inv} & Yes & $60.37\%$ & $57.02\%$ & $-3.35\%$ ($-5.55\%$) \\
\bottomrule
\end{tabular*}
\end{table}
```

---

## 7. Mandatory Literature Citations (BibTeX Keys)

When citing foundational MIR and deep learning literature in the paper, use the following standardized BibTeX keys:

* **Conformer Backbone:** `gulati2020conformer` (*Conformer: Convolution-augmented Transformer for Speech Recognition*, Interspeech 2020).
* **CQT & Librosa:** `mcfee2015librosa` (*librosa: Audio and Music Signal Analysis in Python*, SciPy 2015).
* **Chord Representation & Harte Syntax:** `harte2005symbolic` (*Symbolic Representation of Musical Chords: A Proposed Syntax for Text Annotations*, ISMIR 2005).
* **Evaluation Framework (`mir_eval`):** `raffel2014mireval` (*mir_eval: A Transparent Implementation of Common MIR Metrics*, ISMIR 2014).
* **McGill Billboard Dataset:** `burgoyne2011expert` (*An Expert Ground Truth Set for Audio Chord Recognition and Music Analysis*, ISMIR 2011).
* **Isophonics Beatles/Queen Dataset:** `mauch2010approximate` (*Approximate Note Transcription for the Improved Identification of Difficult Chords*, ISMIR 2010).
* **SpecAugment:** `park2019specaugment` (*SpecAugment: A Simple Data Augmentation Method for Automatic Speech Recognition*, Interspeech 2019).
* **Dynamic Batching & Triton:** `nvidia2024triton` (*NVIDIA Triton Inference Server Architecture and Dynamic Batching Guide*, 2024).
