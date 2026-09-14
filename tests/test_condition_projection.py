import json
import hashlib
from pathlib import Path

from sound_design import CONDITION_ROLES
from tools.project_condition_corpus import project_conditions


def _blocks(lines):
    return "".join(
        f"START_SONG_{ordinal}\n{line}\nEND_SONG\n"
        for ordinal, line in enumerate(lines)
    )


def _block(ordinal, line):
    return f"START_SONG_{ordinal}\n{line}\nEND_SONG\n"


def test_condition_projection_reuses_canonical_roles(tmp_path):
    canonical = tmp_path / "canonical.txt"
    role_lines = {
        "chords": ["V0 I0 C4q", "V0 I0 D4q"],
        "bass": ["V1 I34 C2q", "V1 I34 D2q"],
        "melody": ["V2 I73 G4q", "V2 I73 A4q"],
        "percussion": ["V9 I0 C3q", "V9 I0 D3q"],
    }
    role_paths = {}
    for role, lines in role_lines.items():
        path = tmp_path / f"canonical_{role}.txt"
        text = _blocks(lines)
        path.write_text(text)
        role_paths[role] = str(path.resolve())
    canonical.write_text(
        _blocks([
            "  ".join(role_lines[role][0] for role in role_lines),
            "  ".join(role_lines[role][1] for role in role_lines),
        ])
    )
    records = []
    for ordinal in range(2):
        records.append({
            "ordinal": ordinal,
            "source_id": ordinal,
            "track_hashes": {
                role: hashlib.sha256(
                    _block(ordinal, lines[ordinal]).encode()
                ).hexdigest()
                for role, lines in role_lines.items()
            },
        })
    manifest = {
        "seed": 7,
        "output": str(canonical.resolve()),
        "track_outputs": {
            "paths": {
                "mixed": str(canonical.resolve()),
                **role_paths,
            },
        },
        "records": records,
        "parameter_manifest": {},
        "sound_design": {"stage": "score", "audio_rendered": False},
    }
    manifest_path = canonical.with_name(canonical.name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest))

    outputs = project_conditions(
        canonical,
        tmp_path / "conditions",
        conditions=("cb", "cbp", "cbm", "cbmp", "naturalistic"),
        seed=7,
        melody_percent=50,
        percussion_percent=50,
    )

    assert [path.parent.name for path in outputs] == [
        "cb",
        "cbp",
        "cbm",
        "cbmp",
        "naturalistic",
    ]
    canonical_chords = role_paths["chords"]
    for output, condition in zip(outputs, CONDITION_ROLES):
        manifest = json.loads(
            output.with_name(output.name + ".manifest.json").read_text()
        )
        assert manifest["condition_id"] == condition
        assert manifest["condition_projection"]["generation_reused"] is True
        assert manifest["condition_projection"]["symbolic_regeneration"] is False
        assert manifest["condition_projection"]["source_role_paths"]["chords"] == (
            canonical_chords
        )

    cb_text = outputs[0].read_text()
    cbp_text = outputs[1].read_text()
    cbm_text = outputs[2].read_text()
    cbmp_text = outputs[3].read_text()
    assert "V2" not in cb_text and "V9" not in cb_text
    assert "V2" not in cbp_text and "V9" in cbp_text
    assert "V2" in cbm_text and "V9" not in cbm_text
    assert "V2" in cbmp_text and "V9" in cbmp_text

    naturalistic_manifest = json.loads(
        outputs[4].with_name(
            outputs[4].name + ".manifest.json"
        ).read_text()
    )
    selection = naturalistic_manifest["condition_projection"]["selection"]
    assert selection["melody_target_count"] == 1
    assert selection["percussion_target_count"] == 1
    assert sum(
        record["role_presence"]["V2"]
        for record in naturalistic_manifest["records"]
    ) == 1
    assert sum(
        record["role_presence"]["V9"]
        for record in naturalistic_manifest["records"]
    ) == 1

    before = [output.read_bytes() for output in outputs]
    project_conditions(
        canonical,
        tmp_path / "conditions",
        conditions=("cb", "cbp", "cbm", "cbmp", "naturalistic"),
        seed=7,
        melody_percent=50,
        percussion_percent=50,
    )
    assert before == [output.read_bytes() for output in outputs]


