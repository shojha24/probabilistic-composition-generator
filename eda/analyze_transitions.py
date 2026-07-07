import json
import numpy as np
from pathlib import Path

def analyze_level1(genre="pop_rock"):
    EDA_DIR = Path(__file__).resolve().parent
    DIST_DIR = EDA_DIR.parent / "distributions"
    file_path = DIST_DIR / f"level1_transitions_{genre}.json"
    
    with open(file_path, 'r') as f:
        data = json.load(f)
        
    probs = data['probabilities']
    states = sorted(probs.keys())
    n = len(states)
    
    # Convert to dense matrix
    matrix = np.zeros((n, n))
    outbound_counts = []
    
    for i, s1 in enumerate(states):
        transitions = probs[s1]
        outbound_counts.append(len(transitions))
        for s2, prob in transitions.items():
            if s2 in states:
                j = states.index(s2)
                matrix[i, j] = prob

    # Metrics
    zeros = np.sum(matrix == 0)
    total = n * n
    sparsity = (zeros / total) * 100
    
    print(f"--- LEVEL 1: TRANSITIONS ({genre.upper()}) ---")
    print(f"Total States: {n}")
    print(f"Matrix Sparsity: {sparsity:.2f}%")
    print(f"Avg Outbound Options per Chord: {np.mean(outbound_counts):.2f}")
    print(f"Min Outbound Options: {min(outbound_counts)}")
    print(f"Max Outbound Options: {max(outbound_counts)}")
    print("-" * 50)


def analyze_level2(genre="pop_rock", feature="extensions"):
    EDA_DIR = Path(__file__).resolve().parent
    DIST_DIR = EDA_DIR.parent / "distributions"
    file_path = DIST_DIR / f"level2_{feature}_{genre}.json"
    
    with open(file_path, 'r') as f:
        data = json.load(f)
        
    probs = data['probabilities']
    counts = data['counts'] # We need raw counts to check for statistical significance
    states = sorted(probs.keys())
    n = len(states)
    
    global_unique_options = set()
    options_per_state = []
    entropies = []
    state_totals = []
    
    for state in states:
        state_probs = probs[state]
        state_counts = counts[state]
        
        # Options count
        num_options = len(state_probs)
        options_per_state.append(num_options)
        
        # Global options
        global_unique_options.update(state_probs.keys())
        
        # Entropy calculation: H = -sum(p * log2(p))
        # 0 = perfectly predictable, higher = more uncertain/spread out
        entropy = -sum(p * np.log2(p) for p in state_probs.values() if p > 0)
        entropies.append(entropy)
        
        # Total counts for this state (how many times did this chord happen?)
        total_obs = sum(state_counts.values())
        state_totals.append(total_obs)

    # Global matrix sparsity (States vs Global Unique Options)
    m = len(global_unique_options)
    total_cells = n * m
    filled_cells = sum(options_per_state)
    sparsity = ((total_cells - filled_cells) / total_cells) * 100 if total_cells > 0 else 0

    # How many states are statistically "dangerous" to pull from?
    low_data_states = sum(1 for t in state_totals if t < 50)
    
    print(f"--- LEVEL 2: {feature.upper()} ({genre.upper()}) ---")
    print(f"Total Root/Triad States: {n}")
    print(f"Global Unique {feature.capitalize()}: {m}")
    print(f"Matrix Sparsity: {sparsity:.2f}%")
    print(f"Avg Options per State: {np.mean(options_per_state):.2f} (Min: {min(options_per_state)}, Max: {max(options_per_state)})")
    print(f"Avg Entropy (Bits): {np.mean(entropies):.2f}")
    print(f"States with < 50 observations: {low_data_states} out of {n} ({(low_data_states/n)*100:.1f}%)")
    
    if (low_data_states / n) > 0.3:
        print("WARNING: High number of low-data states! Generator WILL need a fallback/backoff dictionary.")
    print("-" * 50)


def main():
    print("=" * 50)
    print(" POP / ROCK ANALYSIS")
    print("=" * 50)
    analyze_level1("pop_rock")
    analyze_level2("pop_rock", "extensions")
    analyze_level2("pop_rock", "bass")
    print()
    
    print("=" * 50)
    print(" JAZZ ANALYSIS")
    print("=" * 50)
    analyze_level1("jazz")
    analyze_level2("jazz", "extensions")
    analyze_level2("jazz", "bass")

if __name__ == '__main__':
    main()