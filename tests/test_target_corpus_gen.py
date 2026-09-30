"""
Unit tests for quota-aware target corpus generator updates:
1. 70/30 genre split
2. Updated rare extension quotas (including b7)
3. Updated triad quotas
4. Linear quota scaling
5. Altered extension conditioning and co-occurrence
"""

import pytest
from target_corpus_gen import (
    DEFAULT_POP_ROCK_RATIO,
    EXTENSION_TARGET_SLOTS,
    ALTERED_EXTENSION_PREFERRED_7TH,
    RARE_EXTENSION_TARGETS,
    RARE_TRIAD_TARGETS,
    DENSE_EXTENSION_TARGETS,
    TARGET_EVENTS_BY_GENRE,
    GenerationQuota,
    TargetChordGenerator,
    generate_target_corpus,
    walk_extension_trie_target,
)


def test_genre_split_defaults():
    assert DEFAULT_POP_ROCK_RATIO == 0.70
    assert TARGET_EVENTS_BY_GENRE["pop_rock"] == 175_000
    assert TARGET_EVENTS_BY_GENRE["jazz"] == 75_000


def test_b7_quota_and_extension_slots():
    assert "b7" in EXTENSION_TARGET_SLOTS
    assert EXTENSION_TARGET_SLOTS["b7"] == "seventh"
    assert "b7" in RARE_EXTENSION_TARGETS["pop_rock"]
    assert "b7" in RARE_EXTENSION_TARGETS["jazz"]
    assert RARE_EXTENSION_TARGETS["pop_rock"]["b7"] == 50_000
    assert RARE_EXTENSION_TARGETS["jazz"]["b7"] == 40_000


def test_triad_quotas_updated():
    assert RARE_TRIAD_TARGETS["pop_rock"]["sus2"] == 3_500
    assert RARE_TRIAD_TARGETS["pop_rock"]["sus4"] == 6_000
    assert RARE_TRIAD_TARGETS["pop_rock"]["augmented"] == 1_200
    assert RARE_TRIAD_TARGETS["pop_rock"]["diminished"] == 2_500


def test_linear_quota_scaling():
    # Test scaled for double reference
    quota_2x = GenerationQuota.for_genre("pop_rock", 350_000)
    assert quota_2x.extension_targets["b7"] == 100_000
    assert quota_2x.triad_targets["sus2"] == 7_000

    # Test scaled for smaller batch
    quota_small = GenerationQuota.for_genre("pop_rock", 1_750)
    assert quota_small.extension_targets["b7"] == 500
    assert quota_small.triad_targets["sus2"] == 35


def test_altered_extension_conditioning():
    import random
    gen = TargetChordGenerator(genre="pop_rock", tonic_pc=0, num_chords=50, bpm=120, seed=42)
    trie = gen.stage3["trie_B"]["major"]
    rng = random.Random(42)

    # When targeting #9 on major triad, preferred 7th should be b7
    for _ in range(10):
        tup = walk_extension_trie_target(trie, "0", "N|N|N|N", "ninth", "#9", gen.params, rng)
        assert tup[0] == "b7"
        assert tup[1] == "#9"

    # b13 on augmented triad should not be attested / allowed
    assert gen._target_extension_trie("0_augmented", "augmented", "b13") is None


def test_batch_generation_meets_quotas():
    # Generate 14 pop-rock and 6 jazz songs
    results = generate_target_corpus(
        events_by_genre={"pop_rock": 700, "jazz": 300},
        songs_by_genre={"pop_rock": 14, "jazz": 6},
        seed=123,
    )
    assert len(results["pop_rock"]) == 14
    assert len(results["jazz"]) == 6
    assert sum(len(s) for s in results["pop_rock"]) == 700
    assert sum(len(s) for s in results["jazz"]) == 300
