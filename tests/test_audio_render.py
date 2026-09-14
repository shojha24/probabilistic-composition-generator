import os
from pathlib import Path

import pytest

import audio_render
from audio_render import mix_audio_bundle_from_stems, render_audio_bundle
from sound_design import REFERENCE_RENDER_PROFILE


def _manifest(selected_roles=("V0", "V1")):
    return {
        "seed": 7,
        "selected_roles": list(selected_roles),
        "records": [{
            "ordinal": 0,
            "selected_roles": list(selected_roles),
        }],
    }


def _midi_records(tmp_path: Path):
    records = {}
    for track in ("chords", "bass", "percussion"):
        midi_path = tmp_path / f"{track}.mid"
        midi_path.write_bytes(track.encode("ascii"))
        records[track] = {
            "0": {
                "path": str(midi_path),
                "sha256": "unused",
            }
        }
    return records


def test_audio_bundle_renders_stems_and_manifest_selected_mix(
    tmp_path, monkeypatch
):
    soundfont = tmp_path / "reference.sf2"
    soundfont.write_bytes(b"soundfont")
    calls = []

    monkeypatch.setattr(
        audio_render,
        "_resolve_executable",
        lambda binary, label: f"/usr/bin/{binary}",
    )
    monkeypatch.setattr(
        audio_render,
        "_tool_version",
        lambda executable, argument: f"{executable} test",
    )

    def render_midi(executable, midi_path, wav_path, soundfont_path, sample_rate):
        calls.append(("synth", midi_path.name, sample_rate))
        wav_path.write_bytes(b"wav:" + midi_path.read_bytes())

    def encode(executable, wav_path, flac_path, profile):
        flac_path.write_bytes(b"flac:" + wav_path.read_bytes())

    def mix(executable, wav_paths, flac_path, profile):
        calls.append(("mix", [track for track, _path in wav_paths]))
        flac_path.write_bytes(b"mix")

    monkeypatch.setattr(audio_render, "_render_midi_to_wav", render_midi)
    monkeypatch.setattr(audio_render, "_encode_flac", encode)
    monkeypatch.setattr(audio_render, "_mix_flac", mix)

    result = render_audio_bundle(
        _manifest(),
        _midi_records(tmp_path),
        tmp_path / "audio",
        soundfont=soundfont,
        seed=7,
    )

    record = result["records"]["0"]
    assert set(record["stems"]) == {"bass", "chords", "percussion"}
    assert record["selected_roles"] == ["V0", "V1"]
    assert record["mix"]["path"].endswith("/song_0/mix.flac")
    assert calls[-1] == ("mix", ["chords", "bass"])
    assert result["asset"]["sha256"]
    assert result["profile_id"] == REFERENCE_RENDER_PROFILE["profile_id"]


def test_mix_command_contains_role_gains_and_master_policy(tmp_path, monkeypatch):
    captured = {}

    def run(command, *args, **kwargs):
        captured["command"] = command

    monkeypatch.setattr(audio_render.subprocess, "run", run)
    audio_render._mix_flac(
        "ffmpeg",
        [
            ("chords", tmp_path / "chords.wav"),
            ("bass", tmp_path / "bass.wav"),
        ],
        tmp_path / "mix.flac",
        REFERENCE_RENDER_PROFILE,
    )

    command = " ".join(captured["command"])
    assert "-nostdin" in command
    assert "volume=0dB" in command
    assert "volume=3dB" in command
    assert "amix=inputs=2" in command
    assert "loudnorm=I=-18:TP=-1" in command


