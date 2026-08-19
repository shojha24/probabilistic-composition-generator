"""
Integration Examples & Testing Guide for chord_gen.py

This file shows:
1. How to wire chord_gen with a runner script
2. How to integrate with Stage 4 (voicer)
3. Unit test examples
4. Debugging utilities
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
import numpy as np

from chord_gen import (
    ChordGenerator, GenreModel, GenerationState, HyperParams,
    load_genre_model, generate_chord_sequence,
    backoff_sample_stage1, backoff_sample_stage2,
    select_extension_trie, walk_trie_with_backoff
)


# ============================================================================
# PART 1: INTEGRATION WITH RUNNER SCRIPT
# ============================================================================

class ChordGenerationRunner:
    """
    High-level runner that orchestrates generation from CLI args or config.
    
    This is the typical integration point with a main script.
    """
    
    def __init__(
        self,
        genre: str = "pop_rock",
        dist_dir: str = "distributions",
        output_dir: str = "gen/labels",
        seed: Optional[int] = None
    ):
        """Initialize runner with config."""
        self.genre = genre
        self.dist_dir = dist_dir
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.generator = ChordGenerator(
            genre=genre,
            dist_dir=dist_dir,
            seed=seed
        )
    
    def generate_and_save(
        self,
        num_songs: int = 5,
        num_chords: int = 48,
        temperature: Optional[float] = None,
        self_transition_discount: Optional[float] = None
    ) -> List[Path]:
        """
        Generate multiple songs and save as JSON.
        
        Returns:
            List of output file paths
        """
        output_files = []
        
        for song_idx in range(num_songs):
            # Generate
            chords = self.generator.generate_song(
                num_chords=num_chords,
                temperature=temperature,
                self_transition_discount=self_transition_discount
            )
            
            # Save
            filename = self.output_dir / f"{self.genre}_song_{song_idx:03d}.json"
            with open(filename, "w") as f:
                json.dump(chords, f, indent=2)
            
            output_files.append(filename)
            print(f"Saved: {filename}")
        
        return output_files


# Example usage:
def example_runner():
    """Example: how to use ChordGenerationRunner in a main script."""
    
    runner = ChordGenerationRunner(
        genre="jazz",
        dist_dir="distributions/",
        output_dir="output/jazz_chords",
        seed=42
    )
    
    # Generate 10 songs with custom parameters
    files = runner.generate_and_save(
        num_songs=10,
        num_chords=64,
        temperature=1.1,
        self_transition_discount=0.8
    )
    
    print(f"Generated {len(files)} files")


# ============================================================================
# PART 2: INTEGRATION WITH STAGE 4 (VOICER)
# ============================================================================

class VoicerAdapter:
    """
    Adapter to connect chord_gen output (Stages 1–3) to Stage 4 (voicer).
    
    Stage 4 is expected to be in a separate module that:
    - Takes a sequence of symbolic chords
    - Returns rendered voicings (concrete MIDI pitches)
    """
    
    def __init__(self, voicer_module):
        """
        Initialize with a voicer module.
        
        Args:
            voicer_module: Module with enumerate_voicings() and softmax_sample()
        """
        self.voicer = voicer_module
    
    def render_to_voicings(
        self,
        chords: List[Dict[str, Any]],
        register_range: tuple = (36, 84),  # MIDI low/high
        temperature: float = 1.0
    ) -> List[List[int]]:
        """
        Convert symbolic chords to concrete voicings.
        
        Args:
            chords: Output from generate_chord_sequence()
            register_range: (lowest_midi_pitch, highest_midi_pitch)
            temperature: Softmax temperature for voicing selection
        
        Returns:
            List of voicings, one per chord event
            Each voicing is a list of MIDI pitch integers
        """
        
        voicings = []
        prev_voicing = None
        
        for chord in chords:
            # Extract symbolic info
            root = chord["root"]
            triad = chord["triad"]
            bass = chord["bass"]
            extensions = {
                "seventh": chord["seventh"],
                "ninth": chord["ninth"],
                "eleventh": chord["eleventh"],
                "thirteenth": chord["thirteenth"]
            }
            
            # Stage 4a: Enumerate candidates
            candidates = self.voicer.enumerate_voicings(
                root=root,
                triad=triad,
                bass=bass,
                extensions=extensions,
                register_range=register_range
            )
            
            if not candidates:
                # Fallback: trivial voicing (root, third, fifth, bass)
                candidates = [[root, root + 4, root + 7, bass]]
            
            # Stage 4b: Rank by voice-leading distance
            scored = [
                (voicing, self.voicer.distance(voicing, prev_voicing))
                for voicing in candidates
            ]
            
            # Stage 4c: Softmax sample
            voicing = self.voicer.softmax_sample(scored, temperature=temperature)
            voicings.append(voicing)
            prev_voicing = voicing
        
        return voicings
    
    def render_full_pipeline(
        self,
        model: GenreModel,
        num_chords: int = 48,
        hyperparams: Optional[HyperParams] = None,
        rng: Optional[np.random.Generator] = None,
        temperature: float = 1.0,
        self_transition_discount: float = 1.0
    ) -> Dict[str, Any]:
        """
        End-to-end pipeline: Stages 1–3 + Stage 4.
        
        Returns:
            {
                "symbolic_chords": [...],  # From Stage 3
                "voicings": [...]          # From Stage 4
            }
        """
        
        # Stages 1–3
        symbolic_chords = generate_chord_sequence(
            model=model,
            num_chords=num_chords,
            hyperparams=hyperparams,
            rng=rng,
            generation_temperature=temperature,
            self_transition_discount=self_transition_discount
        )
        
        # Stage 4
        voicings = self.render_to_voicings(
            symbolic_chords,
            temperature=temperature
        )
        
        return {
            "symbolic_chords": symbolic_chords,
            "voicings": voicings
        }


# Example usage (assuming voicer module exists):
def example_voicer_integration():
    """
    Example: how to wire chord_gen with a voicer.
    
    This assumes `stage4_voicer` module with required functions.
    """
    # import stage4_voicer as voicer
    # 
    # model = load_genre_model("pop_rock")
    # adapter = VoicerAdapter(voicer)
    # 
    # result = adapter.render_full_pipeline(
    #     model,
    #     num_chords=48,
    #     temperature=1.0
    # )
    # 
    # symbolic = result["symbolic_chords"]
    # voicings = result["voicings"]
    pass


# ============================================================================
# PART 3: UNIT TESTS
# ============================================================================

class ChordGenTestSuite:
    """Unit test examples for chord_gen.py."""
    
    @staticmethod
    def test_load_genre_model():
        """Test that model loading works."""
        try:
            model = load_genre_model("pop_rock", "distributions/")
            assert model.genre == "pop_rock"
            assert len(model.stage1_level0) > 0
            assert len(model.stage2_level0) > 0
            print("✓ test_load_genre_model passed")
        except FileNotFoundError:
            print("⊘ test_load_genre_model skipped (distributions/ not found)")
        except AssertionError as e:
            print(f"✗ test_load_genre_model failed: {e}")
    
    @staticmethod
    def test_stage1_sampling():
        """Test Stage 1 chord sampling."""
        try:
            model = load_genre_model("pop_rock", "distributions/")
            state = GenerationState()
            state.init_sequence()
            
            hp = HyperParams()
            rng = np.random.default_rng(42)
            
            # Sample a few chords
            for _ in range(10):
                root, triad = backoff_sample_stage1(
                    model, state, hp, rng
                )
                
                # Verify output
                assert isinstance(root, (int, np.integer)), f"root {root} not int"
                assert 0 <= root <= 11, f"root {root} out of range"
                assert isinstance(triad, str), f"triad {triad} not str"
                assert triad in ["major", "minor", "diminished", "augmented", "sus4", "sus2", "5"]
                
                # Update state for next iteration
                state.root_t_minus_2 = state.root_t_minus_1
                state.triad_t_minus_2 = state.triad_t_minus_1
                state.root_t_minus_1 = root
                state.triad_t_minus_1 = triad
            
            print("✓ test_stage1_sampling passed")
        except FileNotFoundError:
            print("⊘ test_stage1_sampling skipped")
        except AssertionError as e:
            print(f"✗ test_stage1_sampling failed: {e}")
    
    @staticmethod
    def test_stage2_sampling():
        """Test Stage 2 bass sampling."""
        try:
            model = load_genre_model("pop_rock", "distributions/")
            state = GenerationState()
            state.init_sequence()
            
            hp = HyperParams()
            rng = np.random.default_rng(42)
            
            # Sample bass for various roots
            for root in [0, 5, 7]:
                bass = backoff_sample_stage2(model, root, state, hp, rng)
                
                # Verify output
                assert isinstance(bass, (int, np.integer)), f"bass {bass} not int"
                assert 0 <= bass <= 11, f"bass {bass} out of range"
                
                state.bass_t_minus_1 = bass
            
            print("✓ test_stage2_sampling passed")
        except FileNotFoundError:
            print("⊘ test_stage2_sampling skipped")
        except AssertionError as e:
            print(f"✗ test_stage2_sampling failed: {e}")
    
    @staticmethod
    def test_stage3_trie_selection():
        """Test Stage 3 trie selection."""
        try:
            model = load_genre_model("jazz", "distributions/")
            hp = HyperParams()
            
            # Select trie for a few chords
            for root in [0, 5, 7]:
                for triad in ["major", "minor"]:
                    trie = select_extension_trie(model, root, triad, hp)
                    
                    # Verify it's a dict
                    assert isinstance(trie, dict)
                    # Should have ROOT key or be fallback
                    # (exact structure depends on data)
            
            print("✓ test_stage3_trie_selection passed")
        except FileNotFoundError:
            print("⊘ test_stage3_trie_selection skipped")
        except AssertionError as e:
            print(f"✗ test_stage3_trie_selection failed: {e}")
    
    @staticmethod
    def test_full_generation():
        """Test full chord sequence generation."""
        try:
            model = load_genre_model("pop_rock", "distributions/")
            hp = HyperParams()
            rng = np.random.default_rng(42)
            
            chords = generate_chord_sequence(
                model,
                num_chords=16,
                hyperparams=hp,
                rng=rng,
                generation_temperature=1.0,
                self_transition_discount=1.0
            )
            
            # Verify output structure
            assert len(chords) == 16, f"Expected 16 chords, got {len(chords)}"
            
            for i, chord in enumerate(chords):
                assert "root" in chord
                assert "triad" in chord
                assert "bass" in chord
                assert "seventh" in chord
                assert "ninth" in chord
                assert "eleventh" in chord
                assert "thirteenth" in chord
                assert "timestep" in chord
                assert chord["timestep"] == i
                
                # Verify value ranges
                assert 0 <= chord["root"] <= 11
                assert 0 <= chord["bass"] <= 11
                assert chord["seventh"] in ["N", "7", "b7", "bb7"]
                assert chord["ninth"] in ["N", "9", "b9", "#9"]
            
            print("✓ test_full_generation passed")
        except FileNotFoundError:
            print("⊘ test_full_generation skipped")
        except AssertionError as e:
            print(f"✗ test_full_generation failed: {e}")
    
    @staticmethod
    def test_reproducibility():
        """Test that same seed gives same output."""
        try:
            model = load_genre_model("pop_rock", "distributions/")
            hp = HyperParams()
            
            # Generate twice with same seed
            chords1 = generate_chord_sequence(
                model, 32, hp, rng=np.random.default_rng(999)
            )
            chords2 = generate_chord_sequence(
                model, 32, hp, rng=np.random.default_rng(999)
            )
            
            # Should be identical
            for c1, c2 in zip(chords1, chords2):
                assert c1 == c2, f"Chords differ: {c1} vs {c2}"
            
            print("✓ test_reproducibility passed")
        except FileNotFoundError:
            print("⊘ test_reproducibility skipped")
        except AssertionError as e:
            print(f"✗ test_reproducibility failed: {e}")
    
    @staticmethod
    def test_temperature_effect():
        """Test that temperature changes output."""
        try:
            model = load_genre_model("jazz", "distributions/")
            hp = HyperParams()
            rng1 = np.random.default_rng(111)
            rng2 = np.random.default_rng(111)
            
            # Generate with different temperatures
            chords_low_temp = generate_chord_sequence(
                model, 32, hp, rng=rng1, generation_temperature=0.5
            )
            chords_high_temp = generate_chord_sequence(
                model, 32, hp, rng=rng2, generation_temperature=2.0
            )
            
            # They should typically differ (with high probability)
            # (not guaranteed, but very likely)
            differences = sum(
                1 for c1, c2 in zip(chords_low_temp, chords_high_temp)
                if c1["root"] != c2["root"] or c1["triad"] != c2["triad"]
            )
            
            # If they're identical, temperature likely has no effect (suspicious)
            if differences == 0:
                print("⚠ test_temperature_effect: no differences detected (temperature may be ineffective)")
            else:
                print(f"✓ test_temperature_effect passed ({differences} differences in {len(chords_low_temp)} chords)")
        
        except FileNotFoundError:
            print("⊘ test_temperature_effect skipped")
        except Exception as e:
            print(f"✗ test_temperature_effect failed: {e}")


# ============================================================================
# PART 4: DEBUGGING UTILITIES
# ============================================================================

class ChordGenDebugger:
    """Utilities for debugging and introspecting generation."""
    
    @staticmethod
    def analyze_chord_sequence(chords: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze statistical properties of a generated chord sequence.
        
        Returns:
            {
                "num_chords": int,
                "unique_roots": set,
                "root_counts": dict,
                "triad_distribution": dict,
                "bass_distribution": dict,
                "extension_cooccurrence": dict,
                ...
            }
        """
        
        analysis = {
            "num_chords": len(chords),
            "unique_roots": set(),
            "root_counts": {},
            "triad_counts": {},
            "bass_counts": {},
            "seventh_counts": {},
            "extension_tuples": {}
        }
        
        for chord in chords:
            # Roots
            root = chord["root"]
            analysis["unique_roots"].add(root)
            analysis["root_counts"][root] = analysis["root_counts"].get(root, 0) + 1
            
            # Triads
            triad = chord["triad"]
            analysis["triad_counts"][triad] = analysis["triad_counts"].get(triad, 0) + 1
            
            # Bass
            bass = chord["bass"]
            bass_key = f"root" if bass == 0 else f"inv_{bass}"
            analysis["bass_counts"][bass_key] = analysis["bass_counts"].get(bass_key, 0) + 1
            
            # 7ths
            seventh = chord["seventh"]
            analysis["seventh_counts"][seventh] = analysis["seventh_counts"].get(seventh, 0) + 1
            
            # Extension tuple
            ext_tuple = (
                chord["seventh"],
                chord["ninth"],
                chord["eleventh"],
                chord["thirteenth"]
            )
            analysis["extension_tuples"][ext_tuple] = \
                analysis["extension_tuples"].get(ext_tuple, 0) + 1
        
        return analysis
    
    @staticmethod
    def print_analysis(analysis: Dict[str, Any]) -> None:
        """Pretty-print chord sequence analysis."""
        print(f"\n--- Chord Sequence Analysis ---")
        print(f"Total chords: {analysis['num_chords']}")
        print(f"Unique roots: {sorted(analysis['unique_roots'])}")
        
        print(f"\nRoot distribution:")
        for root in sorted(analysis["root_counts"].keys()):
            count = analysis["root_counts"][root]
            pct = 100 * count / analysis["num_chords"]
            print(f"  {root:2d}: {count:3d} ({pct:5.1f}%)")
        
        print(f"\nTriad distribution:")
        for triad in sorted(analysis["triad_counts"].keys()):
            count = analysis["triad_counts"][triad]
            pct = 100 * count / analysis["num_chords"]
            print(f"  {triad:10s}: {count:3d} ({pct:5.1f}%)")
        
        print(f"\nBass distribution:")
        for bass_type in sorted(analysis["bass_counts"].keys()):
            count = analysis["bass_counts"][bass_type]
            pct = 100 * count / analysis["num_chords"]
            print(f"  {bass_type:10s}: {count:3d} ({pct:5.1f}%)")
        
        print(f"\n7th distribution:")
        for seventh in sorted(analysis["seventh_counts"].keys()):
            count = analysis["seventh_counts"][seventh]
            pct = 100 * count / analysis["num_chords"]
            print(f"  {seventh:3s}: {count:3d} ({pct:5.1f}%)")
        
        print(f"\nTop 5 extension tuples:")
        sorted_exts = sorted(
            analysis["extension_tuples"].items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        for ext_tuple, count in sorted_exts:
            pct = 100 * count / analysis["num_chords"]
            print(f"  {ext_tuple}: {count:3d} ({pct:5.1f}%)")
    
    @staticmethod
    def verify_chord_validity(chords: List[Dict[str, Any]]) -> List[str]:
        """
        Check for validity issues in a chord sequence.
        
        Returns:
            List of warning/error messages (empty if all OK)
        """
        issues = []
        
        for i, chord in enumerate(chords):
            # Check types
            if not isinstance(chord.get("root"), (int, np.integer)):
                issues.append(f"Chord {i}: root is not int: {chord['root']}")
            if not isinstance(chord.get("triad"), str):
                issues.append(f"Chord {i}: triad is not str: {chord['triad']}")
            
            # Check ranges
            if not (0 <= chord.get("root", -1) <= 11):
                issues.append(f"Chord {i}: root out of range: {chord['root']}")
            if not (0 <= chord.get("bass", -1) <= 11):
                issues.append(f"Chord {i}: bass out of range: {chord['bass']}")
            
            # Check enum values
            valid_triads = {"major", "minor", "diminished", "augmented", "sus4", "sus2", "5"}
            if chord.get("triad") not in valid_triads:
                issues.append(f"Chord {i}: unknown triad: {chord['triad']}")
            
            # Check extensions
            for ext_slot in ["seventh", "ninth", "eleventh", "thirteenth"]:
                ext_val = chord.get(ext_slot, "?")
                if ext_val not in ["N", "7", "b7", "bb7", "9", "b9", "#9", "11", "#11", "13", "b13"]:
                    issues.append(f"Chord {i}: invalid {ext_slot}: {ext_val}")
        
        return issues


# ============================================================================
# MAIN: RUN TESTS & EXAMPLES
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("CHORD_GEN INTEGRATION & TESTING")
    print("=" * 60)
    
    # Run test suite
    print("\n--- Running Unit Tests ---")
    suite = ChordGenTestSuite()
    suite.test_load_genre_model()
    suite.test_stage1_sampling()
    suite.test_stage2_sampling()
    suite.test_stage3_trie_selection()
    suite.test_full_generation()
    suite.test_reproducibility()
    suite.test_temperature_effect()
    
    # Example: analyze a generated sequence
    print("\n--- Generating Example Sequence & Analyzing ---")
    try:
        gen = ChordGenerator(genre="pop_rock", seed=42)
        chords = gen.generate_song(num_chords=64)
        
        analysis = ChordGenDebugger.analyze_chord_sequence(chords)
        ChordGenDebugger.print_analysis(analysis)
        
        issues = ChordGenDebugger.verify_chord_validity(chords)
        if issues:
            print("\n⚠ Validity Issues:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("\n✓ All chords valid")
    
    except FileNotFoundError as e:
        print(f"Note: {e}")
        print("To run examples, first generate distributions/ via extract_distributions.py")
    
    print("\n" + "=" * 60)
