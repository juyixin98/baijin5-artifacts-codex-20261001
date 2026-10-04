package com.local.mwis.service;

/** Top-level outcome of a solve request; exactly one status per report. */
public enum ServiceStatus {
    /** Solved; certificate verified; optimality proven by an independent bound. */
    OK_PROVEN,
    /** Solved and certificate verified, but the computable bound leaves a gap. */
    OK_BOUND_INCONCLUSIVE,
    /** Request contract violated (graph, weights or decomposition invalid). */
    REJECTED,
    /** DP table entry budget was exceeded; no result produced. */
    BUDGET_EXCEEDED,
    /** A bag exceeds the supported bitmask width. */
    BAG_TOO_WIDE,
    /** Optional exhaustive cross-check disagreed with the DP result. */
    CROSSCHECK_MISMATCH,
    /** Cross-check requested but graph too large for exhaustive enumeration. */
    CROSSCHECK_SKIPPED,
    /** Unexpected internal failure. */
    INTERNAL_ERROR
}
