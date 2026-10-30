#!/usr/bin/env python3
"""
scripts/extract_all_metrics.py

Exhaustive, reproducible metric extraction across:
1. 5-fold cross-validation Head Metrics (Recall, Precision, F1 for 6 heads + triad quality + rare/common breakdown)
2. 5-fold cross-validation MIREX / WCSR Metrics (mir_eval: Root, MajMin, MajMinInv, Triads, TriadsInv, Sevenths, SeventhsInv, Tetrads, TetradsInv, MIREX)
3. Per-class recall across rare chords and extensions
4. Ground-truth frame & duration distributions across 1,217 Real songs vs 5,000 Synthetic songs

Outputs saved to:
- context_vault/raw_metrics/head_metrics_comparison.csv
- context_vault/raw_metrics/mir_eval_wcsr_comparison.csv
- context_vault/raw_metrics/per_class_recall_comparison.csv
- context_vault/raw_metrics/chord_distributions_1217_vs_synth.csv
"""

import os
import sys
import glob
import time
import numpy as np

# Ensure numpy compatibility with mir_eval
if not hasattr(np, 'int'):
    np.int = int
if not hasattr(np, 'float'):
    np.float = float

import pandas as pd
import mir_eval
from sklearn.metrics import recall_score, precision_score, f1_score, accuracy_score

# Add benchmarking repo to path and chdir so relative file opens succeed
BENCH_DIR = '/home/shojha/ChordFormer-Artificial-Dataset-Benchmarking'
if BENCH_DIR not in sys.path:
    sys.path.insert(0, BENCH_DIR)

ORIG_CWD = os.getcwd()
os.chdir(BENCH_DIR)

from train_eval_test_split import get_test_set_ids
from complex_chord import Chord
from mir.nn.data_storage import FramedH5DataStorage
from settings import DEFAULT_SR, DEFAULT_HOP_LENGTH, JAM_DATASET_PATH

FPS = DEFAULT_SR / DEFAULT_HOP_LENGTH
GT_LAB_DIR = os.path.join(JAM_DATASET_PATH, 'chordlab')
CTRL_LAB_DIR = os.path.join(BENCH_DIR, 'output/output_chordformer_head16(0.5,10.0)_s%d.best_hmm_full/jam')
EXP_LAB_DIR = os.path.join(BENCH_DIR, 'output/output_chordformer_head16_synth_ft_loss_s%d.best_hmm_full/jam')
SYNTH_LAB_DIR = os.path.join(BENCH_DIR, 'data/synth_naturalistic_lab')

RAW_METRICS_DIR = '/home/shojha/probabilistic-composition-generator/context_vault/raw_metrics'
os.makedirs(RAW_METRICS_DIR, exist_ok=True)
os.makedirs(os.path.join(BENCH_DIR, 'context_vault/raw_metrics'), exist_ok=True)

# Load song names
with open(os.path.join(BENCH_DIR, 'data/all_1217.csv')) as f:
    ALL_SONG_NAMES = [line.strip() for line in f.readlines()]


