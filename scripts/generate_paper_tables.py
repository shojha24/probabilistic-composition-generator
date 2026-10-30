#!/usr/bin/env python3
"""
scripts/generate_paper_tables.py

Generates publication-ready LaTeX tables for ISMIR preprint:
- paper/tables/tab1_head_metrics.tex: 6-Head structured chord estimation performance + triad breakdowns
- paper/tables/tab2_wcsr_mirex.tex: Standard mir_eval WCSR and MIREX evaluation

Reads data directly from:
- context_vault/raw_metrics/head_metrics_comparison.csv
- context_vault/raw_metrics/mir_eval_wcsr_comparison.csv
"""

import os
import pandas as pd

TABLES_DIR = '/home/shojha/probabilistic-composition-generator/paper/tables'
os.makedirs(TABLES_DIR, exist_ok=True)
BENCH_TABLES_DIR = '/home/shojha/ChordFormer-Artificial-Dataset-Benchmarking/paper/tables'
os.makedirs(BENCH_TABLES_DIR, exist_ok=True)

RAW_METRICS_DIR = '/home/shojha/probabilistic-composition-generator/context_vault/raw_metrics'


def format_val_std(val, std, is_bold=False):
    base_str = f"{val:.2f} $\\pm$ {std:.2f}"
    if is_bold:
        return f"\\textbf{{{base_str}}}"
    return base_str


def format_delta(delta):
    if delta > 0:
        return f"+{delta:.2f}"
    elif delta < 0:
        return f"{delta:.2f}"
    else:
        return "0.00"


def generate_table1_head_metrics():
    csv_path = os.path.join(RAW_METRICS_DIR, 'head_metrics_comparison.csv')
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} does not exist.")
        return

    df = pd.read_csv(csv_path)

    # Name mapping for clean LaTeX representation
    latex_name_map = {
        'Root+Triad (97 classes)': 'Root + Triad (97 classes)',
        'Bass (13 classes)': 'Bass Pitch (13 classes)',
        '7th Extension': '7th Extension (4 classes)',
        '9th Extension': '9th Extension (4 classes)',
        '11th Extension': '11th Extension (3 classes)',
        '13th Extension': '13th Extension (3 classes)',
        'Macro Average': 'Macro Average (6 Heads)',
        'Triad Quality (9 classes)': 'Triad Quality (all 9 classes)',
        'Common Triads (maj, min)': 'Common Triads (\\textit{maj}, \\textit{min})',
        'Rare Triads (sus2, sus4, dim, aug)': 'Rare Triads (\\textit{sus2}, \\textit{sus4}, \\textit{dim}, \\textit{aug})'
    }

    primary_heads = [
        'Root+Triad (97 classes)',
        'Bass (13 classes)',
        '7th Extension',
        '9th Extension',
        '11th Extension',
        '13th Extension'
    ]

    breakdown_heads = [
        'Triad Quality (9 classes)',
        'Common Triads (maj, min)',
        'Rare Triads (sus2, sus4, dim, aug)'
    ]

    lines = [
        "% Auto-generated Table 1: Structured 6-Head Decomposition & Rare Triad Performance",
        "\\begin{table*}[t]",
        "\\centering",
        "\\small",
        "\\caption{Macro Recall and $F_1$-score across the 6 structural classification heads and isolated triad quality subsets over 5-fold cross-validation (mean $\\pm$ standard deviation across splits $s_0$--$s_4$). Control denotes baseline ChordFormer trained purely on the 1,217 real songs; Experimental denotes pretraining on 5,000 synthetic songs followed by real finetuning. $\\Delta = \\text{Experimental} - \\text{Control}$. Superior values in each pair are \\textbf{bolded}.}",
        "\\label{tab:head_metrics}",
        "\\begin{tabular}{lcccccc}",
        "\\toprule",
        "\\textbf{Structural Output Head} & \\textbf{Control Rec. (\\%)} & \\textbf{Exp. Rec. (\\%)} & \\textbf{$\\Delta$ Rec.} & \\textbf{Control $F_1$ (\\%)} & \\textbf{Exp. $F_1$ (\\%)} & \\textbf{$\\Delta$ $F_1$} \\\\",
        "\\midrule",
        "\\multicolumn{7}{l}{\\textit{Primary 6-Head Multi-Task Decomposition:}} \\\\"
    ]

    def render_row(h_key):
        row = df[df['Head'] == h_key].iloc[0]
        h_label = latex_name_map.get(h_key, h_key)
        c_rec, c_rec_s = row['Control_Recall_Mean'], row['Control_Recall_Std']
        e_rec, e_rec_s = row['Exp_Recall_Mean'], row['Exp_Recall_Std']
        d_rec = row['Delta_Recall']
        c_f1, c_f1_s = row['Control_F1_Mean'], row['Control_F1_Std']
        e_f1, e_f1_s = row['Exp_F1_Mean'], row['Exp_F1_Std']
        d_f1 = row['Delta_F1']

        bold_rec_e = e_rec > c_rec
        bold_rec_c = c_rec > e_rec
        bold_f1_e = e_f1 > c_f1
        bold_f1_c = c_f1 > e_f1

        str_c_rec = format_val_std(c_rec, c_rec_s, bold_rec_c)
        str_e_rec = format_val_std(e_rec, e_rec_s, bold_rec_e)
        str_d_rec = format_delta(d_rec)

        str_c_f1 = format_val_std(c_f1, c_f1_s, bold_f1_c)
        str_e_f1 = format_val_std(e_f1, e_f1_s, bold_f1_e)
        str_d_f1 = format_delta(d_f1)

        return f"  {h_label:<45} & {str_c_rec} & {str_e_rec} & {str_d_rec} & {str_c_f1} & {str_e_f1} & {str_d_f1} \\\\"

    for h in primary_heads:
        lines.append(render_row(h))

    lines.append("\\midrule")
    lines.append(render_row('Macro Average'))
    lines.append("\\midrule")
    lines.append("\\multicolumn{7}{l}{\\textit{Targeted Triad Quality Breakdown:}} \\\\")

    for h in breakdown_heads:
        lines.append(render_row(h))

    lines.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table*}",
        ""
    ])

    out_tex = os.path.join(TABLES_DIR, 'tab1_head_metrics.tex')
    with open(out_tex, 'w') as f:
        f.write("\n".join(lines))
    with open(os.path.join(BENCH_TABLES_DIR, 'tab1_head_metrics.tex'), 'w') as f:
        f.write("\n".join(lines))
    print(f"Generated: {out_tex}")


