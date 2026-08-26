"""Curated zero-based General MIDI programs for JFugue rendering."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    name: str
    program: int
    roles: frozenset[str]


CHORD_INSTRUMENTS = {
    "piano": (
        Instrument("acoustic-grand-piano", 0, frozenset(("pads", "arpeggios"))),
        Instrument("bright-acoustic-piano", 1, frozenset(("arpeggios",))),
        Instrument("honky-tonk-piano", 3, frozenset(("arpeggios",))),
        Instrument("electric-piano-1", 4, frozenset(("pads",))),
        Instrument("electric-piano-2", 5, frozenset(("arpeggios",))),
        Instrument("harpsichord", 6, frozenset(("arpeggios",))),
        Instrument("clavinet", 7, frozenset(("arpeggios",))),
        Instrument("vibraphone", 11, frozenset(("pads", "arpeggios"))),
        Instrument("marimba", 12, frozenset(("arpeggios",))),
        Instrument("percussive-organ", 17, frozenset(("bass", "arpeggios"))),
        Instrument("rock-organ", 18, frozenset(("pads", "bass"))),
        Instrument("church-organ", 19, frozenset(("pads",))),
        Instrument("shakuhachi", 77, frozenset(("pads",))),
        Instrument("bagpipe", 109, frozenset(("pads",))),
        Instrument("fiddle", 110, frozenset(("pads", "arpeggios"))),
        Instrument("shanai", 111, frozenset(("pads",))),
    ),
    "guitar": (
        Instrument("acoustic-guitar-nylon", 24, frozenset(("arpeggios",))),
        Instrument("acoustic-guitar-steel", 25, frozenset(("arpeggios",))),
        Instrument("electric-guitar-jazz", 26, frozenset(("pads",))),
        Instrument("electric-guitar-clean", 27, frozenset(("pads", "arpeggios"))),
        Instrument("electric-guitar-muted", 28, frozenset(("arpeggios",))),
        Instrument("overdriven-guitar", 29, frozenset(("pads",))),
        Instrument("distortion-guitar", 30, frozenset(("pads",))),
        Instrument("guitar-harmonics", 31, frozenset(("arpeggios",))),
        Instrument("sitar", 104, frozenset(("arpeggios",))),
        Instrument("banjo", 105, frozenset(("arpeggios",))),
        Instrument("shamisen", 106, frozenset(("arpeggios",))),
        Instrument("koto", 107, frozenset(("arpeggios",))),
    ),
    "synth": (
        Instrument("new-age-pad", 88, frozenset(("pads",))),
        Instrument("warm-pad", 89, frozenset(("pads",))),
        Instrument("choir-pad", 91, frozenset(("pads",))),
        Instrument("bowed-pad", 92, frozenset(("pads",))),
        Instrument("metallic-pad", 93, frozenset(("pads",))),
        Instrument("halo-pad", 94, frozenset(("pads",))),
        Instrument("sweep-pad", 95, frozenset(("pads",))),
        Instrument("synth-strings-1", 50, frozenset(("pads",))),
        Instrument("synth-strings-2", 51, frozenset(("pads",))),
        Instrument("atmosphere", 99, frozenset(("pads",))),
        Instrument("soundtrack", 100, frozenset(("pads",))),
    ),
}

BASS_INSTRUMENTS = (
    Instrument("acoustic-bass", 32, frozenset(("bass",))),
    Instrument("electric-bass-finger", 33, frozenset(("bass",))),
    Instrument("electric-bass-pick", 34, frozenset(("bass",))),
    Instrument("fretless-bass", 35, frozenset(("bass",))),
    Instrument("slap-bass-1", 36, frozenset(("bass",))),
    Instrument("slap-bass-2", 37, frozenset(("bass",))),
    Instrument("synth-bass-1", 38, frozenset(("bass",))),
    Instrument("synth-bass-2", 39, frozenset(("bass",))),
    Instrument("contrabass", 43, frozenset(("bass",))),
    Instrument("cello", 42, frozenset(("bass",))),
    Instrument("square-wave", 80, frozenset(("bass", "arpeggios"))),
    Instrument("sawtooth-wave", 81, frozenset(("bass",))),
    Instrument("basslead", 87, frozenset(("bass",))),
)