# ==============================================================================
# 1. EXTRACT 5-FOLD MIREX / WCSR METRICS
# ==============================================================================
def evaluate_mirex_wcsr():
    print("=" * 70)
    print("1. Evaluating 5-Fold Cross-Validation MIREX / WCSR Metrics...")
    print("=" * 70)

    eval_funcs = [
        ('Root', mir_eval.chord.root),
        ('MajMin', mir_eval.chord.majmin),
        ('MajMinInv', mir_eval.chord.majmin_inv),
        ('Triads', mir_eval.chord.triads),
        ('TriadsInv', mir_eval.chord.triads_inv),
        ('Sevenths', mir_eval.chord.sevenths),
        ('SeventhsInv', mir_eval.chord.sevenths_inv),
        ('Tetrads', mir_eval.chord.tetrads),
        ('TetradsInv', mir_eval.chord.tetrads_inv),
        ('MIREX', mir_eval.chord.mirex)
    ]

    records = []

    for name, fn in eval_funcs:
        ctrl_scores = []
        exp_scores = []

        for fold in range(5):
            test_ids = get_test_set_ids(fold)

            def calc_fold(lab_dir_pattern):
                corr = 0.0
                wrong = 0.0
                for sid in test_ids:
                    sname = ALL_SONG_NAMES[sid]
                    gd_p = os.path.join(GT_LAB_DIR, sname + '.lab')
                    est_p = os.path.join(lab_dir_pattern, sname + '.lab')
                    if not os.path.exists(gd_p) or not os.path.exists(est_p):
                        continue
                    try:
                        (gd_intervals, gd_labels) = mir_eval.io.load_labeled_intervals(gd_p)
                        (est_intervals, est_labels) = mir_eval.io.load_labeled_intervals(est_p)
                        if gd_intervals[0][0] < 0:
                            gd_intervals[0][0] = 0
                        est_intervals, est_labels = mir_eval.util.adjust_intervals(
                            est_intervals, est_labels, gd_intervals.min(), gd_intervals.max(),
                            start_label='X', end_label='X'
                        )
                        (intervals, gd_labels, est_labels) = mir_eval.util.merge_labeled_intervals(
                            gd_intervals, gd_labels, est_intervals, est_labels
                        )
                        durations = mir_eval.util.intervals_to_durations(intervals)
                        cmp = fn(gd_labels, est_labels)
                        corr += durations[cmp > 0].sum()
                        wrong += durations[cmp == 0].sum()
                    except Exception:
                        continue
                tot = corr + wrong
                return 100.0 * corr / tot if tot > 0 else 0.0

            c_sc = calc_fold(CTRL_LAB_DIR)
            e_sc = calc_fold(EXP_LAB_DIR)
            ctrl_scores.append(c_sc)
            exp_scores.append(e_sc)

        c_mean, c_std = float(np.mean(ctrl_scores)), float(np.std(ctrl_scores, ddof=1))
        e_mean, e_std = float(np.mean(exp_scores)), float(np.std(exp_scores, ddof=1))
        delta_mean = e_mean - c_mean

        rec = {
            'Category': name,
            'Control_Mean': round(c_mean, 2),
            'Control_Std': round(c_std, 2),
            'Exp_Mean': round(e_mean, 2),
            'Exp_Std': round(e_std, 2),
            'Delta_Mean': round(delta_mean, 2),
            'Control_s0': round(ctrl_scores[0], 2),
            'Control_s1': round(ctrl_scores[1], 2),
            'Control_s2': round(ctrl_scores[2], 2),
            'Control_s3': round(ctrl_scores[3], 2),
            'Control_s4': round(ctrl_scores[4], 2),
            'Exp_s0': round(exp_scores[0], 2),
            'Exp_s1': round(exp_scores[1], 2),
            'Exp_s2': round(exp_scores[2], 2),
            'Exp_s3': round(exp_scores[3], 2),
            'Exp_s4': round(exp_scores[4], 2),
        }
        records.append(rec)
        print(f"{name:<12} | Control: {c_mean:.2f} ± {c_std:.2f}% | Exp: {e_mean:.2f} ± {e_std:.2f}% | Delta: {delta_mean:+.2f}%")

    df_mirex = pd.DataFrame(records)
    out_csv = os.path.join(RAW_METRICS_DIR, 'mir_eval_wcsr_comparison.csv')
    df_mirex.to_csv(out_csv, index=False)
    df_mirex.to_csv(os.path.join(BENCH_DIR, 'context_vault/raw_metrics/mir_eval_wcsr_comparison.csv'), index=False)
    print(f"Saved: {out_csv}\n")
    return df_mirex


