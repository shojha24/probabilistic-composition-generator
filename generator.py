"""
generator.py — Single-voice chord generator
============================================
Generates chords and outputs one JFugue Staccato string per song
using the Fixed Octave voicing implementation (PadComposerFixed).
"""

import json
import os
import random
import argparse
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PC_TO_NOTE = {
    0: "C",  1: "Db", 2: "D",  3: "Eb", 4: "E",  5: "F",
    6: "F#", 7: "G",  8: "Ab", 9: "A", 10: "Bb", 11: "B",
}

NOTE_TO_PC = {
    "C": 0,  "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4,  "Fb": 4, "F": 5,  "E#": 5, "F#": 6, "Gb": 6, "G": 7,
    "G#": 8, "Ab": 8, "A": 9,  "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}

TRIAD_TONES = {
    "major":      [0, 4, 7],
    "minor":      [0, 3, 7],
    "diminished": [0, 3, 6],
    "augmented":  [0, 4, 8],
    "sus4":       [0, 5, 7],
    "sus2":       [0, 2, 7],
    "5":          [0, 7],
}

EXT_TO_SEMI = {
    "7": 11, "b7": 10, "bb7": 9, "b9": 1, "#9": 3,
    "9": 2, "11": 5, "#11": 6, "b13": 8, "13": 9,
}

QUALITY_SHORTHAND = {
    ("major",      "N"):   "maj",
    ("major",      "b7"):  "7",
    ("major",      "7"):   "maj7",
    ("major",      "bb7"): "maj",
    ("minor",      "N"):   "min",
    ("minor",      "b7"):  "min7",
    ("minor",      "7"):   "minmaj7",
    ("minor",      "bb7"): "min",
    ("diminished", "N"):   "dim",
    ("diminished", "b7"):  "hdim7",
    ("diminished", "bb7"): "dim7",
    ("diminished", "7"):   "dim",
    ("augmented",  "N"):   "aug",
    ("augmented",  "b7"):  "aug7",
    ("augmented",  "7"):   "aug",
    ("sus4",       "N"):   "sus4",
    ("sus4",       "b7"):  "7sus4",
    ("sus4",       "7"):   "sus4",
    ("sus2",       "N"):   "sus2",
    ("sus2",       "b7"):  "sus2",
    ("5",          "N"):   "5",
    ("5",          "b7"):  "5",
}

VALID_GENRES = ("pop_rock", "jazz", "classical", "folk")

GENRES_TO_TEMPS = {
    "pop_rock": 1.2,
    "jazz":     1.0,
    "classical": 1.0,
    "folk":     1.4,
}

_DEFAULT_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "distributions")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _weighted_sample(prob_dict: dict, rng: random.Random) -> str:
    keys    = list(prob_dict.keys())
    weights = list(prob_dict.values())
    return rng.choices(keys, weights=weights, k=1)[0]


def _build_tone_pool(triad: str, seventh: str, ninth: str,
                     eleventh: str, thirteenth: str) -> list[int]:
    pool = list(TRIAD_TONES.get(triad, [0, 4, 7]))
    for token in (seventh, ninth, eleventh, thirteenth):
        semi = EXT_TO_SEMI.get(token)
        if semi is not None and semi not in pool:
            pool.append(semi)
    return sorted(set(pool))


def _smooth_bass(prev_abs_pc: int, curr_root_pc: int,
                 sampled_semi: int, tone_pool: list[int]) -> int:
    def cost(a, b):
        diff = min(abs(a - b) % 12, 12 - abs(a - b) % 12)
        if diff == 0: return -2.0
        if diff <= 2: return -1.0
        if diff <= 4: return  0.0
        return diff * 0.5

    sampled_abs  = (curr_root_pc + sampled_semi) % 12
    best_semi, best_cost = sampled_semi, cost(prev_abs_pc, sampled_abs)
    if best_cost <= 0:
        return best_semi
    for semi in tone_pool:
        c = cost(prev_abs_pc, (curr_root_pc + semi) % 12)
        if c < best_cost:
            best_cost, best_semi = c, semi
    return best_semi


