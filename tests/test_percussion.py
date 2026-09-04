import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from percussion_module import (
    PERCUSSION_OMISSION_PROBABILITY,
    PercussionModule,
)
from render import render_song


def _progression(*durations, bpm=120):
    return {
        "genre": "pop_rock",
        "tonic_pc": 0,
        "bpm": bpm,
        "chords": [
            {
                "root_interval": 0,
                "triad": "major",
                "bass_interval": 0,
                "duration_token": duration,
            }
            for duration in durations
        ],
    }


def test_percussion_renders_seeded_groove_on_voice_nine():
    track = PercussionModule(seed=0, omission_probability=0).render(
        _progression("w", "h")
    )

    assert track.startswith("V9 ")
    assert any(
        sound in track
        for sound in ("[BASS_DRUM]", "[ACOUSTIC_BASS_DRUM]")
    )
    assert any(
        sound in track
        for sound in (
            "[ACOUSTIC_SNARE]", "[ELECTRIC_SNARE]", "[SIDE_STICK]",
        )
    )
    assert any(
        sound in track
        for sound in (
            "[CLOSED_HI_HAT]", "[PEDAL_HI_HAT]",
            "[RIDE_CYMBAL_1]", "[RIDE_CYMBAL_2]",
            "[CRASH_CYMBAL_1]", "[CRASH_CYMBAL_2]",
            "[CHINESE_CYMBAL]",
        )
    )


def test_render_song_wires_percussion_voice():
    score = render_song(_progression("w"), seed=0)

    assert score.split("  ")[-1].startswith("V9 ")


def test_render_song_exposes_percussion_inclusion_percentage():
    progression = _progression("w")
    default_score = render_song(progression, seed=0)
    explicit_default_score = render_song(
        progression, seed=0, percussion_percent=70
    )
    assert default_score == explicit_default_score

    silent_score = render_song(
        progression, seed=0, percussion_percent=0
    )
    assert "[" not in silent_score.split("  ")[-1]

    audible_score = render_song(
        progression, seed=0, percussion_percent=100
    )
    assert "[" in audible_score.split("  ")[-1]


def test_render_song_rejects_invalid_percussion_inclusion_percentage():
    with pytest.raises(ValueError, match="percussion_percent"):
        render_song(_progression("w"), seed=0, percussion_percent=-1)
    with pytest.raises(ValueError, match="percussion_percent"):
        render_song(_progression("w"), seed=0, percussion_percent=101)


def test_omitted_percussion_keeps_a_silent_synchronized_voice():
    module = PercussionModule(seed=4, omission_probability=1)
    track = module.render(_progression("w", "h"))

    assert module.last_included is False
    assert track.startswith("V9 ")
    assert all(token == "Rs" for token in track.split()[1:])
    assert "[" not in track


def test_tempo_bands_select_the_requested_feel_probability():
    for bpm, expected_feel in (
        (60, "cut_time"),
        (120, "cut_time"),
        (121, "half_time"),
        (180, "half_time"),
    ):
        for seed in range(100):
            module = PercussionModule(seed=seed, omission_probability=0)
            module.render(_progression("w", bpm=bpm))
            rng = random.Random(seed)
            rng.random()
            draw = rng.random()
            expected = expected_feel if draw < 0.20 else "normal"
            assert module.last_feel == expected

    for bpm in (59, 181):
        module = PercussionModule(seed=0, omission_probability=0)
        module.render(_progression("w", bpm=bpm))
        assert module.last_feel == "normal"


def test_cut_time_uses_double_time_cymbal_subdivision():
    kit = {
        "kick": "BASS_DRUM",
        "snare": "ACOUSTIC_SNARE",
        "cymbal": "CLOSED_HI_HAT",
        "crash": "CRASH_CYMBAL_1",
    }

    assert PercussionModule._render_slot(
        ".", ".", "^", kit,
    ) == ("[CLOSED_HI_HAT]s", "Rs")
    assert PercussionModule._render_slot(
        ".", ".", "^", kit, cymbal_on_both_halves=True,
    ) == ("[CLOSED_HI_HAT]s", "[CLOSED_HI_HAT]s")


def test_default_omission_draw_is_seeded():
    progression = _progression("w")
    for seed in range(100):
        module = PercussionModule(seed=seed)
        module.render(progression)
        expected = random.Random(seed).random() >= (
            PERCUSSION_OMISSION_PROBABILITY
        )
        assert module.last_included is expected


def test_invalid_duration_and_probability_are_rejected():
    with pytest.raises(ValueError, match="omission_probability"):
        PercussionModule(omission_probability=1.1)
    with pytest.raises(ValueError, match="Unknown duration token"):
        PercussionModule(seed=1, omission_probability=0).render(
            _progression("invalid")
        )