# ==============================================================================
# 2. EXTRACT 5-FOLD HEAD METRICS & RARE TRIADS
# ==============================================================================
def evaluate_head_metrics():
    print("=" * 70)
    print("2. Evaluating 5-Fold Cross-Validation 6-Head & Triad Metrics...")
    print("=" * 70)

    # Change cwd temporarily to BENCH_DIR so FramedH5DataStorage finds dataset/
    old_cwd = os.getcwd()
    os.chdir(BENCH_DIR)
    storage_y = FramedH5DataStorage('jams_xchord')
    storage_y.load_meta()
    storage_y.load()
    os.chdir(old_cwd)

    head_keys = [
        'Root+Triad (97 classes)',
        'Triad Quality (9 classes)',
        'Bass (13 classes)',
        '7th Extension',
        '9th Extension',
        '11th Extension',
        '13th Extension',
        'Common Triads (maj, min)',
        'Rare Triads (sus2, sus4, dim, aug)',
        'Macro Average'
    ]

    fold_data = {
        'control': [{} for _ in range(5)],
        'exp': [{} for _ in range(5)]
    }

    def process_model(lab_dir_pattern, model_key):
        for fold in range(5):
            test_ids = get_test_set_ids(fold)
            all_targets = [[] for _ in range(6)]
            all_preds = [[] for _ in range(6)]
            triad_targets = []
            triad_preds = []

            for sid in test_ids:
                sname = ALL_SONG_NAMES[sid]
                lab_p = os.path.join(lab_dir_pattern, sname + '.lab')
                if not os.path.exists(lab_p):
                    continue

                slen = storage_y.length[sid]
                tag = storage_y.locate(sid, 0, slen)
                n_frames = tag.shape[0]

                pred_heads = np.zeros((n_frames, 6), dtype=np.int64)
                with open(lab_p) as fl:
                    for line in fl:
                        parts = line.strip().split()
                        if len(parts) >= 3:
                            s, e, ch = float(parts[0]), float(parts[1]), parts[2]
                            sf = max(0, int(round(s * FPS)))
                            ef = min(n_frames, int(round(e * FPS)))
                            pred_heads[sf:ef] = Chord(ch).to_numpy()

                p0 = pred_heads[:, 0]
                t0 = tag[:, 0]
                p1 = np.maximum(0, pred_heads[:, 1] + 1)
                t1 = tag[:, 1] + 1
                p2 = np.maximum(0, pred_heads[:, 2])
                t2 = tag[:, 2]
                p3 = np.maximum(0, pred_heads[:, 3])
                t3 = tag[:, 3]
                p4 = np.maximum(0, pred_heads[:, 4])
                t4 = tag[:, 4]
                p5 = np.maximum(0, pred_heads[:, 5])
                t5 = tag[:, 5]

                # Filter valid targets (>= 0)
                pairs = [(t0, p0), (t1, p1), (t2, p2), (t3, p3), (t4, p4), (t5, p5)]
                for h in range(6):
                    th, ph = pairs[h]
                    valid = th >= 0
                    all_targets[h].extend(th[valid])
                    all_preds[h].extend(ph[valid])

                v0 = t0 >= 0
                tq = np.where(t0[v0] <= 0, 0, (t0[v0] - 1) // 12 + 1)
                pq = np.where(p0[v0] <= 0, 0, (p0[v0] - 1) // 12 + 1)
                triad_targets.extend(tq)
                triad_preds.extend(pq)

            # Convert to numpy
            triad_targets = np.array(triad_targets)
            triad_preds = np.array(triad_preds)

            # Compute fold metrics
            metrics = {}

            # Head 0: Root+Triad
            t_h0, p_h0 = np.array(all_targets[0]), np.array(all_preds[0])
            metrics['Root+Triad (97 classes)'] = {
                'recall': recall_score(t_h0, p_h0, average='macro', zero_division=0) * 100.0,
                'precision': precision_score(t_h0, p_h0, average='macro', zero_division=0) * 100.0,
                'f1': f1_score(t_h0, p_h0, average='macro', zero_division=0) * 100.0
            }

            # Triad Quality (9 classes: 0=N, 1=maj, 2=min, 3=sus4, 4=sus2, 5=dim, 6=aug, 7=5, 8=1)
            metrics['Triad Quality (9 classes)'] = {
                'recall': recall_score(triad_targets, triad_preds, average='macro', zero_division=0) * 100.0,
                'precision': precision_score(triad_targets, triad_preds, average='macro', zero_division=0) * 100.0,
                'f1': f1_score(triad_targets, triad_preds, average='macro', zero_division=0) * 100.0
            }

            # Heads 1..5: Bass, 7th, 9th, 11th, 13th
            head_labels = ['Bass (13 classes)', '7th Extension', '9th Extension', '11th Extension', '13th Extension']
            for h_idx, h_name in enumerate(head_labels, start=1):
                th, ph = np.array(all_targets[h_idx]), np.array(all_preds[h_idx])
                metrics[h_name] = {
                    'recall': recall_score(th, ph, average='macro', zero_division=0) * 100.0,
                    'precision': precision_score(th, ph, average='macro', zero_division=0) * 100.0,
                    'f1': f1_score(th, ph, average='macro', zero_division=0) * 100.0
                }

            # Common Triads: maj (1), min (2)
            common_mask = np.isin(triad_targets, [1, 2])
            metrics['Common Triads (maj, min)'] = {
                'recall': recall_score(triad_targets[common_mask], triad_preds[common_mask], labels=[1, 2], average='macro', zero_division=0) * 100.0,
                'precision': precision_score(triad_targets[common_mask], triad_preds[common_mask], labels=[1, 2], average='macro', zero_division=0) * 100.0,
                'f1': f1_score(triad_targets[common_mask], triad_preds[common_mask], labels=[1, 2], average='macro', zero_division=0) * 100.0
            }

            # Rare Triads: sus4 (3), sus2 (4), dim (5), aug (6)
            rare_mask = np.isin(triad_targets, [3, 4, 5, 6])
            metrics['Rare Triads (sus2, sus4, dim, aug)'] = {
                'recall': recall_score(triad_targets[rare_mask], triad_preds[rare_mask], labels=[3, 4, 5, 6], average='macro', zero_division=0) * 100.0,
                'precision': precision_score(triad_targets[rare_mask], triad_preds[rare_mask], labels=[3, 4, 5, 6], average='macro', zero_division=0) * 100.0,
                'f1': f1_score(triad_targets[rare_mask], triad_preds[rare_mask], labels=[3, 4, 5, 6], average='macro', zero_division=0) * 100.0
            }

            # Macro Average across the 6 standard heads
            six_heads = ['Root+Triad (97 classes)', 'Bass (13 classes)', '7th Extension', '9th Extension', '11th Extension', '13th Extension']
            metrics['Macro Average'] = {
                'recall': float(np.mean([metrics[h]['recall'] for h in six_heads])),
                'precision': float(np.mean([metrics[h]['precision'] for h in six_heads])),
                'f1': float(np.mean([metrics[h]['f1'] for h in six_heads]))
            }

            fold_data[model_key][fold] = metrics

    print("Evaluating Control model across 5 folds...")
    process_model(CTRL_LAB_DIR, 'control')
    print("Evaluating Experimental model across 5 folds...")
    process_model(EXP_LAB_DIR, 'exp')

    # Aggregate across 5 folds
    rows = []
    for h in head_keys:
        ctrl_rec_folds = [fold_data['control'][f][h]['recall'] for f in range(5)]
        ctrl_f1_folds = [fold_data['control'][f][h]['f1'] for f in range(5)]
        exp_rec_folds = [fold_data['exp'][f][h]['recall'] for f in range(5)]
        exp_f1_folds = [fold_data['exp'][f][h]['f1'] for f in range(5)]

        c_rec_mean, c_rec_std = float(np.mean(ctrl_rec_folds)), float(np.std(ctrl_rec_folds, ddof=1))
        c_f1_mean, c_f1_std = float(np.mean(ctrl_f1_folds)), float(np.std(ctrl_f1_folds, ddof=1))
        e_rec_mean, e_rec_std = float(np.mean(exp_rec_folds)), float(np.std(exp_rec_folds, ddof=1))
        e_f1_mean, e_f1_std = float(np.mean(exp_f1_folds)), float(np.std(exp_f1_folds, ddof=1))

        delta_rec = e_rec_mean - c_rec_mean
        delta_f1 = e_f1_mean - c_f1_mean

        rows.append({
            'Head': h,
            'Control_Recall_Mean': round(c_rec_mean, 2),
            'Control_Recall_Std': round(c_rec_std, 2),
            'Exp_Recall_Mean': round(e_rec_mean, 2),
            'Exp_Recall_Std': round(e_rec_std, 2),
            'Delta_Recall': round(delta_rec, 2),
            'Control_F1_Mean': round(c_f1_mean, 2),
            'Control_F1_Std': round(c_f1_std, 2),
            'Exp_F1_Mean': round(e_f1_mean, 2),
            'Exp_F1_Std': round(e_f1_std, 2),
            'Delta_F1': round(delta_f1, 2),
            'Ctrl_Rec_s0': round(ctrl_rec_folds[0], 2),
            'Ctrl_Rec_s1': round(ctrl_rec_folds[1], 2),
            'Ctrl_Rec_s2': round(ctrl_rec_folds[2], 2),
            'Ctrl_Rec_s3': round(ctrl_rec_folds[3], 2),
            'Ctrl_Rec_s4': round(ctrl_rec_folds[4], 2),
            'Exp_Rec_s0': round(exp_rec_folds[0], 2),
            'Exp_Rec_s1': round(exp_rec_folds[1], 2),
            'Exp_Rec_s2': round(exp_rec_folds[2], 2),
            'Exp_Rec_s3': round(exp_rec_folds[3], 2),
            'Exp_Rec_s4': round(exp_rec_folds[4], 2),
        })
        print(f"{h:<35} | Rec: {c_rec_mean:.2f} -> {e_rec_mean:.2f} ({delta_rec:+.2f}%) | F1: {c_f1_mean:.2f} -> {e_f1_mean:.2f} ({delta_f1:+.2f}%)")

    df_heads = pd.DataFrame(rows)
    out_csv = os.path.join(RAW_METRICS_DIR, 'head_metrics_comparison.csv')
    df_heads.to_csv(out_csv, index=False)
    df_heads.to_csv(os.path.join(BENCH_DIR, 'context_vault/raw_metrics/head_metrics_comparison.csv'), index=False)
    print(f"Saved: {out_csv}\n")
    return df_heads


# ==============================================================================
# 3. EXTRACT PER-CLASS RECALL FOR RARE EXTENSIONS & ALTERATIONS
# ==============================================================================
def extract_per_class_recall():
    print("=" * 70)
    print("3. Extracting Per-Class Recall across Rare Chords & Alterations...")
    print("=" * 70)

    # Scoped parsing directly from eval_heads_report.txt and eval_heads_synth_ft_report.txt
    # Scoping avoids collision with integer class indices in other heads or confusion matrix counts
    import re

    def parse_report_scoped(report_path):
        with open(report_path) as f:
            content = f.read()
        results = {}
        sections = re.split(r'=== ([^=]+) ===', content)
        for i in range(1, len(sections), 2):
            sec_name = sections[i].strip()
            sec_body = sections[i+1].split('CONFUSION MATRIX')[0]
            for line in sec_body.split('\n'):
                parts = line.strip().split()
                if len(parts) == 5:
                    try:
                        cls_name = parts[0]
                        prec = float(parts[1])
                        rec = float(parts[2])
                        f1 = float(parts[3])
                        sup = int(parts[4])
                        results[(sec_name, cls_name)] = {'prec': prec, 'rec': rec, 'f1': f1, 'sup': sup}
                        if cls_name not in results:
                            results[cls_name] = results[(sec_name, cls_name)]
                    except ValueError:
                        pass
        return results

    base_r = parse_report_scoped(os.path.join(BENCH_DIR, 'eval_heads_report.txt'))
    synth_r = parse_report_scoped(os.path.join(BENCH_DIR, 'eval_heads_synth_ft_report.txt'))

    items = [
        # Triads from Triad Quality section
        ('sus2', 'Suspended Triad', 'Triad Quality (Isolated: maj, min, sus4, sus2, dim, aug, 5, 1)'),
        ('sus4', 'Suspended Triad', 'Triad Quality (Isolated: maj, min, sus4, sus2, dim, aug, 5, 1)'),
        ('dim', 'Diminished Triad', 'Triad Quality (Isolated: maj, min, sus4, sus2, dim, aug, 5, 1)'),
        ('aug', 'Augmented Triad', 'Triad Quality (Isolated: maj, min, sus4, sus2, dim, aug, 5, 1)'),
        ('5', 'Power Chord', 'Triad Quality (Isolated: maj, min, sus4, sus2, dim, aug, 5, 1)'),
        ('1', 'Root-Only Chord', 'Triad Quality (Isolated: maj, min, sus4, sus2, dim, aug, 5, 1)'),
        # 7th extensions
        ('7', '7th Extension', 'Head 3: 7th Extension'),
        ('b7', '7th Extension', 'Head 3: 7th Extension'),
        ('bb7', '6th/bb7 Extension', 'Head 3: 7th Extension'),
        # 9th extensions
        ('9', '9th Extension', 'Head 4: 9th Extension'),
        ('#9', 'Altered 9th', 'Head 4: 9th Extension'),
        ('b9', 'Altered 9th', 'Head 4: 9th Extension'),
        # 11th extensions
        ('11', '11th Extension', 'Head 5: 11th Extension'),
        ('#11', 'Altered 11th', 'Head 5: 11th Extension'),
        # 13th extensions
        ('13', '13th Extension', 'Head 6: 13th Extension'),
        ('b13', 'Altered 13th', 'Head 6: 13th Extension'),
    ]

    records = []
    for cls, cat, sec in items:
        b = base_r.get((sec, cls), base_r.get(cls))
        s = synth_r.get((sec, cls), synth_r.get(cls))
        b_rec = b['rec'] * 100.0
        s_rec = s['rec'] * 100.0
        gain = s_rec - b_rec
        b_f1 = b['f1'] * 100.0
        s_f1 = s['f1'] * 100.0
        gain_f1 = s_f1 - b_f1
        records.append({
            'Class': cls,
            'Category': cat,
            'Support_Frames': b['sup'],
            'Baseline_Recall': round(b_rec, 2),
            'Synth_Recall': round(s_rec, 2),
            'Recall_Gain': round(gain, 2),
            'Baseline_F1': round(b_f1, 2),
            'Synth_F1': round(s_f1, 2),
            'F1_Gain': round(gain_f1, 2)
        })
        print(f"{cls:<8} ({cat:<18}) | Support: {b['sup']:<8,d} | Base: {b_rec:5.2f}% | Synth: {s_rec:5.2f}% | Gain: {gain:+6.2f}%")

    df_recall = pd.DataFrame(records)
    out_csv = os.path.join(RAW_METRICS_DIR, 'per_class_recall_comparison.csv')
    df_recall.to_csv(out_csv, index=False)
    df_recall.to_csv(os.path.join(BENCH_DIR, 'context_vault/raw_metrics/per_class_recall_comparison.csv'), index=False)
    print(f"Saved: {out_csv}\n")
    return df_recall


# ==============================================================================
# 4. EXTRACT CHORD DISTRIBUTIONS: 1,217 REAL VS 5,000 SYNTHETIC
# ==============================================================================
def extract_chord_distributions():
    print("=" * 70)
    print("4. Extracting Corpus Chord Distributions: 1,217 Real vs 5,000 Synthetic...")
    print("=" * 70)

    # Categories to profile
    triad_map = {0: 'N', 1: 'maj', 2: 'min', 3: 'sus4', 4: 'sus2', 5: 'dim', 6: 'aug', 7: '5', 8: '1'}
    sev_map = {1: '7', 2: 'b7', 3: 'bb7'}
    nin_map = {1: '9', 2: '#9', 3: 'b9'}
    ele_map = {1: '11', 2: '#11'}
    thi_map = {1: '13', 2: 'b13'}

    # Track durations (seconds)
    real_durs = {k: 0.0 for k in list(triad_map.values()) + list(sev_map.values()) + list(nin_map.values()) + list(ele_map.values()) + list(thi_map.values())}
    synth_durs = {k: 0.0 for k in real_durs}

    real_tot_dur = 0.0
    synth_tot_dur = 0.0

    # 1. Profile 1,217 Real Songs
    real_files = glob.glob(os.path.join(GT_LAB_DIR, '*.lab'))
    print(f"Parsing {len(real_files)} real .lab files...")
    for p in real_files:
        with open(p) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3:
                    s, e, ch = float(parts[0]), float(parts[1]), parts[2]
                    dur = max(0.0, e - s)
                    real_tot_dur += dur
                    c = Chord(ch)
                    # Triad quality
                    t_idx = 0 if c.triad <= 0 else c.triad
                    real_durs[triad_map[t_idx]] += dur
                    # Extensions
                    if c.seventh in sev_map:
                        real_durs[sev_map[c.seventh]] += dur
                    if c.ninth in nin_map:
                        real_durs[nin_map[c.ninth]] += dur
                    if c.eleventh in ele_map:
                        real_durs[ele_map[c.eleventh]] += dur
                    if c.thirteenth in thi_map:
                        real_durs[thi_map[c.thirteenth]] += dur

    # 2. Profile 5,000 Synthetic Songs
    synth_files = glob.glob(os.path.join(SYNTH_LAB_DIR, 'song_*.lab'))
    print(f"Parsing {len(synth_files)} synthetic .lab files...")
    for p in synth_files:
        with open(p) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3:
                    s, e, ch = float(parts[0]), float(parts[1]), parts[2]
                    dur = max(0.0, e - s)
                    synth_tot_dur += dur
                    c = Chord(ch)
                    # Triad quality
                    t_idx = 0 if c.triad <= 0 else c.triad
                    synth_durs[triad_map[t_idx]] += dur
                    # Extensions
                    if c.seventh in sev_map:
                        synth_durs[sev_map[c.seventh]] += dur
                    if c.ninth in nin_map:
                        synth_durs[nin_map[c.ninth]] += dur
                    if c.eleventh in ele_map:
                        synth_durs[ele_map[c.eleventh]] += dur
                    if c.thirteenth in thi_map:
                        synth_durs[thi_map[c.thirteenth]] += dur

    print(f"Total Real Audio Duration: {real_tot_dur/3600:.2f} hours ({real_tot_dur:.1f} s)")
    print(f"Total Synth Audio Duration: {synth_tot_dur/3600:.2f} hours ({synth_tot_dur:.1f} s)")

    records = []
    classes_ordered = [
        ('Triad Quality', 'maj'),
        ('Triad Quality', 'min'),
        ('Triad Quality', 'sus4'),
        ('Triad Quality', 'sus2'),
        ('Triad Quality', 'dim'),
        ('Triad Quality', 'aug'),
        ('Triad Quality', '5'),
        ('Triad Quality', '1'),
        ('Triad Quality', 'N'),
        ('7th Extension', '7'),
        ('7th Extension', 'b7'),
        ('7th Extension', 'bb7'),
        ('Upper Extension', '9'),
        ('Upper Extension', '#9'),
        ('Upper Extension', 'b9'),
        ('Upper Extension', '11'),
        ('Upper Extension', '#11'),
        ('Upper Extension', '13'),
        ('Upper Extension', 'b13'),
    ]

    for cat, cls in classes_ordered:
        r_sec = real_durs[cls]
        s_sec = synth_durs[cls]
        r_pct = (r_sec / real_tot_dur * 100.0) if real_tot_dur > 0 else 0.0
        s_pct = (s_sec / synth_tot_dur * 100.0) if synth_tot_dur > 0 else 0.0
        ratio = (s_pct / r_pct) if r_pct > 0 else float('inf')

        records.append({
            'Category': cat,
            'Chord_Class': cls,
            'Real_Duration_Sec': round(r_sec, 2),
            'Real_Proportion_Pct': round(r_pct, 4),
            'Synth_Duration_Sec': round(s_sec, 2),
            'Synth_Proportion_Pct': round(s_pct, 4),
            'Ratio_Synth_to_Real': round(ratio, 2)
        })
        print(f"{cat:<16} | {cls:<6} | Real: {r_pct:7.4f}% ({r_sec:9.1f}s) | Synth: {s_pct:7.4f}% ({s_sec:9.1f}s) | Ratio: {ratio:5.2f}x")

    df_dist = pd.DataFrame(records)
    out_csv = os.path.join(RAW_METRICS_DIR, 'chord_distributions_1217_vs_synth.csv')
    df_dist.to_csv(out_csv, index=False)
    df_dist.to_csv(os.path.join(BENCH_DIR, 'context_vault/raw_metrics/chord_distributions_1217_vs_synth.csv'), index=False)
    print(f"Saved: {out_csv}\n")
    return df_dist


if __name__ == '__main__':
    t0 = time.time()
    df_mirex = evaluate_mirex_wcsr()
    df_heads = evaluate_head_metrics()
    df_recall = extract_per_class_recall()
    df_dist = extract_chord_distributions()
    print("=" * 70)
    print(f"ALL METRICS EXTRACTED SUCCESSFULLY in {time.time() - t0:.1f} seconds!")
    print("=" * 70)
