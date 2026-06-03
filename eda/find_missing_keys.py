from pathlib import Path
import jams


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
OUTPUT_FILE = ROOT_DIR / "missing_key_mode.txt"


def has_key_mode_annotation(jams_path: Path) -> bool:
    try:
        jam = jams.load(str(jams_path), validate=False)
    except Exception:
        return False

    for annotation in jam.annotations:
        if annotation.namespace == "key_mode":
            return True

    return False


def main() -> None:
    missing_paths = []

    for i, jams_path in enumerate(sorted(DATA_DIR.rglob("*.jams"))):
        if not has_key_mode_annotation(jams_path):
            relative_path = jams_path.relative_to(ROOT_DIR).as_posix()
            missing_paths.append(f"{jams_path.name}\t{relative_path}")
        
        if (i + 1) % 100 == 0:
            print(f"Checked {i + 1} files...")

    OUTPUT_FILE.write_text("\n".join(missing_paths) + ("\n" if missing_paths else ""), encoding="utf-8")
    print(f"Wrote {len(missing_paths)} missing paths to {OUTPUT_FILE.name}")


if __name__ == "__main__":
    main()