def test_audio_bundle_reports_missing_renderer(tmp_path, monkeypatch):
    soundfont = tmp_path / "reference.sf2"
    soundfont.write_bytes(b"soundfont")
    monkeypatch.setattr(audio_render.shutil, "which", lambda _binary: None)

    with pytest.raises(FileNotFoundError, match="FluidSynth executable"):
        render_audio_bundle(
            _manifest(),
            _midi_records(tmp_path),
            tmp_path / "audio",
            soundfont=soundfont,
            seed=7,
        )


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX executable wrappers")
def test_audio_bundle_executes_renderer_and_mixer_commands(tmp_path):
    soundfont = tmp_path / "reference.sf2"
    soundfont.write_bytes(b"soundfont")

    def fake_tool(name, version, output_flag=None):
        tool = tmp_path / name
        script = [
            "#!/usr/bin/env python3",
            "from pathlib import Path",
            "import sys",
            "args = sys.argv[1:]",
            f"version = {version!r}",
            "if args and args[0] in {'--version', '-version'}:",
            "    print(version)",
            "    raise SystemExit(0)",
        ]
        if output_flag is None:
            script.extend([
                "output = Path(args[-1])",
            ])
        else:
            script.extend([
                f"output = Path(args[args.index({output_flag!r}) + 1])",
            ])
        script.extend([
            "output.parent.mkdir(parents=True, exist_ok=True)",
            "output.write_bytes(version.encode('ascii'))",
        ])
        tool.write_text("\n".join(script) + "\n", encoding="utf-8")
        tool.chmod(0o755)
        return tool

    fluidsynth = fake_tool("fake-fluidsynth", "fake-fluidsynth 1.0", "-F")
    ffmpeg = fake_tool("fake-ffmpeg", "fake-ffmpeg 1.0")
    result = render_audio_bundle(
        _manifest(),
        _midi_records(tmp_path),
        tmp_path / "audio",
        soundfont=soundfont,
        seed=7,
        fluidsynth_bin=str(fluidsynth),
        ffmpeg_bin=str(ffmpeg),
    )

    record = result["records"]["0"]
    assert result["renderer"]["version"] == "fake-fluidsynth 1.0"
    assert result["mixer"]["version"] == "fake-ffmpeg 1.0"
    assert Path(record["stems"]["chords"]["path"]).is_file()
    assert Path(record["mix"]["path"]).is_file()


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX executable wrappers")
def test_audio_bundle_parallel_multiple_songs(tmp_path):
    soundfont = tmp_path / "reference.sf2"
    soundfont.write_bytes(b"soundfont")

    def fake_tool(name, version, output_flag=None):
        tool = tmp_path / name
        script = [
            "#!/usr/bin/env python3",
            "from pathlib import Path",
            "import sys",
            "args = sys.argv[1:]",
            f"version = {version!r}",
            "if args and args[0] in {'--version', '-version'}:",
            "    print(version)",
            "    raise SystemExit(0)",
        ]
        if output_flag is None:
            script.extend([
                "output = Path(args[-1])",
            ])
        else:
            script.extend([
                f"output = Path(args[args.index({output_flag!r}) + 1])",
            ])
        script.extend([
            "output.parent.mkdir(parents=True, exist_ok=True)",
            "output.write_bytes(version.encode('ascii'))",
        ])
        tool.write_text("\n".join(script) + "\n", encoding="utf-8")
        tool.chmod(0o755)
        return tool

    fluidsynth = fake_tool("fake-fluidsynth", "fake-fluidsynth 1.0", "-F")
    ffmpeg = fake_tool("fake-ffmpeg", "fake-ffmpeg 1.0")

    manifest = {
        "seed": 7,
        "selected_roles": ["V0", "V1"],
        "records": [
            {"ordinal": 0, "selected_roles": ["V0", "V1"]},
            {"ordinal": 1, "selected_roles": ["V0", "V1"]},
        ],
    }
    midi_records = {
        "mixed": {
            "0": {"path": str(tmp_path / "0.mid")},
            "1": {"path": str(tmp_path / "1.mid")},
        }
    }
    (tmp_path / "0.mid").write_bytes(b"0")
    (tmp_path / "1.mid").write_bytes(b"1")

    result = render_audio_bundle(
        manifest,
        midi_records,
        tmp_path / "audio",
        soundfont=soundfont,
        seed=7,
        fluidsynth_bin=str(fluidsynth),
        ffmpeg_bin=str(ffmpeg),
    )

    assert "0" in result["records"]
    assert "1" in result["records"]
    assert Path(result["records"]["0"]["mix"]["path"]).is_file()
    assert Path(result["records"]["1"]["mix"]["path"]).is_file()


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX executable wrappers")
def test_mix_audio_bundle_from_stems(tmp_path):
    def fake_ffmpeg():
        tool = tmp_path / "fake-ffmpeg"
        script = [
            "#!/usr/bin/env python3",
            "from pathlib import Path",
            "import sys",
            "args = sys.argv[1:]",
            "if args and args[0] in {'--version', '-version'}:",
            "    print('fake-ffmpeg 1.0')",
            "    raise SystemExit(0)",
            "output = Path(args[-1])",
            "output.parent.mkdir(parents=True, exist_ok=True)",
            "output.write_bytes(b'mixed-audio')",
        ]
        tool.write_text("\n".join(script) + "\n", encoding="utf-8")
        tool.chmod(0o755)
        return tool

    ffmpeg = fake_ffmpeg()

    stems = {}
    for track in ("chords", "bass", "melody", "percussion"):
        stem_file = tmp_path / f"{track}.flac"
        stem_file.write_bytes(f"audio-{track}".encode())
        stems[track] = {"path": str(stem_file), "sha256": "dummy"}

    manifest = {
        "seed": 7,
        "selected_roles": ["V0", "V1"],
        "records": [
            {"ordinal": 0, "selected_roles": ["V0", "V1"]},
            {"ordinal": 1, "selected_roles": ["V0", "V1", "V9"]},
        ],
    }
    stems_records = {
        "0": stems,
        "1": stems,
    }

    result = mix_audio_bundle_from_stems(
        manifest,
        stems_records,
        tmp_path / "condition_audio",
        seed=7,
        ffmpeg_bin=str(ffmpeg),
        renderer_info={"engine": "FluidSynth", "version": "test"},
        asset_info={"asset_id": "test-sf2"},
    )

    assert result["renderer"]["version"] == "test"
    assert result["asset"]["asset_id"] == "test-sf2"
    rec0 = result["records"]["0"]
    assert set(rec0["stems"]) == {"chords", "bass"}
    assert Path(rec0["mix"]["path"]).is_file()
    assert rec0["selected_roles"] == ["V0", "V1"]

    rec1 = result["records"]["1"]
    assert set(rec1["stems"]) == {"chords", "bass", "percussion"}
    assert Path(rec1["mix"]["path"]).is_file()
    assert rec1["selected_roles"] == ["V0", "V1", "V9"]


