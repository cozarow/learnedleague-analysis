"""
ll_analysis.py
--------------
Analysis functions for LearnedLeague rundle data.

Load data:
    import json
    with open("rundle_data.json") as f:
        data = json.load(f)
"""

import json
import os
from itertools import combinations


def pairwise_agreement(data: dict, player1: str, player2: str) -> float:
    """
    Compute the proportion of questions where two players gave the same answer
    (both correct or both incorrect), excluding any question where either
    player forfeited.

    Returns a float in [0, 1], or None if there are no valid questions to compare.
    """
    answers1 = data[player1]
    answers2 = data[player2]

    both_played = [(a1, a2) for a1, a2 in zip(answers1, answers2)
                   if a1 != "F" and a2 != "F"]

    if not both_played:
        return None

    agreed = sum(1 for a1, a2 in both_played if a1 == a2)
    return agreed / len(both_played)

def one_pairwise_agreement(data: dict, p1: str) -> float:
    players = list(data.keys())
    result = {p: {} for p in players}
    result_list = []

    for p in players:
        agreement = pairwise_agreement(data, p1, p)
        result[p] = agreement
        result_list.append((p, agreement))

    #return(result)

    return(sorted(result_list, key=lambda x: x[1], reverse=True))

def all_pairwise_agreements(data: dict) -> dict:
    """
    Compute pairwise agreement for every pair of players in the rundle.

    Returns a dict of dicts:
        { player1: { player2: float, ... }, ... }
    where result[a][b] == result[b][a] for all a, b.
    """
    players = list(data.keys())
    result = {p: {} for p in players}

    for p1, p2 in combinations(players, 2):
        agreement = pairwise_agreement(data, p1, p2)
        result[p1][p2] = agreement
        result[p2][p1] = agreement  # symmetric

    return result

def average_agreement(agreements: dict) -> list:
    """
    For each player, compute their average pairwise agreement with all others.
    Returns a list of (player, avg_agreement) sorted descending.
    """
    result = []
    for player, others in agreements.items():
        vals = [v for v in others.values() if v is not None]
        avg = sum(vals) / len(vals) if vals else None
        result.append((player, avg))
    return sorted(result, key=lambda x: x[1], reverse=True)

def print_agreement_matrix(agreements: dict):
    """
    Print a readable matrix of all pairwise agreements, sorted by player name.
    """
    players = sorted(agreements.keys())
    col_w = 7

    # Header
    row_label_w = max(len(p) for p in players) + 1
    header = " " * row_label_w + "".join(f"{p:>{col_w}}" for p in players)
    print(header)
    print("-" * len(header))

    for p1 in players:
        row = f"{p1:<{row_label_w}}"
        for p2 in players:
            if p1 == p2:
                row += f"{'---':>{col_w}}"
            elif p2 in agreements[p1] and agreements[p1][p2] is not None:
                row += f"{agreements[p1][p2]:>{col_w}.1%}"
            else:
                row += f"{'n/a':>{col_w}}"
        print(row)


def most_similar_pairs(agreements: dict, n: int = 10) -> list:
    """
    Return the n most similar pairs of players, as a sorted list of
        ((player1, player2), agreement)
    in descending order.
    """
    pairs = []
    players = list(agreements.keys())
    for p1, p2 in combinations(players, 2):
        val = agreements[p1].get(p2)
        if val is not None:
            pairs.append(((p1, p2), val))
    return sorted(pairs, key=lambda x: x[1], reverse=True)[:n]

def least_similar_pairs(agreements: dict, n: int = 10) -> list:
    """
    Return the n least similar pairs of players, as a sorted list of
        ((player1, player2), agreement)
    in descending order.
    """
    pairs = []
    players = list(agreements.keys())
    for p1, p2 in combinations(players, 2):
        val = agreements[p1].get(p2)
        if val is not None:
            pairs.append(((p1, p2), val))
    return sorted(pairs, key=lambda x: x[1], reverse=False)[:n]


if __name__ == "__main__":
    DATA_FILE = os.path.join(os.path.dirname(__file__), "rundle_data.json")
    with open(DATA_FILE) as f:
        data = json.load(f)

    # --- Single pair ---
    # p1, p2 = "SchE", "KeJ4"   # example
    # agreement = pairwise_agreement(data, p1, p2)
    # print(f"Agreement between {p1} and {p2}: {agreement:.1%}\n")

    # --- Single person ---
    # p1 = "OrdP" #example
    # print(f"Agreement between {p1} and others: {one_pairwise_agreement(data, p1)}\n")

    # --- All pairs ---
    agreements = all_pairwise_agreements(data)

    print("Average agreement:")
    for player, avg in average_agreement(agreements):
        print(f"  {player:<12} {avg:.1%}")

    print("Top 10 most similar pairs:")
    for (a, b), val in most_similar_pairs(agreements, n=10):
        print(f"  {a} & {b}: {val:.1%}")

    print("Top 10 least similar pairs:")
    for (a, b), val in least_similar_pairs(agreements, n=10):
        print(f"  {a} & {b}: {val:.1%}")

    print()
    #print_agreement_matrix(agreements)

# pairwise_agreeent("OzaC", )