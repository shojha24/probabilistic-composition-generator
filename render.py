"""Render generated progression JSON files as synchronized JFugue score text."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from bass_module import BassModule
from chord_module import ChordModule


def render_song(progression: dict, seed: int | None = None, mode: str = "pads") -> str:
    chords = ChordModule(mode=mode, seed=seed).render(progression)
    bass = BassModule().render(progression)
    return f"{chords}  {bass}"


def render_directory(input_dir: str, output: str, seed: int | None = None, mode: str = "pads") -> None:
    files = sorted(Path(input_dir).glob("song_*.json"))
    if not files:
        raise FileNotFoundError(f"No song_*.json files found in {input_dir}")
    voicer_counts = Counter()
    with open(output, "w", encoding="utf-8") as out:
        for index, path in enumerate(files):
            with open(path, encoding="utf-8") as source:
                progression = json.load(source)
            chord_module = ChordModule(
                mode=mode,
                seed=None if seed is None else seed + index,
            )
            chord_track = chord_module.render(progression)
            bass_module = BassModule(seed=None if seed is None else seed + index)
            bass_track = bass_module.render(
                progression,
                pad_instrument=chord_module.selected_instrument,
                pad_mode=mode == "pads",
            )
            out.write(f"START_SONG_{index}\n")
            out.write(f"{chord_track}  {bass_track}\n")
            out.write("END_SONG\n")
            voicer = chord_module.last_voicer or "unknown"
            voicer_counts[voicer] += 1
            print(
                f"Rendered song {index}: {path.name} "
                f"({voicer}, chord={chord_module.last_instrument} "
                f"[I{chord_module.last_instrument_program}], "
                f"bass={bass_module.last_instrument.name} "
                f"[I{bass_module.last_instrument.program}]"
                f"{' (pad collapse)' if bass_module.collapsed_to_pad else ''}, {mode})"
            )
    print(f"Rendered {len(files)} song(s) to {output}")
    print("Voicer summary:")
    for voicer, count in sorted(voicer_counts.items()):
        print(f"  {voicer}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--mode", choices=("pads", "arpeggios"), default="pads")
    args = parser.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    render_directory(args.in_dir, args.out, args.seed, args.mode)


if __name__ == "__main__":
    main()