def test_audio_bundle_skips_mix_when_render_mix_false(tmp_path, monkeypatch):
    soundfont = tmp_path / "reference.sf2"
    soundfont.write_bytes(b"soundfont")
    mix_called = []

    monkeypatch.setattr(
        audio_render,
        "_resolve_executable",
        lambda binary, label: f"/usr/bin/{binary}",
    )
    monkeypatch.setattr(
        audio_render,
        "_tool_version",
        lambda executable, argument: f"{executable} test",
    )

    def render_midi(executable, midi_path, wav_path, soundfont_path, sample_rate):
        wav_path.write_bytes(b"wav:" + midi_path.read_bytes())

    def encode(executable, wav_path, flac_path, profile):
        flac_path.write_bytes(b"flac:" + wav_path.read_bytes())

    def mix(executable, wav_paths, flac_path, profile):
        mix_called.append(flac_path)
        flac_path.write_bytes(b"mix")

    monkeypatch.setattr(audio_render, "_render_midi_to_wav", render_midi)
    monkeypatch.setattr(audio_render, "_encode_flac", encode)
    monkeypatch.setattr(audio_render, "_mix_flac", mix)

    result = render_audio_bundle(
        _manifest(),
        _midi_records(tmp_path),
        tmp_path / "audio",
        soundfont=soundfont,
        seed=7,
        render_mix=False,
    )

    record = result["records"]["0"]
    assert set(record["stems"]) == {"bass", "chords", "percussion"}
    assert "mix" not in record
    assert len(mix_called) == 0
    assert not (tmp_path / "audio" / "song_0" / "mix.flac").exists()


def test_profile_validation_and_s16_flag(tmp_path, monkeypatch):
    captured = {}

    def run(command, *args, **kwargs):
        captured["command"] = command

    monkeypatch.setattr(audio_render.subprocess, "run", run)

    # Validate mono 16-bit 22.05kHz profile passes validation
    profile_16bit = dict(REFERENCE_RENDER_PROFILE)
    profile_16bit["channels"] = 1
    profile_16bit["bit_depth"] = 16
    profile_16bit["sample_rate_hz"] = 22050
    validated = audio_render._validate_profile(profile_16bit)
    assert validated["channels"] == 1
    assert validated["bit_depth"] == 16

    # Verify _encode_flac uses -sample_fmt s16
    audio_render._encode_flac(
        "ffmpeg",
        tmp_path / "in.wav",
        tmp_path / "out.flac",
        profile_16bit,
    )
    cmd = " ".join(captured["command"])
    assert "-sample_fmt s16" in cmd
    assert "-ar 22050" in cmd
    assert "-ac 1" in cmd

    # Verify _mix_flac uses -sample_fmt s16
    audio_render._mix_flac(
        "ffmpeg",
        [("chords", tmp_path / "chords.wav")],
        tmp_path / "mix.flac",
        profile_16bit,
    )
    mix_cmd = " ".join(captured["command"])
    assert "-sample_fmt s16" in mix_cmd
    assert "-ar 22050" in mix_cmd
    assert "-ac 1" in mix_cmd


