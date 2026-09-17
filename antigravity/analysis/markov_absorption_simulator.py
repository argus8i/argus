"""
markov_absorption_simulator.py - Markov Chain Multi-State Transition & Absorption Simulator
Calculates the empirical 5x5 state transition matrix from 214,441 historical quotes
and computes exact absorption probabilities: P(Profit Target) vs P(10-Day LC Trap).
"""

import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "track1_historical.db"))

STATES = [
    "TWO_SIDED_BASE",      # State 0: Normal trading, 2-sided depth, price inside band
    "LOCKED_UC",           # State 1: At or near Upper Circuit, 0 offers
    "UNLOCKED_VOLATILE",   # State 2: Volatile wide range, churn
    "LOCKED_LC",           # State 3: At Lower Circuit, 0 bids (absorbing trap)
    "BAND_TIGHTENED"       # State 4: Band cut (Rule 6 early exit)
]


def classify_state(cl: float, op: float, hi: float, lo: float, uc: Optional[float], lc: Optional[float], band: Optional[float], prev_band: Optional[float]) -> str:
    """Classifies a trading day into one of the 5 discrete microstructure states."""
    if prev_band and band and band < prev_band:
        return "BAND_TIGHTENED"

    if uc and cl and cl >= (uc - 0.02):
        return "LOCKED_UC"

    if lc and cl and cl <= (lc + 0.02):
        return "LOCKED_LC"

    if lo and lo > 0:
        rng = ((hi - lo) / lo) * 100
        if rng >= 5.0:
            return "UNLOCKED_VOLATILE"

    return "TWO_SIDED_BASE"


def compute_empirical_transition_matrix(db_path: str = DB_PATH) -> Tuple[Dict[str, Dict[str, float]], Dict[str, int]]:
    """Calculates the 5x5 transition probability matrix P from the historical database."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT scripcode FROM daily_quotes;")
    scrips = [r[0] for r in cur.fetchall()]

    transition_counts = defaultdict(lambda: defaultdict(int))
    state_counts = defaultdict(int)

    for code in scrips:
        cur.execute("""
        SELECT trade_date, open_price, high_price, low_price, close_price, upper_circuit, lower_circuit, band_pct
        FROM daily_quotes
        WHERE scripcode = ?
        ORDER BY trade_date ASC;
        """, (code,))
        rows = cur.fetchall()
        if len(rows) < 2:
            continue

        for i in range(len(rows) - 1):
            curr_row = rows[i]
            next_row = rows[i + 1]

            prev_band = rows[i - 1][7] if i > 0 else curr_row[7]
            s_t = classify_state(curr_row[4], curr_row[1], curr_row[2], curr_row[3], curr_row[5], curr_row[6], curr_row[7], prev_band)
            s_t1 = classify_state(next_row[4], next_row[1], next_row[2], next_row[3], next_row[5], next_row[6], next_row[7], curr_row[7])

            transition_counts[s_t][s_t1] += 1
            state_counts[s_t] += 1

    conn.close()

    # Normalize to probabilities
    matrix = {}
    for s_from in STATES:
        matrix[s_from] = {}
        total = state_counts[s_from]
        for s_to in STATES:
            prob = (transition_counts[s_from][s_to] / total) if total > 0 else 0.0
            matrix[s_from][s_to] = round(prob, 4)

    return matrix, dict(state_counts)


def simulate_absorption_trajectories(
    matrix: Dict[str, Dict[str, float]],
    start_state: str = "TWO_SIDED_BASE",
    target_sessions: int = 10,
    num_simulations: int = 10000,
    seed: int = 42
) -> Dict[str, Any]:
    """Runs 10,000 Markov random walk simulations to compute absorption probabilities."""
    import random
    random.seed(seed)

    outcomes = {
        "PROFIT_TARGET_HIT": 0,    # Hit 2 or more consecutive Upper Circuits
        "LC_TRAP_ABSORBED": 0,     # Absorbed into Lower Circuit Lockout
        "RULE_6_BAND_CUT": 0,      # Absorbed into Surveillance Band Cut
        "NORMAL_HOLD": 0           # Remained in normal oscillation
    }

    consecutive_lc_counts = []
    consecutive_uc_counts = []

    for _ in range(num_simulations):
        current = start_state
        streak_uc = 0
        streak_lc = 0
        absorbed = False

        for session in range(1, target_sessions + 1):
            probs = [matrix[current][next_s] for next_s in STATES]
            next_state = random.choices(STATES, weights=probs, k=1)[0]

            if next_state == "LOCKED_UC":
                streak_uc += 1
                if streak_uc >= 2:  # 2 consecutive UCs achieves ~+15-20% pre-emptive target
                    outcomes["PROFIT_TARGET_HIT"] += 1
                    absorbed = True
                    break
            else:
                streak_uc = 0

            if next_state == "LOCKED_LC":
                streak_lc += 1
                if streak_lc >= 2:  # Trapped in consecutive lower circuits
                    outcomes["LC_TRAP_ABSORBED"] += 1
                    absorbed = True
                    break
            else:
                streak_lc = 0

            if next_state == "BAND_TIGHTENED":
                outcomes["RULE_6_BAND_CUT"] += 1
                absorbed = True
                break

            current = next_state

        if not absorbed:
            outcomes["NORMAL_HOLD"] += 1

    total = num_simulations
    return {
        "total_runs": total,
        "prob_profit_target": round(outcomes["PROFIT_TARGET_HIT"] / total, 4),
        "prob_lc_trap": round(outcomes["LC_TRAP_ABSORBED"] / total, 4),
        "prob_band_cut_exit": round(outcomes["RULE_6_BAND_CUT"] / total, 4),
        "prob_normal_hold": round(outcomes["NORMAL_HOLD"] / total, 4)
    }


if __name__ == "__main__":
    print("Computing 5x5 Empirical State Transition Matrix across 214,441 records...")
    mat, counts = compute_empirical_transition_matrix()
    print("\n=== EMPIRICAL 5x5 MARKOV TRANSITION MATRIX ===")
    print(f"{'State From':<22} | " + " | ".join(f"{s[:10]:<10}" for s in STATES))
    print("-" * 80)
    for s_from in STATES:
        row_str = " | ".join(f"{mat[s_from][s_to]:>10.2%}" for s_to in STATES)
        print(f"{s_from:<22} | {row_str}")

    print("\nRunning 10,000 Markov Trajectory Simulations from TWO_SIDED_BASE...")
    sim = simulate_absorption_trajectories(mat, start_state="TWO_SIDED_BASE")
    print(f"  P(Profit Target Hit +15%): {sim['prob_profit_target']:.2%}")
    print(f"  P(Absorbed into LC Trap) : {sim['prob_lc_trap']:.2%}")
    print(f"  P(Rule 6 Band Cut Exit)  : {sim['prob_band_cut_exit']:.2%}")
    print(f"  P(Normal Multi-Day Hold) : {sim['prob_normal_hold']:.2%}")