def _reconstruct_harte(root_name: str, triad: str, bass_name: str,
                        seventh: str, ninth: str,
                        eleventh: str, thirteenth: str) -> str:
    if (triad, seventh) in QUALITY_SHORTHAND:
        base = QUALITY_SHORTHAND[(triad, seventh)]
    else:
        fallback = {"major": "maj", "minor": "min", "diminished": "dim",
                    "augmented": "aug", "sus4": "sus4", "sus2": "sus2", "5": "5"}
        base = fallback.get(triad, "maj")

    exts = [t for t in (ninth, eleventh, thirteenth) if t != "N"]
    result = root_name + ":" + base
    if exts:
        result += "(" + ",".join(exts) + ")"
    if bass_name and bass_name != root_name:
        result += "/" + bass_name
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────────────────────────────────────

class ChordGenerator:
    def __init__(self, genre="pop_rock", tonic_pc=0, num_chords=16,
                 temperature=1.0, seed=None, dist_dir=_DEFAULT_DIST_DIR, bpm=120):
        if genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {genre!r}")

        if isinstance(tonic_pc, str):
            note = tonic_pc[0].upper() + tonic_pc[1:]
            tonic_pc = NOTE_TO_PC.get(note)
            if tonic_pc is None:
                raise ValueError(f"Unrecognised tonic: {tonic_pc!r}")

        self.genre       = genre
        self.tonic_pc    = int(tonic_pc) % 12
        self.num_chords  = num_chords
        self.temperature = float(temperature)
        self.bpm         = bpm
        self.rng         = random.Random(seed)

        self._load_distributions(dist_dir)

    def _load_distributions(self, dist_dir: str) -> None:
        def _load(name):
            path = os.path.join(dist_dir, f"{name}_{self.genre}.json")
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

        try:
            l1  = _load("level1_transitions")
            l2e = _load("level2_extensions")
            l2b = _load("level2_bass")
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Distribution file not found: {e}.")

        self._l1  = l1 ["probabilities"]
        self._l2e = l2e["probabilities"]
        self._l2b = l2b["probabilities"]

        l1_counts = l1["counts"]
        self._state_freq = {st: sum(targets.values()) for st, targets in l1_counts.items()}

    def _tonic_state(self) -> str:
        for candidate in ("0_major", "0_minor"):
            if candidate in self._l1:
                return candidate
        if self._state_freq:
            states  = list(self._state_freq.keys())
            weights = [self._state_freq[s] for s in states]
            return self.rng.choices(states, weights=weights, k=1)[0]
        return "0_major"

    def _apply_temperature(self, prob_dict: dict) -> dict:
        if self.temperature == 1.0:
            return prob_dict
        scaled = {k: v ** (1.0 / self.temperature) for k, v in prob_dict.items()}
        total  = sum(scaled.values())
        return {k: v / total for k, v in scaled.items()}

    def _level1_walk(self) -> list[str]:
        states, current = [], self._tonic_state()
        for _ in range(self.num_chords):
            states.append(current)
            transitions = self._l1.get(current)
            if not transitions:
                current = self._tonic_state()
                continue
            current = _weighted_sample(self._apply_temperature(transitions), self.rng)
        return states

    def generate(self) -> list[dict]:
        seconds_per_beat = 60.0 / self.bpm
        current_time = 0.0

        # Dictionary of tokens to their length in beats
        durations = {"ww": 8.0, "w": 4.0, "h": 2.0}
        dur_keys = list(durations.keys())
        # Weights dictate how common each duration is (e.g., 60% whole notes)
        dur_weights = [0.15, 0.60, 0.25] 

        raw, prev_bass_abs = [], None

        for state in self._level1_walk():
            interval_str, triad = state.rsplit("_", 1)
            root_pc = (int(interval_str) + self.tonic_pc) % 12

            # Pick a duration for this specific chord
            dur_token = self.rng.choices(dur_keys, weights=dur_weights, k=1)[0]
            chord_beats = durations[dur_token]
            chord_len_sec = seconds_per_beat * chord_beats

            # Extensions
            ext_dist = self._l2e.get(state)
            if ext_dist:
                tokens = _weighted_sample(ext_dist, self.rng).strip("()").split(",")
                while len(tokens) < 4: tokens.append("N")
                seventh, ninth, eleventh, thirteenth = tokens[:4]
            else:
                seventh = ninth = eleventh = thirteenth = "N"

            # Bass
            bass_dist = self._l2b.get(state)
            bass_semi = int(_weighted_sample(bass_dist, self.rng)) if bass_dist else 0

            # Voice-leading smooth bass
            tone_pool = _build_tone_pool(triad, seventh, ninth, eleventh, thirteenth)
            if prev_bass_abs is not None:
                bass_semi = _smooth_bass(prev_bass_abs, root_pc, bass_semi, tone_pool)
            bass_abs_pc   = (root_pc + bass_semi) % 12
            prev_bass_abs = bass_abs_pc

            root_name = PC_TO_NOTE[root_pc]
            bass_name = PC_TO_NOTE[bass_abs_pc]
            harte = _reconstruct_harte(root_name, triad, bass_name,
                                       seventh, ninth, eleventh, thirteenth)

            raw.append({
                "time_start":     round(current_time, 4),
                "time_end":       round(current_time + chord_len_sec, 4),
                "duration_token": dur_token,
                "root":           root_name,
                "triad":          triad,
                "bass":           bass_name,
                "seventh":        seventh,
                "ninth":          ninth,
                "eleventh":       eleventh,
                "thirteenth":     thirteenth,
                "harte":          harte,
                "root_pc":        root_pc,
                "bass_abs_pc":    bass_abs_pc,
                "tone_pool":      tone_pool,
            })
            
            # Step time forward by the exact length of this chord
            current_time += chord_len_sec

        return raw

    def to_lab_rows(self, chords: Optional[list[dict]] = None) -> list[str]:
        if chords is None:
            chords = self.generate()
        rows = []
        for c in chords:
            row = "\t".join([
                f"{c['time_start']:.4f}", f"{c['time_end']:.4f}",
                c["root"], c["triad"], c["bass"],
                c["seventh"], c["ninth"], c["eleventh"], c["thirteenth"],
                c["harte"],
            ])
            rows.append(row)
        return rows

    def write_lab(self, path: str, chords: Optional[list[dict]] = None) -> None:
        rows = self.to_lab_rows(chords)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# JFugue helpers