def test_condition_projection_relocated_canonical_files(tmp_path):
    orig_dir = tmp_path / "orig"
    orig_dir.mkdir()
    new_dir = tmp_path / "relocated"
    new_dir.mkdir()

    role_lines = {
        "chords": ["V0 I0 C4q"],
        "bass": ["V1 I34 C2q"],
        "melody": ["V2 I73 G4q"],
        "percussion": ["V9 I0 C3q"],
    }
    for role, lines in role_lines.items():
        (new_dir / f"canonical_{role}.txt").write_text(_blocks(lines))

    canonical_new = new_dir / "canonical.txt"
    canonical_new.write_text(
        _blocks(["  ".join(role_lines[role][0] for role in role_lines)])
    )
    records = [{
        "ordinal": 0,
        "source_id": 0,
        "track_hashes": {
            role: hashlib.sha256(_block(0, lines[0]).encode()).hexdigest()
            for role, lines in role_lines.items()
        },
    }]
    # Manifest paths still point to nonexistent orig_dir
    manifest = {
        "seed": 42,
        "output": str(orig_dir / "canonical.txt"),
        "track_outputs": {
            "paths": {
                "mixed": str(orig_dir / "canonical.txt"),
                **{role: str(orig_dir / f"canonical_{role}.txt") for role in role_lines},
            },
        },
        "records": records,
        "parameter_manifest": {},
        "sound_design": {"stage": "score", "audio_rendered": False},
    }
    manifest_path = canonical_new.with_name(canonical_new.name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest))

    outputs = project_conditions(
        canonical_new,
        new_dir / "conditions",
        conditions=("cb", "naturalistic"),
        seed=42,
    )
    assert len(outputs) == 2
    proj_manifest = json.loads(
        outputs[0].with_name(outputs[0].name + ".manifest.json").read_text()
    )
    assert proj_manifest["condition_projection"]["source_role_paths"]["chords"] == str(
        (new_dir / "canonical_chords.txt").resolve()
    )


