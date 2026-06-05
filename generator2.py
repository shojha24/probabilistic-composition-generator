"""
generator.py — Phase 2: Synthetic Chord Sequence Generator
===========================================================
Consumes the Phase 1 JSON distributions and produces fully-formed
ChordFormer 10-column .lab annotation sequences in Harte syntax.

Pipeline per generation:
  1. Load genre-conditioned Level 1 / Level 2 distributions
  2. Level 1 Markov walk  → (root_interval, triad) skeleton
  3. Level 2 sampler      → extensions + raw bass interval (root-relative)
  4. Algorithmic Bassist  → voice-leading smoother over dynamic tone pool
  5. Absolute pitch translation + Harte string reconstruction
  6. 10-column .lab row formatting
  7. Multi-Voice JFugue Staccato string compilation

Usage (CLI):
    python generator.py --genre jazz --songs 100 --bpm 130
    python generator.py --genre pop_rock --tonic C --num 16 --seed 42
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
_DEFAULT_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "distributions")


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pc_distance(a: int, b: int) -> int:
    diff = abs(a - b) % 12
    return min(diff, 12 - diff)

def _build_tone_pool(triad: str, seventh: str, ninth: str,
                     eleventh: str, thirteenth: str) -> list[int]:
    pool = list(TRIAD_TONES.get(triad, [0, 4, 7]))
    for token in (seventh, ninth, eleventh, thirteenth):
        semi = EXT_TO_SEMI.get(token)
        if semi is not None and semi not in pool:
            pool.append(semi)
    return sorted(set(pool))

def _bass_cost(prev_abs_pc: int, curr_abs_pc: int) -> float:
    dist = _pc_distance(prev_abs_pc, curr_abs_pc)
    if dist == 0: return -2.0   # common tone
    if dist <= 2: return -1.0   # stepwise
    if dist <= 4: return  0.0   # neutral
    return dist * 0.5           # penalty

def _smooth_bass(prev_abs_pc: int, curr_root_pc: int,
                 sampled_semi: int, tone_pool: list[int]) -> int:
    sampled_abs  = (curr_root_pc + sampled_semi) % 12
    current_cost = _bass_cost(prev_abs_pc, sampled_abs)

    if current_cost <= 0:
        return sampled_semi

    best_semi = sampled_semi
    best_cost = current_cost
    for semi in tone_pool:
        abs_pc = (curr_root_pc + semi) % 12
        cost   = _bass_cost(prev_abs_pc, abs_pc)
        if cost < best_cost:
            best_cost = cost
            best_semi = semi
    return best_semi

def _reconstruct_harte(root_name: str, triad: str, bass_name: str,
                        seventh: str, ninth: str,
                        eleventh: str, thirteenth: str) -> str:
    if (triad, seventh) in QUALITY_SHORTHAND:
        base = QUALITY_SHORTHAND[(triad, seventh)]
    else:
        fallback_map = {
            "major": "maj", "minor": "min", "diminished": "dim",
            "augmented": "aug", "sus4": "sus4", "sus2": "sus2", "5": "5"
        }
        base = fallback_map.get(triad, "maj")
        if seventh not in ("N", "bb7"):
            ninth = seventh + "," + ninth if ninth != "N" else seventh

    exts = [t for t in (ninth, eleventh, thirteenth) if t != "N"]
    result = root_name + ":" + base
    if exts:
        result += "(" + ",".join(exts) + ")"

    if bass_name and bass_name != root_name:
        result += "/" + bass_name

    return result

def _weighted_sample(prob_dict: dict, rng: random.Random) -> str:
    keys    = list(prob_dict.keys())
    weights = list(prob_dict.values())
    return rng.choices(keys, weights=weights, k=1)[0]


# ─────────────────────────────────────────────────────────────────────────────
# Main generator class
# ─────────────────────────────────────────────────────────────────────────────

class ChordGenerator:
    def __init__(self, genre="pop_rock", tonic_pc=0, num_chords=16,
                 temperature=1.0, seed=None, dist_dir=_DEFAULT_DIST_DIR,
                 bars_per_chord=1, bpm=120):
        if genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {genre!r}")

        if isinstance(tonic_pc, str):
            note = tonic_pc[0].upper() + tonic_pc[1:]
            tonic_pc = NOTE_TO_PC.get(note)
            if tonic_pc is None: raise ValueError(f"Unrecognised tonic: {tonic_pc!r}")

        self.genre          = genre
        self.tonic_pc       = int(tonic_pc) % 12
        self.num_chords     = num_chords
        self.temperature    = float(temperature)
        self.bars_per_chord = bars_per_chord
        self.bpm            = bpm
        self.rng            = random.Random(seed)

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
        if self.genre in ("pop_rock", "folk"):
            for candidate in ("0_major", "0_minor"):
                if candidate in self._l1: return candidate
        if self._state_freq:
            states  = list(self._state_freq.keys())
            weights = [self._state_freq[s] for s in states]
            return self.rng.choices(states, weights=weights, k=1)[0]
        return "0_major"

    def _apply_temperature(self, prob_dict: dict) -> dict:
        if self.temperature == 1.0: return prob_dict
        scaled = {k: v ** (1.0 / self.temperature) for k, v in prob_dict.items()}
        total  = sum(scaled.values())
        return {k: v / total for k, v in scaled.items()}

    def _level1_walk(self) -> list[str]:
        states = []
        current = self._tonic_state()

        for _ in range(self.num_chords):
            states.append(current)
            transitions = self._l1.get(current)
            if not transitions:
                current = self._tonic_state()
                continue
            current = _weighted_sample(self._apply_temperature(transitions), self.rng)
        return states

    def _sample_extensions(self, state: str) -> tuple[str, str, str, str]:
        ext_dist = self._l2e.get(state)
        if not ext_dist: return ("N", "N", "N", "N")
        raw = _weighted_sample(ext_dist, self.rng)
        tokens = raw.strip("()").split(",")
        while len(tokens) < 4: tokens.append("N")
        return tuple(tokens[:4])

    def _sample_bass(self, state: str) -> int:
        bass_dist = self._l2b.get(state)
        return int(_weighted_sample(bass_dist, self.rng)) if bass_dist else 0

    def _smooth_sequence(self, chords: list[dict]) -> list[dict]:
        smoothed = []
        prev_bass_abs = None

        for chord in chords:
            root_pc    = chord["root_pc"]
            sampled    = chord["bass_semi"]
            tone_pool  = chord["tone_pool"]

            if prev_bass_abs is None:
                best_semi = sampled
            else:
                best_semi = _smooth_bass(prev_bass_abs, root_pc, sampled, tone_pool)

            prev_bass_abs = (root_pc + best_semi) % 12
            smoothed.append({**chord, "bass_semi": best_semi, "bass_abs_pc": prev_bass_abs})
        return smoothed

    def _translate(self, chords: list[dict]) -> list[dict]:
        translated = []
        seconds_per_bar = (60.0 / self.bpm) * 4
        bar_len         = seconds_per_bar * self.bars_per_chord

        for i, chord in enumerate(chords):
            root_pc, bass_pc = chord["root_pc"], chord["bass_abs_pc"]
            root_name, bass_name = PC_TO_NOTE[root_pc], PC_TO_NOTE[bass_pc]
            seventh, ninth = chord["seventh"], chord["ninth"]
            eleventh, thirteenth = chord["eleventh"], chord["thirteenth"]
            triad = chord["triad"]

            harte = _reconstruct_harte(root_name, triad, bass_name, seventh, ninth, eleventh, thirteenth)
            
            translated.append({
                "time_start": round(i * bar_len, 4),
                "time_end":   round((i + 1) * bar_len, 4),
                "root":       root_name,
                "triad":      triad,
                "bass":       bass_name,
                "seventh":    seventh,
                "ninth":      ninth,
                "eleventh":   eleventh,
                "thirteenth": thirteenth,
                "harte":      harte,
                # --- RETAINED FOR MULTITRACK COMPOSER MIDI MATH ---
                "root_pc":     root_pc,
                "bass_abs_pc": bass_pc,
                "bass_semi":   chord["bass_semi"],
                "tone_pool":   chord["tone_pool"],
            })
        return translated

    def generate(self) -> list[dict]:
        l1_states = self._level1_walk()
        raw_chords = []
        
        for state in l1_states:
            interval_str, triad = state.rsplit("_", 1)
            root_interval = int(interval_str)
            root_pc       = (root_interval + self.tonic_pc) % 12

            seventh, ninth, eleventh, thirteenth = self._sample_extensions(state)
            bass_semi = self._sample_bass(state)
            tone_pool = _build_tone_pool(triad, seventh, ninth, eleventh, thirteenth)

            raw_chords.append({
                "state":         state,
                "root_interval": root_interval,
                "root_pc":       root_pc,
                "triad":         triad,
                "seventh":       seventh,
                "ninth":         ninth,
                "eleventh":      eleventh,
                "thirteenth":    thirteenth,
                "bass_semi":     bass_semi,
                "tone_pool":     tone_pool,
                "bass_abs_pc":   (root_pc + bass_semi) % 12,
            })

        smoothed = self._smooth_sequence(raw_chords)
        return self._translate(smoothed)

    def to_lab_rows(self, chords: Optional[list[dict]] = None) -> list[str]:
        if chords is None: chords = self.generate()
        rows = []
        for c in chords:
            row = "\t".join([
                f"{c['time_start']:.4f}", f"{c['time_end']:.4f}",
                c["root"], c["triad"], c["bass"],
                c["seventh"], c["ninth"], c["eleventh"], c["thirteenth"], c["harte"],
            ])
            rows.append(row)
        return rows

    def write_lab(self, path: str, chords: Optional[list[dict]] = None) -> None:
        rows = self.to_lab_rows(chords)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Multitrack Composer (Java Interoperability)
# ───────────────────────────────────────────────────────────────────────────── 
 
def _midi_to_jfugue(midi: int) -> str:
    """Absolute MIDI integer → JFugue 5 note-name string, e.g. 60 → 'C5'."""
    _NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    octave = (midi // 12) - 1
    name   = _NOTE_NAMES[midi % 12]
    return f"{name}{octave}"
 
 
def _chord_token(midi_notes: list[int], duration: str) -> str:
    """
    Build a JFugue simultaneous-note token that advances the cursor by
    exactly `duration`.
 
    JFugue 5 rule: when notes are joined by `+`, the cursor advances by the
    duration of the *last* note in the chain.  Notes before the last must be
    written without a duration suffix so they share the last note's clock tick.
    We therefore write every note without a duration except the final one.
 
        C5+E5+G5w   ← cursor advances by one whole note
    """
    if not midi_notes:
        return f"R{duration}"
    tokens = [_midi_to_jfugue(m) for m in midi_notes]
    # All but the last have no duration; last carries the duration.
    return "+".join(tokens[:-1] + [tokens[-1] + duration])
 
 
# ─────────────────────────────────────────────────────────────────────────────
# MultitrackComposer  (Python side — produces JFugue Staccato strings)
# ─────────────────────────────────────────────────────────────────────────────
 
class MultitrackComposer:
    """
    Translates a Phase-2 chord sequence into a multi-voice JFugue Staccato
    string.  Four melodic voices + GM percussion (V9).
 
    Voice layout
    ────────────
    V0  Chord pad      — whole-note block chords, mid-register (oct 4–5)
    V1  Bass           — whole notes, low register (oct 2–3)
    V2  Extensions     — whole notes, upper register (oct 5–6); rest when none
    V3  Arpeggiator    — 16 × sixteenth notes per chord, mid-upper register
    V9  Percussion     — kick + snare 4/4 pattern, repeats every chord
 
    Every voice begins with   T{bpm} V{n} I{instrument}
    so all voices share the same tempo and the instrument is set before any
    note data.
    """
 
    # GM instrument pools
    _INST_PAD  = [0, 4, 16, 48, 49, 54, 88]   # Piano, E-Piano, Organ, Strings, Synth Pad
    _INST_BASS = [32, 33, 34, 38, 43]           # Acoustic/Electric/Fretless Bass, Tuba
    _INST_EXT  = [4, 5, 8, 11, 12]              # E-Piano, Harpsichord, Vibraphone, Vibes
    _INST_ARP  = [73, 75, 26, 66, 80]           # Flute, Pan Flute, Jazz Guitar, Alto Sax, Lead Synth
 
    # GM percussion names for V9 — JFugue 5 requires dictionary name strings
    # in brackets, NOT raw integers.  [36] is invalid; [BASS_DRUM] is correct.
    # Full name list: http://www.jfugue.org/doc/constant-values.html
    _KICK   = "BASS_DRUM"        # MIDI 36
    _SNARE  = "ACOUSTIC_SNARE"   # MIDI 38
    _HIHAT  = "CLOSED_HI_HAT"    # MIDI 42
    _CRASH  = "CRASH_CYMBAL_1"   # MIDI 49
 
    def __init__(self, chords: list[dict], song_id: int, seed: Optional[int] = None):
        self.chords  = chords
        self.song_id = song_id
        rng = random.Random(seed if seed is not None else song_id)
 
        self.inst_pad  = rng.choice(self._INST_PAD)
        self.inst_bass = rng.choice(self._INST_BASS)
        self.inst_ext  = rng.choice(self._INST_EXT)
        self.inst_arp  = rng.choice(self._INST_ARP)
 
        self._rng = rng  # reused for arp decisions
 
    # ── per-measure builders ──────────────────────────────────────────────────
 
    def _measure_pad(self, chord: dict) -> str:
        """
        V0: block chord — triad + optional 7th stacked in octave 4/5.
 
        Smart-inversion rule: if the bass note is a semitone or major-seventh
        away from the root (interval 1 or 11), the root clashes hard.  Move the
        root up one octave (first inversion) so the bass can breathe.
        """
        root_pc      = chord["root_pc"]
        bass_pc      = chord["bass_abs_pc"]
        triad_semis  = list(TRIAD_TONES.get(chord["triad"], [0, 4, 7]))
        oct_base     = 60  # middle C octave
 
        bass_rel = (bass_pc - root_pc) % 12
        if bass_rel in (1, 11) and len(triad_semis) >= 3:
            # 1st inversion: push root tone up an octave
            triad_semis = [triad_semis[1], triad_semis[2], triad_semis[0] + 12]
 
        # Add seventh if present
        seventh_semi = EXT_TO_SEMI.get(chord.get("seventh", "N"))
        if seventh_semi is not None:
            triad_semis.append(seventh_semi)
 
        midi_notes = [oct_base + root_pc + s for s in sorted(set(triad_semis))]
        return _chord_token(midi_notes, "w")
 
    def _measure_bass(self, chord: dict) -> str:
        """V1: single bass note, octave 2–3."""
        root_pc  = chord["root_pc"]
        bass_pc  = chord["bass_abs_pc"]
        bass_semi = chord["bass_semi"]
        oct_base = 36  # C2
 
        bass_midi = oct_base + bass_pc
        # If the bass note is 'above' the root and widely voiced, pull it down
        # an octave so it sits in the low register.
        if bass_pc > root_pc and bass_semi > 7:
            bass_midi -= 12
        # Safety: keep in range [24, 55]
        while bass_midi > 55: bass_midi -= 12
        while bass_midi < 24: bass_midi += 12
 
        return f"{_midi_to_jfugue(bass_midi)}w"
 
    def _measure_ext(self, chord: dict) -> str:
        """V2: stack of extension notes (9th, 11th, 13th), octave 5–6.  Rest if none."""
        root_pc  = chord["root_pc"]
        oct_base = 72  # C5
 
        ext_midis = []
        for key in ("ninth", "eleventh", "thirteenth"):
            val = chord.get(key, "N")
            semi = EXT_TO_SEMI.get(val)
            if semi is not None:
                ext_midis.append(oct_base + root_pc + semi)
 
        if not ext_midis:
            return "Rw"
        return _chord_token(sorted(ext_midis), "w")
 
    def _measure_arp(self, chord: dict) -> str:
        """
        V3: 16 sixteenth-note slots, strictly one token per slot so the voice
        stays bar-locked.  80% chord-tone / 20% non-chord-tone (NCT).
        Rests on beat downbeats (slots 0, 4, 8, 12) with 60% probability.
        """
        root_pc   = chord["root_pc"]
        tone_pool = chord["tone_pool"]
        nct_pool  = [s for s in range(12) if s not in tone_pool]
        oct_base  = 72  # C5
 
        tokens = []
        for beat in range(16):
            if beat % 4 == 0 and self._rng.random() < 0.6:
                tokens.append("Rs")
            elif self._rng.random() < 0.80 or not nct_pool:
                semi     = self._rng.choice(tone_pool)
                midi     = oct_base + root_pc + semi
                tokens.append(f"{_midi_to_jfugue(midi)}s")
            else:
                semi     = self._rng.choice(nct_pool)
                midi     = oct_base + root_pc + semi
                tokens.append(f"{_midi_to_jfugue(midi)}s")
 
        return " ".join(tokens)
 
    def _measure_drums(self, measure_idx: int) -> str:
        """
        V9: 16 sixteenth-note slots of GM percussion.
 
        JFugue 5 V9 syntax: bracketed DICTIONARY NAMES, not integers.
            CORRECT:  [BASS_DRUM]s   [ACOUSTIC_SNARE]s
            WRONG:    [36]s  (raises "Could not find '36' in dictionary")
 
        Simultaneous hits via `+` are also unreliable on V9 in JFugue 5 —
        the parser attempts a dictionary lookup on the left operand as a note
        name and fails.  Crash + kick are emitted as two consecutive sixteenth
        tokens instead (inaudibly tight at any reasonable BPM).
 
        Bar 0 gets an extra leading crash token, keeping every subsequent bar
        at exactly 16 sixteenths (one whole note) regardless.
        """
        tokens = []
 
        # Prepend crash on the very first bar only.
        if measure_idx == 0:
            tokens.append(f"[{self._CRASH}]s")
 
        for slot in range(16):
            if slot == 0:
                tokens.append(f"[{self._KICK}]s")
            elif slot == 4:
                tokens.append(f"[{self._SNARE}]s")
            elif slot == 8:
                tokens.append(f"[{self._KICK}]s")
            elif slot == 12:
                tokens.append(f"[{self._SNARE}]s")
            else:
                if self._rng.random() < 0.15:
                    tokens.append("Rs")
                else:
                    tokens.append(f"[{self._HIHAT}]s")
 
        return " ".join(tokens)
 
    # ── public API ────────────────────────────────────────────────────────────
 
    def render_to_jfugue(self, bpm: int) -> str:
        """
        Build the full multi-voice JFugue Staccato string.
 
        CRITICAL: every voice starts with T{bpm} so JFugue uses the same tempo
        when computing absolute tick positions.  Without this, voices that don't
        inherit the tempo marker play at 120 BPM regardless of the requested BPM,
        producing wildly different sequence lengths.
        """
        # Voice builders: (voice_num, instrument, measure_builder_fn)
        voice_configs = [
            (0, self.inst_pad,  self._measure_pad),
            (1, self.inst_bass, self._measure_bass),
            (2, self.inst_ext,  self._measure_ext),
            (3, self.inst_arp,  self._measure_arp),
        ]
 
        voice_strings = []
        for v_num, inst, measure_fn in voice_configs:
            # Header: tempo + voice + instrument — in that order, before any notes
            parts = [f"T{bpm}", f"V{v_num}", f"I{inst}"]
            for chord in self.chords:
                parts.append(measure_fn(chord))
            voice_strings.append(" ".join(parts))
 
        # Percussion (V9) — no instrument token needed; GM always uses channel 10
        drum_parts = [f"T{bpm}", "V9"]
        for i, _ in enumerate(self.chords):
            drum_parts.append(self._measure_drums(i))
        voice_strings.append(" ".join(drum_parts))
 
        return "  ".join(voice_strings)


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="ChordFormer Phase 2/3 Batch Generator")
    p.add_argument("--genre",       default="pop_rock", choices=VALID_GENRES)
    p.add_argument("--tonic",       default="C", help="Tonic note name (C, F#, Bb …) or integer 0-11")
    p.add_argument("--num",         type=int, default=16, help="Number of chords per song")
    p.add_argument("--songs",       type=int, default=5, help="Number of songs to generate in the batch")
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--bpm",         type=int, default=120)
    p.add_argument("--seed",        type=int, default=None)
    p.add_argument("--dist-dir",    default=_DEFAULT_DIST_DIR)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    try:
        tonic = int(args.tonic)
    except ValueError:
        tonic = args.tonic

    OUTPUT_TXT_FILE = "gen/generated_scores.txt"
    LABELS_DIR = "gen/labels"
    os.makedirs(LABELS_DIR, exist_ok=True)
    
    print(f"Starting batch generation of {args.songs} songs...")
    print(f"BPM: {args.bpm} | Genre: {args.genre.upper()}")
    
    with open(OUTPUT_TXT_FILE, 'w') as f:
        for i in range(args.songs):
            print(f" -> Generating track {i}...")
            
            # 1. Initialize Generator (Varying the seed safely if one was provided)
            gen = ChordGenerator(
                genre       = args.genre,
                tonic_pc    = tonic,
                num_chords  = args.num,
                temperature = args.temperature,
                bpm         = args.bpm,
                seed        = args.seed + i if args.seed is not None else None,
                dist_dir    = args.dist_dir,
            )
            
            # 2. Generate Harmonic Logic
            chords = gen.generate()
            
            # 3. Save .lab annotations
            lab_path = os.path.join(LABELS_DIR, f"SONG_{i}.lab")
            gen.write_lab(lab_path, chords)
            
            # 4. Compile JFugue String
            composer = MultitrackComposer(chords, i)
            jfugue_string = composer.render_to_jfugue(bpm=args.bpm)
            
            # 5. Write to Java interchange file
            f.write(f"START_SONG_{i}\n")
            f.write(jfugue_string + "\n")
            f.write("END_SONG\n")

            
    print(f"\nSuccess! Wrote rendering instructions to {OUTPUT_TXT_FILE}")
    print("Ready to run HumanizedMidiRenderer.java.")