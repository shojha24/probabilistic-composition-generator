#!/usr/bin/env python3
"""
scripts/generate_paper_figures.py

Generates publication-grade vector PDF and PNG figures for ISMIR preprint:
1. paper/figures/fig1_chord_distribution.pdf & .png: Log-scale class distribution comparing 1,217 Real vs 5,000 Synthetic Corpus
2. paper/figures/fig2_per_class_recall_delta.pdf & .png: Two-panel evaluation of rare chord recall and delta gains

Reads directly from:
- context_vault/raw_metrics/chord_distributions_1217_vs_synth.csv
- context_vault/raw_metrics/per_class_recall_comparison.csv
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Global styling for ISMIR academic preprint
plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif', 'Times', 'serif'],
    'mathtext.fontset': 'stix',
    'pdf.fonttype': 42,
    'ps.fonttype': 42,
    'axes.labelsize': 11,
    'axes.titlesize': 12,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.titlesize': 13,
})

FIG_DIR = '/home/shojha/probabilistic-composition-generator/paper/figures'
os.makedirs(FIG_DIR, exist_ok=True)
BENCH_FIG_DIR = '/home/shojha/ChordFormer-Artificial-Dataset-Benchmarking/paper/figures'
os.makedirs(BENCH_FIG_DIR, exist_ok=True)

RAW_METRICS_DIR = '/home/shojha/probabilistic-composition-generator/context_vault/raw_metrics'


def plot_fig1_chord_distribution():
    csv_p = os.path.join(RAW_METRICS_DIR, 'chord_distributions_1217_vs_synth.csv')
    if not os.path.exists(csv_p):
        print(f"Error: {csv_p} not found.")
        return

    df = pd.read_csv(csv_p)
    # Filter out N for cleaner chord-extension comparison
    df = df[df['Chord_Class'] != 'N'].copy()

    # Define groups
    triad_classes = ['maj', 'min', 'sus4', 'sus2', 'dim', 'aug', '5', '1']
    sev_classes = ['7', 'b7', 'bb7']
    upper_classes = ['9', '#9', 'b9', '11', '#11', '13', 'b13']
    all_ordered = triad_classes + sev_classes + upper_classes

    df['order'] = df['Chord_Class'].apply(lambda x: all_ordered.index(x) if x in all_ordered else 99)
    df = df.sort_values('order').reset_index(drop=True)

    x = np.arange(len(df))
    width = 0.38

    fig, ax = plt.subplots(figsize=(13.5, 5.6), dpi=300)

    color_real = '#1f4e78'   # Deep navy
    color_synth = '#d95f02'  # Vibrant ochre orange

    # Log scale requires positive values
    r_pct = np.maximum(df['Real_Proportion_Pct'].values, 0.0005)
    s_pct = np.maximum(df['Synth_Proportion_Pct'].values, 0.0005)

    rects1 = ax.bar(x - width/2, r_pct, width, label='Real Corpus (1,217 Songs, 73.7h)', color=color_real, edgecolor='black', linewidth=0.6, alpha=0.92)
    rects2 = ax.bar(x + width/2, s_pct, width, label='Synthetic Corpus (5,000 Songs, 460.3h)', color=color_synth, edgecolor='black', linewidth=0.6, alpha=0.92)

    ax.set_yscale('log')
    ax.set_ylim(0.0008, 300.0)
    ax.set_ylabel('Duration Proportion (% of Total Audio, Log Scale)', fontweight='bold', fontsize=11)
    ax.set_title('Corpus Structural Head Distribution: Real (1,217 Songs) vs. Synthetic Generator (5,000 Songs)', fontweight='bold', pad=34, fontsize=12.5)
    ax.set_xticks(x)
    ax.set_xticklabels(df['Chord_Class'], fontweight='bold', fontsize=10.5)

    # Annotate upweighting factor for rare / altered chords
    for i, row in df.iterrows():
        ratio = row['Ratio_Synth_to_Real']
        cls = row['Chord_Class']
        if ratio >= 2.0 and row['Synth_Proportion_Pct'] > 0.01:
            y_pos = max(r_pct[i], s_pct[i]) * 1.35
            if y_pos < 55:
                ax.text(x[i] + width/2, y_pos, f"{ratio:.1f}$\\times$", ha='center', va='bottom', fontsize=8.5, fontweight='bold', color='#b30000', rotation=45)

    # Add vertical category divider lines
    div1 = len(triad_classes) - 0.5
    div2 = len(triad_classes) + len(sev_classes) - 0.5
    ax.axvline(div1, color='gray', linestyle='--', linewidth=1.0, alpha=0.7)
    ax.axvline(div2, color='gray', linestyle='--', linewidth=1.0, alpha=0.7)

    # Category bracket labels placed cleanly at y=95 with zero collision
    ax.text((div1)/2, 95, 'Triad Qualities & Power Chords', ha='center', va='center', fontsize=9.5, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#bbbbbb', alpha=0.95))
    ax.text((div1 + div2)/2, 95, '7th Extensions', ha='center', va='center', fontsize=9.5, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#bbbbbb', alpha=0.95))
    ax.text((div2 + len(df))/2, 95, 'Upper & Altered Extensions', ha='center', va='center', fontsize=9.5, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#bbbbbb', alpha=0.95))

    ax.grid(axis='y', which='both', linestyle=':', alpha=0.35, color='gray')

    # Legend placed centered above axes to avoid any overlapping
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.015), ncol=2, frameon=True, framealpha=0.95, edgecolor='#cccccc', fontsize=10.5)

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()

    out_pdf = os.path.join(FIG_DIR, 'fig1_chord_distribution.pdf')
    out_png = os.path.join(FIG_DIR, 'fig1_chord_distribution.png')
    plt.savefig(out_pdf, bbox_inches='tight')
    plt.savefig(out_png, dpi=300, bbox_inches='tight')
    plt.savefig(os.path.join(BENCH_FIG_DIR, 'fig1_chord_distribution.pdf'), bbox_inches='tight')
    plt.savefig(os.path.join(BENCH_FIG_DIR, 'fig1_chord_distribution.png'), dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Generated: {out_pdf} and {out_png}")


def plot_fig2_per_class_recall_delta():
    csv_p = os.path.join(RAW_METRICS_DIR, 'per_class_recall_comparison.csv')
    if not os.path.exists(csv_p):
        print(f"Error: {csv_p} not found.")
        return

    df = pd.read_csv(csv_p)

    # Order rare classes logically: Rare triads, power/lone root, 7th extensions, upper/altered extensions
    target_order = ['sus2', 'sus4', 'dim', 'aug', '5', '1', '7', 'b7', 'bb7', '9', '#9', 'b9', '11', '#11', '13', 'b13']
    df['order'] = df['Class'].apply(lambda x: target_order.index(x) if x in target_order else 99)
    df = df.sort_values('order', ascending=False).reset_index(drop=True)

    y = np.arange(len(df))
    height = 0.38

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.5, 7.2), dpi=300, sharey=True, gridspec_kw={'width_ratios': [1.15, 1.0]})

    color_base = '#2b5c8f'   # Deep blue
    color_synth = '#e66101'  # Orange

    # Panel (a): Grouped horizontal bar chart
    ax1.barh(y + height/2, df['Baseline_Recall'], height, label='Baseline (1,217 Real Only)', color=color_base, edgecolor='black', linewidth=0.5, alpha=0.9)
    ax1.barh(y - height/2, df['Synth_Recall'], height, label='Synth Pretrained + Finetuned', color=color_synth, edgecolor='black', linewidth=0.5, alpha=0.9)

    ax1.set_yticks(y)
    ax1.set_yticklabels(df['Class'], fontweight='bold', fontsize=11)
    ax1.set_xlabel('Frame-wise Recall (%)', fontweight='bold', fontsize=11)
    ax1.set_title('(a) Recall on Rare Chords & Upper Extensions', fontweight='bold', pad=12, fontsize=12)
    ax1.set_xlim(0, 95)
    ax1.grid(axis='x', linestyle=':', alpha=0.4, color='gray')
    ax1.legend(loc='lower right', framealpha=0.95, edgecolor='#cccccc', fontsize=10.5)
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    # Panel (b): Diverging Delta Recall
    deltas = df['Recall_Gain'].values
    bar_colors = ['#2ca25f' if d > 0 else ('#e41a1c' if d < 0 else '#888888') for d in deltas]

    bars = ax2.barh(y, deltas, height * 1.5, color=bar_colors, edgecolor='black', linewidth=0.5, alpha=0.88)
    ax2.axvline(0, color='black', linestyle='-', linewidth=1.1, alpha=0.8)

    # Label deltas on bars
    for i, d in enumerate(deltas):
        offset = 0.6 if d >= 0 else -0.6
        ha = 'left' if d >= 0 else 'right'
        sign_str = f"+{d:.2f}%" if d > 0 else (f"{d:.2f}%" if d < 0 else "0.00%")
        ax2.text(d + offset, y[i], sign_str, va='center', ha=ha, fontsize=8.8, fontweight='bold', color='#111111')

    ax2.set_xlabel('$\\Delta$ Recall (Experimental $-$ Baseline, pp)', fontweight='bold', fontsize=11)
    ax2.set_title('(b) Absolute Recall Gain / Loss ($\\Delta$)', fontweight='bold', pad=12, fontsize=12)
    ax2.set_xlim(-6.5, 30.0)
    ax2.grid(axis='x', linestyle=':', alpha=0.4, color='gray')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)

    plt.tight_layout()

    out_pdf = os.path.join(FIG_DIR, 'fig2_per_class_recall_delta.pdf')
    out_png = os.path.join(FIG_DIR, 'fig2_per_class_recall_delta.png')
    plt.savefig(out_pdf, bbox_inches='tight')
    plt.savefig(out_png, dpi=300, bbox_inches='tight')
    plt.savefig(os.path.join(BENCH_FIG_DIR, 'fig2_per_class_recall_delta.pdf'), bbox_inches='tight')
    plt.savefig(os.path.join(BENCH_FIG_DIR, 'fig2_per_class_recall_delta.png'), dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Generated: {out_pdf} and {out_png}")


if __name__ == '__main__':
    plot_fig1_chord_distribution()
    plot_fig2_per_class_recall_delta()
