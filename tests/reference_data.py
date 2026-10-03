"""Hand-computed reference values for the test suite.

DERIVATION (done on paper, NOT produced by the code under test):

Reference motif M2 (length 2), count matrix rows = positions, columns A/C/G/T:
    pos1: [4, 0, 0, 0]
    pos2: [0, 0, 4, 0]
Pseudocount 1, uniform background 0.25 per base.

Smoothed probabilities:
    pos1: A = 5/8, C = G = T = 1/8
    pos2: G = 5/8, A = C = T = 1/8

Log2 odds vs uniform background:
    log2((5/8) / (1/4)) = log2(2.5) = 1.3219280948873622
    log2((1/8) / (1/4)) = log2(0.5) = -1.0

Score classes over all 16 dinucleotides (each has probability 1/16):
    AG            : 2 * log2(2.5)            = 2.6438561897747244   (1 k-mer)
    A{A,C,T} and
    {C,G,T}G      : log2(2.5) - 1            = 0.3219280948873622   (6 k-mers)
    remaining 9   : -2.0                                            (9 k-mers)

Exact tail probabilities under the uniform background:
    P(S >= 2.643856...) = 1/16  = 0.0625
    P(S >= 0.321928...) = 7/16  = 0.4375
    P(S >= -2.0)        = 16/16 = 1.0

Multiple-testing reference (independent of motif): for the p-value vector
[0.01, 0.04, 0.05] with family size 3:
    Bonferroni: [0.03, 0.12, 0.15]
    BH: sorted p = (0.01, 0.04, 0.05); raw adj = (0.03, 0.06, 0.05);
        enforced monotone from the right -> (0.03, 0.05, 0.05)
"""

M2_MATRIX = [[4.0, 0.0, 0.0, 0.0], [0.0, 0.0, 4.0, 0.0]]
M2_PSEUDOCOUNT = 1.0

LOG2_2_5 = 1.3219280948873622  # log2(2.5)

SCORE_AG = 2.6438561897747244   # 2 * log2(2.5)
SCORE_MID = 0.3219280948873622  # log2(2.5) - 1
SCORE_LOW = -2.0

P_AG = 0.0625    # 1/16
P_MID = 0.4375   # 7/16
P_LOW = 1.0      # 16/16

# Scan of "CAGT" with M2 (window scores, hand-computed):
#   plus : CA = -2.0, AG = 2.643856..., GT = -2.0
#   minus: RC(CA)=TG -> -1 + log2(2.5) = 0.321928...
#          RC(AG)=CT -> -2.0
#          RC(GT)=AC -> log2(2.5) - 1   = 0.321928...
SCAN_CAGT_EXPECTED = {
    # (start, strand): score
    (0, "+"): -2.0,
    (1, "+"): SCORE_AG,
    (2, "+"): -2.0,
    (0, "-"): SCORE_MID,
    (1, "-"): -2.0,
    (2, "-"): SCORE_MID,
}
SCAN_CAGT_N_TESTS = 6
# AG hit corrections with family size 6:
SCAN_CAGT_AG_BONFERRONI = 0.375  # 0.0625 * 6
SCAN_CAGT_AG_BH = 0.375          # rank 1 of 6: 0.0625 * 6 / 1

# Unknown-base handling on "ANG" with M2:
#   skip policy        -> all 4 windows skipped
#   marginalize policy -> N contributes log2(sum_b bg(b)*PWM(b)/bg(b)) = 0:
#       plus  AN = log2(2.5) + 0 = 1.321928...
#       plus  NG = 0 + log2(2.5) = 1.321928...
#       minus RC(AN)=NT -> 0 + (-1) = -1.0
#       minus RC(NG)=CN -> (-1) + 0 = -1.0
MARG_AN = LOG2_2_5
MARG_NG = LOG2_2_5
MARG_NT = -1.0
MARG_CN = -1.0

# Correction reference vector (see derivation above).
CORR_PVALUES = [0.01, 0.04, 0.05]
CORR_BONFERRONI = [0.03, 0.12, 0.15]
CORR_BH = [0.03, 0.05, 0.05]
