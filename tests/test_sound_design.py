import json

from render import render_directory
from sound_design import CONDITION_ROLES, derive_seed


def _label():
    return {
        "genre": "pop_rock", "tonic_pc": 0, "bpm": 120, "num_chords": 1,
        "chords": [{
            "root_interval": 0, "triad": "major", "bass_interval": 0,
            "seventh": "N", "ninth": "N", "eleventh": "N", "thirteenth": "N",
        }],
    }


def test_derived_sound_design_seeds_are_stable_and_domain_separated():
    assert derive_seed(7, "humanization", 0) == derive_seed(7, "humanization", 0)
    assert derive_seed(7, "humanization", 0) != derive_seed(7, "sound-design", 0)


def test_condition_is_a_manifested_role_projection(tmp_path):
    (tmp_path / "song_0.json").write_text(json.dumps(_label()))
    output = tmp_path / "cbm.txt"
    render_directory(tmp_path, output, seed=7, condition="cbm")
    manifest = json.loads(output.with_name(output.name + ".manifest.json").read_text())
    record = manifest["records"][0]

    assert manifest["condition_id"] == "cbm"
    assert manifest["selected_roles"] == list(CONDITION_ROLES["cbm"])
    assert record["selected_roles"] == list(CONDITION_ROLES["cbm"])
    assert record["audio_rendered"] is False
    assert record["randomness"]["humanization_seed"] == derive_seed(7, "humanization", 0)
    assert manifest["parameter_manifest"]["unresolved_parameters"] == []
