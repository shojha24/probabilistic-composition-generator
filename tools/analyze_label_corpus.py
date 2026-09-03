#!/usr/bin/env python3
import json, os, glob
from collections import Counter, defaultdict

def analyze_dir(d):
    files = sorted(glob.glob(os.path.join(d, 'song_*.json')))
    totalsongs = len(files)
    totalchords = 0
    triad = Counter()
    seventh = Counter()
    ninth = Counter()
    eleventh = Counter()
    thirteenth = Counter()
    harte = Counter()
    tensions_per_chord = Counter()
    fingerprint = Counter()
    roots = Counter()
    for fp in files:
        with open(fp,'r',encoding='utf-8') as f:
            j = json.load(f)
        chords = j.get('chords', [])
        totalchords += len(chords)
        for c in chords:
            triad[c.get('triad','')] += 1
            seventh[c.get('seventh','')] += 1
            ninth[c.get('ninth','')] += 1
            eleventh[c.get('eleventh','')] += 1
            thirteenth[c.get('thirteenth','')] += 1
            harte[c.get('harte','')] += 1
            roots[c.get('root','')] += 1
            tcount = 0
            for fkey in ('ninth','eleventh','thirteenth'):
                if c.get(fkey) and c.get(fkey) != 'N':
                    tcount += 1
            tensions_per_chord[tcount] += 1
            # fingerprint: tuple(root, triad, seventh, ninth, eleventh, thirteenth)
            fpkey = (c.get('root'), c.get('triad'), c.get('seventh'), c.get('ninth'), c.get('eleventh'), c.get('thirteenth'))
            fingerprint[fpkey] += 1
    avg_chords = totalchords / totalsongs if totalsongs else 0
    unique_harte = len(harte)
    unique_fingerprints = len(fingerprint)
    top_fp = fingerprint.most_common(10)
    return {
        'path': d,
        'songs': totalsongs,
        'total_chords': totalchords,
        'avg_chords_per_song': round(avg_chords,2),
        'triad_counts': triad.most_common(8),
        'seventh_counts': seventh.most_common(8),
        'tensions_per_chord': sorted(tensions_per_chord.items()),
        'unique_harte': unique_harte,
        'unique_fingerprints': unique_fingerprints,
        'top_fingerprints': [ (list(k), v) for k,v in top_fp],
        'top_roots': roots.most_common(10),
    }

if __name__=='__main__':
    jazz = analyze_dir('gen/jazz-labels')
    pop = analyze_dir('gen/pop-rock-labels')
    out = {'jazz':jazz,'pop_rock':pop}
    with open('tools/labels_report.json','w',encoding='utf-8') as f:
        json.dump(out,f,indent=2)
    print(json.dumps(out, indent=2))
