import json
from pathlib import Path

import render as render_module
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


def test_audio_stage_updates_manifest_after_midi_bundle(tmp_path, monkeypatch):
    (tmp_path / "song_0.json").write_text(json.dumps(_label()))
    soundfont = tmp_path / "reference.sf2"
    soundfont.write_bytes(b"soundfont")

    def fake_midi_bundle(
        score_paths,
        output_dir,
        *,
        seed,
        tempo_by_ordinal,
    ):
        assert set(score_paths) == {"mixed", "chords", "bass", "percussion"}
        assert tempo_by_ordinal == {0: 120}
        return {
            track: {
                "0": {
                    "path": str(tmp_path / f"{track}.mid"),
                    "sha256": "midi",
                    "humanization_seed": str(seed),
                }
            }
            for track in score_paths
        }

    def fake_audio(manifest, midi_records, output_dir, **kwargs):
        assert set(midi_records) == {"mixed", "chords", "bass", "percussion"}
        assert manifest["selected_roles"] == ["V0", "V1", "V9"]
        assert manifest["records"][0]["selected_roles"] == [
            "V0", "V1", "V9"
        ]
        return {
            "profile_id": "reference-transparent-v1",
            "records": {
                "0": {
                    "mix": {"path": str(tmp_path / "mix.flac"), "sha256": "audio"},
                }
            },
        }

    monkeypatch.setattr(render_module, "render_midi_bundle", fake_midi_bundle)
    monkeypatch.setattr(render_module, "render_audio_bundle", fake_audio)

    output = tmp_path / "audio-scores.txt"
    render_directory(
        tmp_path,
        output,
        seed=7,
        stage="audio",
        soundfont=soundfont,
    )

    manifest = json.loads(
        output.with_name(output.name + ".manifest.json").read_text()
    )
    assert manifest["sound_design"]["stage"] == "audio"
    assert manifest["sound_design"]["audio_rendered"] is True
    assert manifest["records"][0]["audio_rendered"] is True


def test_audio_midi_bundle_injects_role_tempos(tmp_path, monkeypatch):
    score_paths = {}
    for track, line in (
        ("mixed", "T90 V0 Cmaj"),
        ("bass", "V1 C2"),
    ):
        path = tmp_path / f"{track}.txt"
        path.write_text(
            f"START_SONG_0\n{line}\nEND_SONG\n",
            encoding="utf-8",
        )
        score_paths[track] = path

    captured = {}

    def fake_midi_scores(score_path, output_dir, *, seed):
        captured[Path(score_path).name] = Path(score_path).read_text(
            encoding="utf-8"
        )
        return {}

    monkeypatch.setattr(
        render_module,
        "render_midi_scores",
        fake_midi_scores,
    )
    render_module.render_midi_bundle(
        score_paths,
        tmp_path / "midi",
        seed=7,
        tempo_by_ordinal={0: 90},
    )

    assert any("T90 V1 C2" in text for text in captured.values())
    assert any("T90 V0 Cmaj" in text for text in captured.values())
    assert not list((tmp_path / "midi").glob(".timed-score.*"))
