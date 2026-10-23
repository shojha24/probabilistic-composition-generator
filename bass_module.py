"""Build a bass track from generated chord progression events and support sub-measure inversion slicing."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Mapping

from chord_gen import DURATION_BEATS, PC_TO_NOTE, reconstruct_harte
from chord_module import midi_to_jfugue
from instruments import BASS_INSTRUMENTS, Instrument, sample_bass_instrument
from voicing.types import ChordEvent, Song

INTERVAL_TO_DEGREE = {
    0: "1", 1: "b2", 2: "2", 3: "b3", 4: "3", 5: "4",
    6: "#4", 7: "5", 8: "b6", 9: "6", 10: "b7", 11: "7"
}


def resolve_bass_pitch(bass_pc: int, tonic_pc: int, rng: random.Random) -> int:
    """Generate authentic bass pitch within standard 4-string bass range (E1 to G3, Spec 18 §3.1)."""
    # Base octave in Octave 1: C1 (24) to B1 (35)
    base_midi = 24 + ((bass_pc + tonic_pc) % 12)

    # 4-string electric/upright bass bottom limit is E1 (MIDI 28)
    if base_midi < 28:
        base_midi += 12  # Transpose low C, C#, D, D# up to Octave 2

    # 20% probability of an octave jump to high walking/slap register (Octave 2-3)
    # Allows notes up to G3 (MIDI 55)
    if rng.random() < 0.20 and base_midi + 12 <= 55:
        return base_midi + 12
    return base_midi


def slice_chord_events(
    events: list[dict | ChordEvent] | Mapping[str, Any],
    genre: str = "pop_rock",
    tonic_pc: int = 0,
    bpm: int = 120,
    seed: int | None = None,
) -> list[dict]:
    """Slice chord events into sub-measure inversion slices aligned with bass movement (Spec 17 §2)."""
    if isinstance(events, Mapping):
        genre = events.get("genre", genre)
        tonic_pc = events.get("tonic_pc", tonic_pc)
        bpm = events.get("bpm", bpm)
        events = events.get("chords", [])
    seconds_per_beat = 60.0 / bpm if bpm > 0 else 0.5
    rng = random.Random(seed)
    sliced_events: list[dict] = []
    current_time = 0.0

    for index, event in enumerate(events):
        is_no_chord = (
            event.is_no_chord if isinstance(event, ChordEvent)
            else event.get("is_no_chord", event.get("harte") == "N")
        )
        duration_token = (
            event.duration_token if isinstance(event, ChordEvent)
            else event.get("duration_token", "w")
        )
        duration_beats = float(DURATION_BEATS.get(duration_token, 4.0))
        duration_seconds = duration_beats * seconds_per_beat
        time_start = (
            event.get("time_start", current_time)
            if isinstance(event, dict)
            else current_time
        )

        if is_no_chord:
            sliced_events.append({
                "is_no_chord": True,
                "root_interval": None,
                "triad": None,
                "bass_interval": None,
                "seventh": "N",
                "ninth": "N",
                "eleventh": "N",
                "thirteenth": "N",
                "root": None,
                "bass": None,
                "harte": "N",
                "duration_token": duration_token,
                "duration_beats": duration_beats,
                "duration_seconds": duration_seconds,
                "time_start": time_start,
                "time_end": time_start + duration_seconds,
            })
            current_time = time_start + duration_seconds
            continue

        root_interval = (
            event.root_interval if isinstance(event, ChordEvent)
            else event["root_interval"]
        )
        triad = (
            event.triad if isinstance(event, ChordEvent)
            else event["triad"]
        )
        seventh = (
            getattr(event, "seventh", "N") if isinstance(event, ChordEvent)
            else event.get("seventh", "N")
        )
        ninth = (
            getattr(event, "ninth", "N") if isinstance(event, ChordEvent)
            else event.get("ninth", "N")
        )
        eleventh = (
            getattr(event, "eleventh", "N") if isinstance(event, ChordEvent)
            else event.get("eleventh", "N")
        )
        thirteenth = (
            getattr(event, "thirteenth", "N") if isinstance(event, ChordEvent)
            else event.get("thirteenth", "N")
        )
        base_harte = (
            event.harte if isinstance(event, ChordEvent)
            else event.get("harte")
        )
        root_pc = (root_interval + tonic_pc) % 12
        root_name = PC_TO_NOTE[root_pc]
        base_harte_no_slash = (
            base_harte.split("/")[0]
            if base_harte
            else reconstruct_harte(
                root_name, triad, root_name, seventh, ninth, eleventh, thirteenth
            )
        )

        has_next = index + 1 < len(events)
        next_event = events[index + 1] if has_next else None
        next_is_no_chord = (
            (
                next_event.is_no_chord if isinstance(next_event, ChordEvent)
                else next_event.get("is_no_chord", next_event.get("harte") == "N")
            )
            if next_event is not None
            else True
        )

        def make_slice(t_start: float, d_tok: str, d_beats: float, b_int: int, b_name: str, h_label: str) -> dict:
            d_sec = d_beats * seconds_per_beat
            return {
                "is_no_chord": False,
                "root_interval": root_interval,
                "triad": triad,
                "bass_interval": b_int,
                "seventh": seventh,
                "ninth": ninth,
                "eleventh": eleventh,
                "thirteenth": thirteenth,
                "root": root_name,
                "bass": b_name,
                "harte": h_label,
                "duration_token": d_tok,
                "duration_beats": d_beats,
                "duration_seconds": d_sec,
                "time_start": t_start,
                "time_end": t_start + d_sec,
            }

        base_bass_interval = (
            event.bass_interval if isinstance(event, ChordEvent)
            else event.get("bass_interval", 0)
        )
        if base_bass_interval != 0:
            inv_bass_pc = (root_pc + base_bass_interval) % 12
            inv_bass_name = PC_TO_NOTE[inv_bass_pc]
            inv_deg = INTERVAL_TO_DEGREE.get(base_bass_interval, inv_bass_name)
            inv_harte = base_harte if (base_harte and "/" in base_harte) else f"{base_harte_no_slash}/{inv_deg}"
            sliced_events.append(
                make_slice(time_start, duration_token, duration_beats, base_bass_interval, inv_bass_name, inv_harte)
            )
            current_time = time_start + duration_seconds
            continue

        if duration_token not in ("w", "h.", "h"):
            sliced_events.append(
                make_slice(time_start, duration_token, duration_beats, 0, root_name, base_harte_no_slash)
            )
            current_time = time_start + duration_seconds
            continue

        if has_next and not next_is_no_chord:
            next_root = (
                next_event.root_interval if isinstance(next_event, ChordEvent)
                else next_event["root_interval"]
            )
            next_bass = (
                getattr(next_event, "bass_interval", 0) if isinstance(next_event, ChordEvent)
                else next_event.get("bass_interval", 0)
            )
            next_bass_pc = (next_root + next_bass + tonic_pc) % 12
        else:
            next_bass_pc = root_pc

        fifth_interval = 7
        fifth_pc = (root_pc + fifth_interval) % 12
        fifth_name = PC_TO_NOTE[fifth_pc]
        fifth_harte = f"{base_harte_no_slash}/5"

        if triad in ("minor", "min", "diminished", "dim", "hdim7"):
            third_interval = 3
            third_degree = "b3"
        elif triad == "sus4":
            third_interval = 5
            third_degree = "4"
        elif triad == "sus2":
            third_interval = 2
            third_degree = "2"
        else:
            third_interval = 4
            third_degree = "3"
        third_pc = (root_pc + third_interval) % 12
        third_name = PC_TO_NOTE[third_pc]
        third_harte = f"{base_harte_no_slash}/{third_degree}"

        approach_dir = rng.choice([-1, 1])
        approach_pc = (next_bass_pc + approach_dir) % 12
        approach_name = PC_TO_NOTE[approach_pc]
        approach_interval = (approach_pc - root_pc) % 12
        approach_deg = INTERVAL_TO_DEGREE.get(approach_interval, approach_name)
        approach_harte = (
            f"{base_harte_no_slash}/{approach_deg}"
            if approach_deg != "1"
            else base_harte_no_slash
        )

        pattern_roll = rng.random()
        if duration_token == "w":
            if genre == "pop_rock":
                if pattern_roll < 0.60:
                    # 60% straight root pedal
                    sliced_events.append(
                        make_slice(time_start, "w", 4.0, 0, root_name, base_harte_no_slash)
                    )
                elif pattern_roll < 0.85:
                    # 25% root groove: bass audio alternates root-5th, but label remains root-position
                    # to match Billboard/Isophonics annotation conventions (Spec 19 §4.1)
                    sliced_events.append(
                        make_slice(time_start, "w", 4.0, 0, root_name, base_harte_no_slash)
                    )
                else:
                    # 15% end-of-phrase approach on beat 4 (h. + q)
                    sliced_events.extend([
                        make_slice(time_start, "h.", 3.0, 0, root_name, base_harte_no_slash),
                        make_slice(time_start + 3.0 * seconds_per_beat, "q", 1.0, approach_interval, approach_name, approach_harte),
                    ])
            else:  # jazz
                if pattern_roll < 0.50:
                    # 50% 4-beat scalar walking lines (q + q + q + q)
                    spb = seconds_per_beat
                    sliced_events.extend([
                        make_slice(time_start, "q", 1.0, 0, root_name, base_harte_no_slash),
                        make_slice(time_start + spb, "q", 1.0, third_interval, third_name, third_harte),
                        make_slice(time_start + 2.0 * spb, "q", 1.0, fifth_interval, fifth_name, fifth_harte),
                        make_slice(time_start + 3.0 * spb, "q", 1.0, approach_interval, approach_name, approach_harte),
                    ])
                elif pattern_roll < 0.80:
                    # 30% root-5th alternations (h + h)
                    sliced_events.extend([
                        make_slice(time_start, "h", 2.0, 0, root_name, base_harte_no_slash),
                        make_slice(time_start + 2.0 * seconds_per_beat, "h", 2.0, fifth_interval, fifth_name, fifth_harte),
                    ])
                else:
                    # 20% complex passing lines (q + q + q + q)
                    passing_2nd_interval = (third_interval // 2) if third_interval > 1 else 1
                    passing_2nd_pc = (root_pc + passing_2nd_interval) % 12
                    passing_2nd_name = PC_TO_NOTE[passing_2nd_pc]
                    passing_2nd_deg = INTERVAL_TO_DEGREE.get(passing_2nd_interval, passing_2nd_name)
                    passing_2nd_harte = f"{base_harte_no_slash}/{passing_2nd_deg}"
                    spb = seconds_per_beat
                    sliced_events.extend([
                        make_slice(time_start, "q", 1.0, 0, root_name, base_harte_no_slash),
                        make_slice(time_start + spb, "q", 1.0, passing_2nd_interval, passing_2nd_name, passing_2nd_harte),
                        make_slice(time_start + 2.0 * spb, "q", 1.0, third_interval, third_name, third_harte),
                        make_slice(time_start + 3.0 * spb, "q", 1.0, approach_interval, approach_name, approach_harte),
                    ])
        elif duration_token == "h.":
            threshold = 0.15 if genre == "pop_rock" else 0.30
            if pattern_roll < threshold:
                sliced_events.extend([
                    make_slice(time_start, "h", 2.0, 0, root_name, base_harte_no_slash),
                    make_slice(time_start + 2.0 * seconds_per_beat, "q", 1.0, approach_interval, approach_name, approach_harte),
                ])
            else:
                sliced_events.append(
                    make_slice(time_start, "h.", 3.0, 0, root_name, base_harte_no_slash)
                )
        elif duration_token == "h":
            threshold = 0.10 if genre == "pop_rock" else 0.25
            if pattern_roll < threshold:
                sliced_events.extend([
                    make_slice(time_start, "q", 1.0, 0, root_name, base_harte_no_slash),
                    make_slice(time_start + seconds_per_beat, "q", 1.0, approach_interval, approach_name, approach_harte),
                ])
            else:
                sliced_events.append(
                    make_slice(time_start, "h", 2.0, 0, root_name, base_harte_no_slash)
                )
        else:
            sliced_events.append(
                make_slice(time_start, duration_token, duration_beats, 0, root_name, base_harte_no_slash)
            )

        current_time = time_start + duration_seconds

    return sliced_events


def slice_progression(progression: dict | Song, seed: int | None = None) -> dict:
    """Slice a full progression dictionary into time-aligned sub-measure inversion slices."""
    song = progression if isinstance(progression, Song) else Song.from_dict(progression)
    genre = getattr(song, "genre", "pop_rock")
    tonic_pc = getattr(song, "tonic_pc", 0)
    bpm = (
        progression.get("bpm", 120)
        if isinstance(progression, dict)
        else getattr(song, "bpm", 120)
    )
    events = (
        progression.chords if isinstance(progression, Song)
        else progression["chords"]
    )
    sliced = slice_chord_events(
        events,
        genre=genre,
        tonic_pc=tonic_pc,
        bpm=bpm,
        seed=seed,
    )
    payload = {
        "genre": genre,
        "tonic_pc": tonic_pc,
        "bpm": bpm,
        "num_chords": len(sliced),
        "duration_total_seconds": sum(e["duration_seconds"] for e in sliced),
        "chords": sliced,
    }
    if isinstance(progression, dict):
        if "cohort" in progression:
            payload["cohort"] = progression["cohort"]
        progression.update(payload)
    return payload


@dataclass
class BassModule:
    instrument: int | None = None
    seed: int | None = None
    pad_collapse_probability: float = 0.25
    last_instrument: Instrument | None = None
    collapsed_to_pad: bool = False

    def slice_progression(self, progression: dict | Song) -> dict:
        return slice_progression(progression, seed=self.seed)

    def render(
        self,
        progression: dict | Song,
        pad_instrument: Instrument | None = None,
        pad_mode: bool = False,
        chord_midis: list[list[int]] | None = None,
    ) -> str:
        song = progression if isinstance(progression, Song) else Song.from_dict(progression)
        if not 0.0 <= self.pad_collapse_probability <= 1.0:
            raise ValueError("pad_collapse_probability must be between 0 and 1")
        rng = random.Random(self.seed)
        collapse_draw = (
            rng.random() if pad_instrument is not None else None
        )
        self.collapsed_to_pad = (
            pad_mode
            and pad_instrument is not None
            and collapse_draw is not None
            and collapse_draw < self.pad_collapse_probability
        )
        if self.collapsed_to_pad:
            self.last_instrument = pad_instrument
            program = pad_instrument.program
        elif self.instrument is None:
            self.last_instrument = sample_bass_instrument(rng)
            program = self.last_instrument.program
        else:
            program = self.instrument
        events = progression.chords if isinstance(progression, Song) else progression["chords"]
        genre = getattr(song, "genre", "pop_rock")

        # Check if the progression was already sub-measure sliced
        has_submeasure_slices = any(
            (
                e.bass_interval != 0 if isinstance(e, ChordEvent)
                else e.get("bass_interval", 0) != 0
            )
            for e in events
        )

        tokens = []
        for index, event in enumerate(events):
            is_no_chord = (
                event.is_no_chord if isinstance(event, ChordEvent)
                else event.get("is_no_chord", event.get("harte") == "N")
            )
            duration = event.get("duration_token", "w") if isinstance(event, dict) else event.duration_token
            if is_no_chord:
                tokens.append(f"R{duration}")
                continue
            root_interval = event["root_interval"] if isinstance(event, dict) else event.root_interval
            bass_interval = event.get("bass_interval", 0) if isinstance(event, dict) else event.bass_interval
            bass_pc = (root_interval + bass_interval) % 12
            midi = resolve_bass_pitch(bass_pc, song.tonic_pc, rng)
            chord_pitches = chord_midis[index] if chord_midis and index < len(chord_midis) else ()
            if any(pitch == midi for pitch in chord_pitches) and midi - 12 >= 28:
                midi -= 12

            # If the progression is already explicitly sliced, render sub-measure slices directly.
            # For whole notes (w) with root bass (bass_interval == 0), allow groove rendering
            # per Spec 19 §4.1: bass audio alternates root-5th, but label remains root-position.
            if duration != "w" and has_submeasure_slices:
                tokens.append(f"{midi_to_jfugue(midi)}{duration}")
                continue
            if bass_interval != 0:
                tokens.append(f"{midi_to_jfugue(midi)}{duration}")
                continue

            has_next = (
                not self.collapsed_to_pad
                and index + 1 < len(events)
            )
            next_event = events[index + 1] if has_next else None
            next_is_no_chord = (
                (
                    next_event.is_no_chord if isinstance(next_event, ChordEvent)
                    else next_event.get("is_no_chord", next_event.get("harte") == "N")
                )
                if next_event is not None
                else True
            )

            if has_next and not next_is_no_chord and next_event is not None:
                next_root = next_event["root_interval"] if isinstance(next_event, dict) else next_event.root_interval
                next_bass = next_event.get("bass_interval", 0) if isinstance(next_event, dict) else next_event.bass_interval
                next_bass_pc = (next_root + next_bass + song.tonic_pc) % 12
                next_base = 24 + next_bass_pc
                if next_base < 28:
                    next_base += 12
                if abs((next_base + 12) - midi) < abs(next_base - midi) and next_base + 12 <= 55:
                    next_midi = next_base + 12
                else:
                    next_midi = next_base
                app_choices = [p for p in (next_midi - 1, next_midi + 1) if 28 <= p <= 55]
                approach_midi = rng.choice(app_choices) if app_choices else next_midi
                fifth_midi = midi + 7 if midi + 7 <= 55 else midi - 5
                third_midi = midi + 4 if midi + 4 <= 55 else midi - 8

                pattern_roll = rng.random()
                if duration == "w":
                    if genre == "pop_rock":
                        if pattern_roll < 0.60:
                            tokens.append(f"{midi_to_jfugue(midi)}w")
                        elif pattern_roll < 0.85:
                            # 25% root-5th
                            tokens.append(f"{midi_to_jfugue(midi)}h {midi_to_jfugue(fifth_midi)}h")
                        else:
                            # 15% approach
                            tokens.append(f"{midi_to_jfugue(midi)}h. {midi_to_jfugue(approach_midi)}q")
                    else:  # jazz
                        if pattern_roll < 0.50:
                            # 50% 4-beat walking
                            tokens.append(
                                f"{midi_to_jfugue(midi)}q {midi_to_jfugue(third_midi)}q "
                                f"{midi_to_jfugue(fifth_midi)}q {midi_to_jfugue(approach_midi)}q"
                            )
                        elif pattern_roll < 0.80:
                            # 30% root-5th
                            tokens.append(f"{midi_to_jfugue(midi)}h {midi_to_jfugue(fifth_midi)}h")
                        else:
                            # 20% complex passing line
                            passing_midi = midi + 2 if midi + 2 <= 55 else midi - 10
                            tokens.append(
                                f"{midi_to_jfugue(midi)}q {midi_to_jfugue(passing_midi)}q "
                                f"{midi_to_jfugue(third_midi)}q {midi_to_jfugue(approach_midi)}q"
                            )
                elif duration == "h.":
                    threshold = 0.15 if genre == "pop_rock" else 0.30
                    if pattern_roll < threshold:
                        tokens.append(f"{midi_to_jfugue(midi)}h {midi_to_jfugue(approach_midi)}q")
                    else:
                        tokens.append(f"{midi_to_jfugue(midi)}h.")
                elif duration == "h":
                    threshold = 0.10 if genre == "pop_rock" else 0.25
                    if pattern_roll < threshold:
                        tokens.append(f"{midi_to_jfugue(midi)}q {midi_to_jfugue(approach_midi)}q")
                    else:
                        tokens.append(f"{midi_to_jfugue(midi)}h")
                else:
                    tokens.append(f"{midi_to_jfugue(midi)}{duration}")
            else:
                tokens.append(f"{midi_to_jfugue(midi)}{duration}")
        return f"V1 I{program} " + " ".join(tokens)

