"""Versioned sound-design contracts shared by corpus rendering stages."""
from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA_VERSION = 1
SEED_DERIVATION_VERSION = "fnv1a64-v1"
CONDITION_ROLES = {
    "cb": ("V0", "V1"),
    "cbp": ("V0", "V1", "V9"),
    "cbm": ("V0", "V1", "V2"),
    "cbmp": ("V0", "V1", "V2", "V9"),
    "naturalistic": ("V0", "V1", "V2", "V9"),
}
REFERENCE_RENDER_PROFILE = {
    "profile_id": "reference-transparent-v1",
    "reproducibility_class": "canonical",
    "format": "flac",
    "sample_rate_hz": 44100,
    "bit_depth": 24,
    "channels": 2,
    "start_padding_frames": 0,
    "tail_policy": "preserve_release",
    "loudness_target_lufs": -18.0,
    "true_peak_ceiling_dbtp": -1.0,
    "role_gains_db": {"V0": 0.0, "V1": 3.0, "V2": -1.0, "V9": -6.0},
}
ASSET_REGISTRY = {
    "general-midi-fallback-v1": {
        "asset_id": "general-midi-fallback-v1",
        "release": "general-midi-programs",
        "license": "renderer-dependent; non-audio symbolic fallback",
        "renderer": "General MIDI",
        "reproducibility_class": "canonical",
    }
}


def derive_seed(root_seed: int, domain: str, ordinal: int) -> int:
    """Derive a stable Java-compatible non-negative seed."""
    if isinstance(root_seed, bool) or not isinstance(root_seed, int):
        raise ValueError("root_seed must be an integer")
    payload = f"{root_seed}:{domain}:{ordinal}".encode("utf-8")
    value = 0xcbf29ce484222325
    for byte in payload:
        value ^= byte
        value = (value * 0x100000001b3) & ((1 << 64) - 1)
    return value & ((1 << 63) - 1)


def config_sha256(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def parameter_manifest(
    *,
    config: dict[str, Any],
    source_manifests: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the closed-world portion of a render manifest."""
    return {
        "schema_version": SCHEMA_VERSION,
        "completeness": "closed_world",
        "effective_config_sha256": config_sha256(config),
        "unresolved_parameters": [],
        "defaulted_parameters": [],
        "source_manifests": source_manifests,
        "parameter_sources": {
            key: "cli" for key in sorted(config)
        },
    }
