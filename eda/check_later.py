import numpy as np
import random

def generate_open_and_closed_candidates(chord_pitches, target_register=(48, 76)):
    """Generates a massive, diverse pool of both open and closed voicings."""
    candidates = []
    
    # 1. Generate closed inversions first
    closed_inversions = get_all_octave_permutations(chord_pitches, target_register)
    
    for voicing in closed_inversions:
        candidates.append(voicing) # Keep the closed version
        
        # 2. Generate Drop 2 (Open)
        if len(voicing) >= 3:
            drop2 = list(voicing)
            drop2[-2] -= 12  # Drop the 2nd voice from the top by an octave
            if all(target_register[0] <= note <= target_register[1] for note in drop2):
                candidates.append(sorted(drop2))
                
        # 3. Generate Drop 3 (Open)
        if len(voicing) >= 4:
            drop3 = list(voicing)
            drop3[-3] -= 12  # Drop the 3rd voice from the top by an octave
            if all(target_register[0] <= note <= target_register[1] for note in drop3):
                candidates.append(sorted(drop3))
                
    return list(set(tuple(c) for c in candidates)) # Remove duplicates

def choose_stochastic_voicing(previous_voicing, candidate_pool, temperature=1.5, center_pitch=60):
    """Weighs candidates and selects one probabilistically based on temperature."""
    costs = []
    
    for candidate in candidate_pool:
        # Calculate standard voice leading cost
        parsimony_cost = sum(abs(c - p) for c, p in zip(candidate, previous_voicing))
        
        # Calculate center-of-keyboard gravity cost to prevent long-term drift
        avg_pitch = sum(candidate) / len(candidate)
        gravity_cost = abs(avg_pitch - center_pitch)
        
        total_cost = (1.0 * parsimony_cost) + (0.2 * gravity_cost)
        
        # Critical protection: Hard penalty for voice crossing
        if voice_crossed(candidate, previous_voicing):
            total_cost += 500
            
        costs.append(total_cost)
        
    # Convert costs into a probability distribution via Softmax/Boltzmann
    costs = np.array(costs)
    # Subtracting the minimum cost avoids floating-point overflow errors
    log_inputs = -(costs - np.min(costs)) / temperature
    exp_values = np.exp(log_inputs)
    probabilities = exp_values / np.sum(exp_values)
    
    # Select a candidate based on the calculated probabilities
    chosen_index = np.random.choice(len(candidate_pool), p=probabilities)
    return candidate_pool[chosen_index]