def generate_table2_wcsr_mirex():
    csv_path = os.path.join(RAW_METRICS_DIR, 'mir_eval_wcsr_comparison.csv')
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} does not exist.")
        return

    df = pd.read_csv(csv_path)

    latex_cat_map = {
        'Root': 'Root Pitch Only',
        'MajMin': 'Major / Minor',
        'MajMinInv': 'Major / Minor + Inversions',
        'Triads': 'Triads (Full Quality)',
        'TriadsInv': 'Triads + Inversions',
        'Sevenths': 'Sevenths (Standard)',
        'SeventhsInv': 'Sevenths + Inversions',
        'Tetrads': 'Tetrads (Extended)',
        'TetradsInv': 'Tetrads + Inversions',
        'MIREX': '\\textbf{MIREX Metric (Overall)}'
    }

    lines = [
        "% Auto-generated Table 2: Standard mir_eval WCSR and MIREX Benchmark Comparison",
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        "\\caption{Standard MIR evaluation benchmark: Weighted Chord Symbol Recall (WCSR, \\%) across 5-fold cross-validation on 1,217 real songs (mean $\\pm$ std across splits $s_0$--$s_4$). Control: 1,217-song real baseline. Experimental: Synthetic pretraining + real finetuning. Superior values are \\textbf{bolded}.}",
        "\\label{tab:wcsr_mirex}",
        "\\begin{tabular}{lccc}",
        "\\toprule",
        "\\textbf{Evaluation Rule} & \\textbf{Control (\\%)} & \\textbf{Experimental (\\%)} & \\textbf{$\\Delta$ (pp)} \\\\",
        "\\midrule"
    ]

    for _, row in df.iterrows():
        cat = row['Category']
        label = latex_cat_map.get(cat, cat)
        c_m, c_s = row['Control_Mean'], row['Control_Std']
        e_m, e_s = row['Exp_Mean'], row['Exp_Std']
        d_m = row['Delta_Mean']

        bold_e = e_m > c_m
        bold_c = c_m > e_m

        str_c = format_val_std(c_m, c_s, bold_c)
        str_e = format_val_std(e_m, e_s, bold_e)
        str_d = format_delta(d_m)

        if cat == 'MIREX':
            lines.append("\\midrule")
        lines.append(f"  {label:<32} & {str_c} & {str_e} & {str_d} \\\\")

    lines.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
        ""
    ])

    out_tex = os.path.join(TABLES_DIR, 'tab2_wcsr_mirex.tex')
    with open(out_tex, 'w') as f:
        f.write("\n".join(lines))
    with open(os.path.join(BENCH_TABLES_DIR, 'tab2_wcsr_mirex.tex'), 'w') as f:
        f.write("\n".join(lines))
    print(f"Generated: {out_tex}")


if __name__ == '__main__':
    generate_table1_head_metrics()
    generate_table2_wcsr_mirex()
