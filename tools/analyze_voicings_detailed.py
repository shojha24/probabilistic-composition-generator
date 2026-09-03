#!/usr/bin/env python3
import re, json, sys
from collections import Counter, defaultdict

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


def analyze_file(score_path, labels_dir):
    with open(score_path,'r',encoding='utf-8') as f:
        text = f.read()
    indices = re.findall(r"START_SONG_(\d+)", text)
    blocks = re.split(r"START_SONG_\d+", text)[1:]
    results = []
    for song_idx, block in zip(indices, blocks):
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
            midis_sorted = sorted(midis)
            min_adj = min((b-a) for a,b in zip(midis_sorted, midis_sorted[1:])) if len(midis_sorted)>1 else 999
            pc_counts = Counter(pcs)
            midi_counts = Counter(midis)
            dup_pc_count = sum(1 for c in pc_counts.values() if c>1)
            exact_dup = any(c>1 for c in midi_counts.values())
            third_doubled=False
            tensions_present=0; tensions_spelled=0
            root_name=None; triad=None
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
                        third_interval = 4 if triad in ('major','augmented') else 3
                        third_pc = (root_pc + third_interval) % 12
                        if pc_counts.get(third_pc,0) > 1:
                            third_doubled = True
                for f in ('ninth','eleventh','thirteenth'):
                    val = lab.get(f)
                    if val and val!='N':
                        tensions_present += 1
                        mapped = TENSION_MAP.get(val)
                        if mapped is not None and mapped in pc_counts:
                            tensions_spelled += 1
            results.append({
                'song_idx': si,
                'chord_i': i,
                'token': tok,
                'num_notes': len(midis),
                'min_adj': int(min_adj),
                'dup_pc_count': dup_pc_count,
                'exact_dup': exact_dup,
                'third_doubled': third_doubled,
                'tensions_present': tensions_present,
                'tensions_spelled': tensions_spelled,
            })
    return results

if __name__=='__main__':
    jazz = analyze_file('gen/jazz_scores.txt', 'gen/jazz-labels')
    pop = analyze_file('gen/pop_rock_scores.txt', 'gen/pop-rock-labels')
    # sort by min_adj asc, then num_notes desc
    jazz_sorted = sorted(jazz, key=lambda r: (r['min_adj'], -r['num_notes']))
    pop_sorted = sorted(pop, key=lambda r: (r['min_adj'], -r['num_notes']))
    out = {'jazz_top15_tightest': jazz_sorted[:15], 'pop_top15_tightest': pop_sorted[:15], 'jazz_stats_count':len(jazz), 'pop_stats_count':len(pop)}
    with open('tools/voicing_report.json','w',encoding='utf-8') as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
