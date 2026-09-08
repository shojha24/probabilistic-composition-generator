import json
import hashlib

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