# ─────────────────────────────────────────────────────────────────────────────

def _midi_to_jfugue(midi: int) -> str:
    """Absolute MIDI integer → JFugue 5 note-name string, e.g. 60 → 'C5'."""
    _NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{_NAMES[midi % 12]}{(midi // 12) - 1}"


def _chord_token(midi_notes: list[int], duration: str) -> str:
    if not midi_notes:
        return f"R{duration}"
    return "+".join(f"{_midi_to_jfugue(m)}{duration}" for m in midi_notes)


# ─────────────────────────────────────────────────────────────────────────────
# PadComposerFixed
# ─────────────────────────────────────────────────────────────────────────────

class PadComposerFixed:
    """
    Fixed Octave voicing.

    Anchors the tonic to MIDI 62 (D4) and voices every chord root relative
    to that anchor.  Produces consistent, register-stable output well-suited
    to pad/harmony roles where register wandering is undesirable.
    """

    _DEFAULT_INST = 0

    def __init__(self, chords: list[dict], tonic_pc: int = 0,
                 inst: int = _DEFAULT_INST):
        self.chords   = chords
        self.tonic_pc = tonic_pc
        self.inst     = inst

    def _measure_pad(self, chord: dict) -> str:
        root_pc     = chord["root_pc"]
        bass_pc     = chord["bass_abs_pc"]
        triad_semis = list(TRIAD_TONES.get(chord["triad"], [0, 4, 7]))

        # Anchor the tonic to MIDI 62 (D4), voice root relative to that.
        TARGET_CENTER = 62
        tonic_midi = 60 + self.tonic_pc
        while tonic_midi < TARGET_CENTER - 6:
            tonic_midi += 12
        while tonic_midi > TARGET_CENTER + 6:
            tonic_midi -= 12
        root_midi = tonic_midi + ((root_pc - self.tonic_pc) % 12)

        bass_rel = (bass_pc - root_pc) % 12
        if bass_rel in (1, 11) and len(triad_semis) >= 3:
            triad_semis = [triad_semis[1], triad_semis[2], triad_semis[0] + 12]

        midi_notes = [root_midi + s for s in sorted(set(triad_semis))]

        seventh_semi = EXT_TO_SEMI.get(chord.get("seventh", "N"))
        if seventh_semi is not None:
            seventh_midi = root_midi + seventh_semi
            fifth_midi   = root_midi + triad_semis[-1]
            if seventh_midi <= fifth_midi:
                seventh_midi += 12
            midi_notes.append(seventh_midi)

        for key in ("ninth", "eleventh", "thirteenth"):
            semi = EXT_TO_SEMI.get(chord.get(key, "N"))
            if semi is not None:
                midi_notes.append(root_midi + 12 + semi)

        midi_notes = [m for m in midi_notes if m % 12 != bass_pc]

        if midi_notes:
            lowest_midi = min(midi_notes)
            bass_midi = lowest_midi - 1
            while bass_midi % 12 != bass_pc:
                bass_midi -= 1
            midi_notes.append(bass_midi)
        else:
            midi_notes.append(tonic_midi + bass_pc - self.tonic_pc)

        # Pull the specific duration dynamically set by the generator
        dur = chord.get("duration_token", "w")
        return _chord_token(sorted(set(midi_notes)), dur)

    def render_to_jfugue(self, bpm: int) -> str:
        parts = [f"T{bpm}", "V0", f"I{self.inst}"]
        for chord in self.chords:
            parts.append(self._measure_pad(chord))
        return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="Chord sequence generator")
    p.add_argument("--genre",       default="pop_rock", choices=VALID_GENRES)
    p.add_argument("--tonic",       default="C",
                   help="Tonic note (C, F#, Bb …) or integer 0-11")
    p.add_argument("--num",         type=int, default=48,
                   help="Chords per song")
    p.add_argument("--songs",       type=int, default=5,
                   help="Number of songs to generate")
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--bpm",         type=int, default=120)
    p.add_argument("--seed",        type=int, default=None)
    p.add_argument("--dist-dir",    default=_DEFAULT_DIST_DIR)
    p.add_argument("--out-dir",     default="gen/labels",
                   help="Output directory for .lab files")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    try:
        tonic = int(args.tonic)
    except ValueError:
        tonic = args.tonic

    OUTPUT_TXT_FILE = "gen/generated_scores.txt"
    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(os.path.dirname(OUTPUT_TXT_FILE), exist_ok=True)

    print(f"Starting batch generation of {args.songs} songs...")
    print(f"BPM: {args.bpm} | Genre: {args.genre.upper()}")

    with open(OUTPUT_TXT_FILE, "w") as f:
        for i in range(args.songs):
            print(f"  -> Generating song {i}...")

            gen_tonic = random.randint(0, 11)
            gen_num_chords = random.randint(10, 50)
            gen_bpm = random.randint(60, 180)
            curr_genre = "jazz"
            gen_temperature = GENRES_TO_TEMPS.get(curr_genre, 1.0)

            gen = ChordGenerator(
                genre       = curr_genre, # args.genre,
                tonic_pc    = gen_tonic,
                num_chords  = gen_num_chords, # args.num,
                temperature = gen_temperature,
                bpm         = gen_bpm, # args.bpm,
                seed        = args.seed + i if args.seed is not None else None,
                dist_dir    = args.dist_dir,
            )
            chords = gen.generate()

            # .lab annotation file
            lab_path = os.path.join(args.out_dir, f"SONG_{i}.lab")
            gen.write_lab(lab_path, chords)

            # JFugue Staccato string
            jfugue_str = PadComposerFixed(
                chords, tonic_pc=gen_tonic
            ).render_to_jfugue(bpm=gen_bpm)

            f.write(f"START_SONG_{i}\n")
            f.write(jfugue_str + "\n")
            f.write("END_SONG\n")

    print(f"\nSuccess! Wrote rendering instructions to {OUTPUT_TXT_FILE}")
    print("Ready to run HumanizedMidiRenderer.java.")