#!/usr/bin/env python3
import re, json
from collections import Counter

NOTE_BASE = {'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}

def note_to_midi(token):
    m = re.search(r"([A-G])([#b]?)(\d+)", token)
    if not m:
        return None
    name, acc, octave = m.group(1), m.group(2), int(m.group(3))
    pc = NOTE_BASE[name]
    if acc == '#': pc = (pc + 1) % 12
    if acc == 'b': pc = (pc - 1) % 12
    midi = (octave + 1) * 12 + pc
    return midi, pc

TENSION_MAP = {'b9':1, '9':2, '#9':3, '11':5, '#11':6, 'b11':4, '13':9, 'b13':8, '#13':10}

def analyze(score_path, labels_dir):
    with open(score_path,'r',encoding='utf-8') as f:
        text = f.read()
    song_blocks = re.split(r"START_SONG_\d+", text)[1:]
    indices = re.findall(r"START_SONG_(\d+)", text)
    totals = Counter()
    for song_idx, block in zip(indices, song_blocks):
        si = int(song_idx)
        label_path = f"{labels_dir}/song_{si}.json"
        labels = None
        try:
            with open(label_path,'r',encoding='utf-8') as lf:
                labels = json.load(lf)
        except Exception:
            labels = None
        tokens = re.split(r"\s+", block.strip())
        chord_tokens = [t for t in tokens if '+' in t]
        for i, tok in enumerate(chord_tokens):
            parts = tok.split('+')
            midis=[]; pcs=[]
            for p in parts:
                nm = note_to_midi(p)
                if nm:
                    midi, pc = nm
                    midis.append(midi); pcs.append(pc)
            if not midis: continue
            totals['chords'] += 1
            n = len(midis)
            totals['num_notes_sum'] += n
            if n >= 6:
                totals['dense_ge_6'] += 1
            pc_counts = Counter(pcs)
            if any(c>1 for c in pc_counts.values()):
                totals['has_pc_doubling'] += 1
            midis_sorted = sorted(midis)
            min_adj = min((b-a) for a,b in zip(midis_sorted, midis_sorted[1:])) if len(midis_sorted)>1 else 999
            if min_adj < 3:
                totals['close_interval_lt3'] += 1
            if min_adj < 2:
                totals['close_interval_lt2'] += 1
            midi_counts = Counter(midis)
            if any(c>1 for c in midi_counts.values()):
                totals['exact_midi_duplicate'] += 1
            if labels and 'chords' in labels and i < len(labels['chords']):
                lab = labels['chords'][i]
                root_name = lab.get('root')
                triad = lab.get('triad')
                if root_name:
                    m = re.match(r"^([A-G])([#b]?)$", root_name)
                    if m:
                        base, acc = m.group(1), m.group(2)
                        root_pc = NOTE_BASE[base]
                        if acc == '#': root_pc = (root_pc+1)%12
                        if acc == 'b': root_pc = (root_pc-1)%12
                        if triad in ('major','minor','diminished','augmented'):
                            third_interval = 4 if triad in ('major','augmented') else 3
                            third_pc = (root_pc + third_interval) % 12
                            if pc_counts.get(third_pc,0) > 1:
                                totals['third_doubled'] += 1
                tensions_present=0; tensions_spelled=0
                for f in ('ninth','eleventh','thirteenth'):
                    val = lab.get(f)
                    if val and val!='N':
                        tensions_present += 1
                        mapped = TENSION_MAP.get(val)
                        if mapped is not None and mapped in pc_counts:
                            tensions_spelled += 1
                if tensions_present>0 and tensions_spelled == tensions_present:
                    totals['all_label_tensions_spelled'] += 1
    chords = totals['chords'] or 1
    report = {
        'total_chords': totals['chords'],
        'avg_notes_per_chord': round(totals['num_notes_sum']/totals['chords'],2),
        'dense_ge_6': int(totals['dense_ge_6']),
        'dense_ge_6_pct': round(100*totals['dense_ge_6']/chords,2),
        'close_lt3': int(totals['close_interval_lt3']),
        'close_lt3_pct': round(100*totals['close_interval_lt3']/chords,2),
        'close_lt2': int(totals['close_interval_lt2']),
        'close_lt2_pct': round(100*totals['close_interval_lt2']/chords,2),
        'pc_doubling': int(totals['has_pc_doubling']),
        'pc_doubling_pct': round(100*totals['has_pc_doubling']/chords,2),
        'exact_midi_dup': int(totals['exact_midi_duplicate']),
        'third_doubled': int(totals['third_doubled']),
        'all_label_tensions_spelled': int(totals['all_label_tensions_spelled']),
    }
    return report

if __name__=='__main__':
    j = analyze('gen/jazz_scores.txt','gen/jazz-labels')
    p = analyze('gen/pop_rock_scores.txt','gen/pop-rock-labels')
    out = {'jazz':j,'pop_rock':p}
    print(json.dumps(out, indent=2))
    with open('tools/metrics_now.json','w',encoding='utf-8') as f:
        json.dump(out,f,indent=2)
