package com.example.coloring.diag;

/**
 * Why a branch of the k-colorability search was rejected, or why the tree
 * terminated. Every value is a real, independently assertable search outcome.
 */
public enum PruningReason {

    /** Target k is already excluded by the proven clique lower bound (omega > k). */
    CLIQUE_LOWER_BOUND,

    /** Chosen vertex had no usable color: every color is taken by a colored neighbour. */
    NO_FEASIBLE_COLOR,

    /** Color branch skipped because colors are interchangeable (rename symmetry). */
    SYMMETRY_CANONICAL_SKIP,

    /** Candidate branch fully assigned without conflicts (a certificate exists). */
    FOUND_FEASIBLE,

    /** Entire decision tree exhausted without finding a proper coloring. */
    PROVED_INFEASIBLE,

    /** Node budget exhausted; only bounds proven before the stop are reported. */
    BUDGET_EXHAUSTED
}
