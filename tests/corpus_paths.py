"""Shared generated-corpus path resolution for regression tests."""
import json
from pathlib import Path


GEN_DIR = Path(__file__).parents[1] / "gen"


def corpus_dir(name: str) -> Path:
    """Prefer the canonical corpus directory, then its target counterpart."""
    canonical = GEN_DIR / name
    if canonical.is_dir():
        return canonical
    target = GEN_DIR / f"target-{name}"
    if target.is_dir():
        return target
    raise FileNotFoundError(
        f"Neither corpus directory exists: {canonical} or {target}"
    )


def corpus_files(
    name: str,
    *,
    voicer_family: str | None = None,
) -> list[Path]:
    """Return song files, filtering target corpora by their family hint."""
    root = corpus_dir(name)
    paths = sorted(root.glob("song_*.json"))
    if voicer_family is None or not root.name.startswith("target-"):
        return paths
    return [
        path for path in paths
        if json.loads(path.read_text(encoding="utf-8")).get("voicer_family")
        == voicer_family
    ]