def test_condition_projection_audio_stage_mixes_from_stems(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical.txt"
    role_lines = {
        "chords": ["V0 I0 C4q", "V0 I0 D4q"],
        "bass": ["V1 I34 C2q", "V1 I34 D2q"],
        "melody": ["V2 I73 G4q", "V2 I73 A4q"],
        "percussion": ["V9 I0 C3q", "V9 I0 D3q"],
    }
    role_paths = {}
    for role, lines in role_lines.items():
        path = tmp_path / f"canonical_{role}.txt"
        text = _blocks(lines)
        path.write_text(text)
        role_paths[role] = str(path.resolve())
    canonical.write_text(
        _blocks([
            "  ".join(role_lines[role][0] for role in role_lines),
            "  ".join(role_lines[role][1] for role in role_lines),
        ])
    )
    records = []
    for ordinal in range(2):
        records.append({
            "ordinal": ordinal,
            "source_id": ordinal,
            "bpm": 120,
            "track_hashes": {
                role: hashlib.sha256(
                    _block(ordinal, lines[ordinal]).encode()
                ).hexdigest()
                for role, lines in role_lines.items()
            },
        })
    manifest = {
        "seed": 7,
        "output": str(canonical.resolve()),
        "track_outputs": {
            "paths": {
                "mixed": str(canonical.resolve()),
                **role_paths,
            },
        },
        "records": records,
        "parameter_manifest": {},
        "sound_design": {"stage": "score", "audio_rendered": False},
    }
    manifest_path = canonical.with_name(canonical.name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest))

    soundfont = tmp_path / "FluidR3_GM.sf2"
    soundfont.write_bytes(b"soundfont")

    # Track how many times MIDI rendering and audio rendering / mixing are called
    midi_scores_calls = []
    midi_bundle_calls = []

    def fake_render_midi_scores(score_path, output_dir, *, seed):
        midi_scores_calls.append((str(score_path), str(output_dir)))
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        res = {}
        for ord_val in range(2):
            mid = Path(output_dir) / f"START_SONG_{ord_val}.mid"
            mid.write_bytes(b"midi")
            res[str(ord_val)] = {"path": str(mid.resolve()), "sha256": "midhash"}
        return res

    def fake_render_midi_bundle(score_paths, output_dir, *, seed, tempo_by_ordinal=None):
        midi_bundle_calls.append(output_dir)
        res = {}
        for track in score_paths:
            res[track] = fake_render_midi_scores(score_paths[track], Path(output_dir) / track, seed=seed)
        return res

    log_file = tmp_path / "calls.log"

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
            f"with open({str(log_file)!r}, 'a') as log:",
            f"    log.write({name!r} + ' ' + ' '.join(args) + '\\n')",
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

    import render

    monkeypatch.setattr(render, "render_midi_scores", fake_render_midi_scores)
    monkeypatch.setattr(render, "render_midi_bundle", fake_render_midi_bundle)

    out_dir = tmp_path / "conditions"
    outputs = project_conditions(
        canonical,
        out_dir,
        conditions=("cb", "cbp", "cbm", "cbmp", "naturalistic"),
        seed=7,
        stage="audio",
        soundfont=soundfont,
        fluidsynth_bin=str(fluidsynth),
        ffmpeg_bin=str(ffmpeg),
    )

    assert len(outputs) == 5

    # FluidSynth stem rendering was only called for the canonical stems (4 tracks x 2 songs = 8 calls)
    # NOT 25+ calls from re-synthesizing each condition's mixed audio!
    synth_lines = [
        line for line in log_file.read_text().splitlines()
        if line.startswith("fake-fluidsynth") and "-F" in line
    ]
    assert len(synth_lines) == 8

    # Check that each condition output has its audio and midi
    for cond in ("cb", "cbp", "cbm", "cbmp", "naturalistic"):
        cond_dir = out_dir / cond
        manifest_file = cond_dir / "scores.txt.manifest.json"
        assert manifest_file.is_file()
        cond_manifest = json.loads(manifest_file.read_text())
        assert cond_manifest["sound_design"]["stage"] == "audio"
        assert cond_manifest["sound_design"]["audio_rendered"] is True

        # MIDI was generated for every song
        for ord_val in range(2):
            midi_file = cond_dir / "midi" / f"START_SONG_{ord_val}.mid"
            assert midi_file.is_file()
            mix_file = cond_dir / "audio" / f"song_{ord_val}" / "mix.flac"
            assert mix_file.is_file()

        # Check role presence in stems record
        rec0 = cond_manifest["records"][0]
        if cond == "cb":
            assert set(rec0["audio"]["stems"]) == {"chords", "bass"}
        elif cond == "cbp":
            assert set(rec0["audio"]["stems"]) == {"chords", "bass", "percussion"}
        elif cond == "cbm":
            assert set(rec0["audio"]["stems"]) == {"chords", "bass", "melody"}
        elif cond == "cbmp":
            assert set(rec0["audio"]["stems"]) == {"chords", "bass", "melody", "percussion"}

    # Verify that all condition mixes used the SAME shared stems from _stems
    cb_manifest = json.loads((out_dir / "cb" / "scores.txt.manifest.json").read_text())
    cbmp_manifest = json.loads((out_dir / "cbmp" / "scores.txt.manifest.json").read_text())
    assert (
        cb_manifest["records"][0]["audio"]["stems"]["chords"]["path"]
        == cbmp_manifest["records"][0]["audio"]["stems"]["chords"]["path"]
    )
    assert (
        cb_manifest["records"][0]["audio"]["stems"]["bass"]["path"]
        == cbmp_manifest["records"][0]["audio"]["stems"]["bass"]["path"]
    )

