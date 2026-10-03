"""Independent reference values and implementations for the test-suite.

Nothing in this module imports the package's scoring, scanning or
significance code. Values here are either hand-computed constants or
produced by a deliberately naive, self-contained re-implementation, so the
tests check the package against an independent source of truth rather than
against itself.

Hand-computed reference motif ("AC" detector)
---------------------------------------------
counts = [[4,0,0,0], [0,4,0,0]]  (columns A,C,G,T), background uniform
0.25, pseudocount 1.0:

    p' = (n + 1*0.25) / (4 + 1)  ->  0.85 for the consensus base, 0.05 else
    log2-odds: log2(0.85/0.25) = log2(3.4)   (consensus)
               log2(0.05/0.25) = log2(0.2)   (non-consensus)

Of the 16 equally likely 2-letter words:
    1 word  (AC) scores 2*log2(3.4)            -> p = 1/16  = 0.0625
    6 words score log2(3.4) + log2(0.2)        -> p = 7/16  = 0.4375
    9 words score 2*log2(0.2)                  -> p = 16/16 = 1.0
"""

from __future__ import annotations

import itertools
import math

ALPHABET = "ACGT"

# --- hand-computed constants for the reference motif -----------------------
REF_COUNTS = [[4.0, 0.0, 0.0, 0.0], [0.0, 4.0, 0.0, 0.0]]
REF_BACKGROUND = {"A": 0.25, "C": 0.25, "G": 0.25, "T": 0.25}
REF_PSEUDOCOUNT = 1.0

CONSENSUS_LOGODDS = math.log2(3.4)
OFF_LOGODDS = math.log2(0.2)

SCORE_BEST = 2.0 * CONSENSUS_LOGODDS            # word "AC"
SCORE_MID = CONSENSUS_LOGODDS + OFF_LOGODDS     # one consensus position
SCORE_LOW = 2.0 * OFF_LOGODDS                   # no consensus position

P_BEST = 1.0 / 16.0
P_MID = 7.0 / 16.0
P_LOW = 1.0


# --- naive independent re-implementation -----------------------------------

def naive_log_odds(counts, background, pseudocount):
    matrix = []
    for row in counts:
        total = sum(row)
        matrix.append(
            [
                math.log2(((row[b] + pseudocount * background[ALPHABET[b]]) / (total + pseudocount))
                          / background[ALPHABET[b]])
                for b in range(4)
            ]
        )
    return matrix


def naive_score(matrix, word):
    return sum(matrix[i][ALPHABET.index(ch)] for i, ch in enumerate(word))


def naive_distribution(counts, background, pseudocount):
    """Return {score: probability} by brute-force enumeration of all words."""
    matrix = naive_log_odds(counts, background, pseudocount)
    k = len(counts)
    dist: dict[float, float] = {}
    for word_tuple in itertools.product(ALPHABET, repeat=k):
        word = "".join(word_tuple)
        prob = 1.0
        for ch in word:
            prob *= background[ch]
        score = naive_score(matrix, word)
        # merge with tolerance-free key: round like the docs declare
        key = round(score, 9)
        dist[key] = dist.get(key, 0.0) + prob
    return dist


def naive_pvalue(counts, background, pseudocount, score):
    dist = naive_distribution(counts, background, pseudocount)
    return sum(p for s, p in dist.items() if s >= score - 1e-9)


def naive_reverse_complement(seq):
    comp = {"A": "T", "C": "G", "G": "C", "T": "A"}
    return "".join(comp.get(ch, "N") for ch in reversed(seq))
