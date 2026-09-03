#!/usr/bin/env python3
import glob, json, os
from collections import Counter, defaultdict

def analyze_labels(dirpath):
    files = sorted(glob.glob(os.path.join(dirpath, 'song_*.json')))
    total_songs = len(files)
    total_chords = 0
    fingerprint_counts = Counter()  # (root,triad,7,9,11,13)
    norm_fp_counts = Counter()      # (triad,7,9,11,13) ignoring root
    roots_per_norm = defaultdict(set)
    tensions_counter = Counter()
    for fp in files:
        with open(fp,'r',encoding='utf-8') as f:
            j = json.load(f)
        chords = j.get('chords',[])
        total_chords += len(chords)
        for c in chords:
            key = (c.get('root'), c.get('triad'), c.get('seventh'), c.get('ninth'), c.get('eleventh'), c.get('thirteenth'))
            fingerprint_counts[key] += 1
            norm = (c.get('triad'), c.get('seventh'), c.get('ninth'), c.get('eleventh'), c.get('thirteenth'))
            norm_fp_counts[norm] += 1
            roots_per_norm[norm].add(c.get('root'))
            # count number of tensions
            tcount = 0
            for fkey in ('ninth','eleventh','thirteenth'):
                if c.get(fkey) and c.get(fkey) != 'N':
                    tcount += 1
            tensions_counter[tcount] += 1
    return {
        'path': dirpath,
        'songs': total_songs,
        'total_chords': total_chords,
        'unique_fingerprints': len(fingerprint_counts),
        'unique_norm_fps': len(norm_fp_counts),
        'top_fingerprints': fingerprint_counts.most_common(20),
        'top_norm_fps': norm_fp_counts.most_common(20),
        'roots_per_norm_median': sorted((len(v) for v in roots_per_norm.values()))[:20],
        'tensions_per_chord': sorted(tensions_counter.items()),
        'rare_fingerprints': [ (fp,c) for fp,c in fingerprint_counts.items() if c<=5 ][:20],
        'rare_norm_fps': [ (fp,c) for fp,c in norm_fp_counts.items() if c<=10 ][:20]
    }

if __name__=='__main__':
    jazz = analyze_labels('gen/jazz-labels')
    pop = analyze_labels('gen/pop-rock-labels')
    out = {'jazz':jazz,'pop_rock':pop}
    print(json.dumps(out, indent=2))
    with open('tools/fingerprint_norm_report.json','w',encoding='utf-8') as f:
        json.dump(out,f,indent=2)
