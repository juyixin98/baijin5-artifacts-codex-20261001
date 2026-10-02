package com.example.coloring.diag;

/**
 * Machine-readable reason for a decision.
 *
 * <p>Reject reasons encode the pruning rule applied; accept/undetermined
 * reasons encode the proof basis or the stopping condition.</p>
 */
public enum Reason {

    // Accept bases
    BRANCH_EXPLORED("branch is consistent with partial coloring; explored"),
    FEASIBLE_COMPLETE("all vertices colored; feasible upper-bound certificate"),
    LOWER_BOUND_MATCHES_UPPER("clique lower bound equals current upper bound; gap closed"),

    // Pruning (reject) rules
    ADJACENT_SAME_COLOR("candidate color is already used by an adjacent colored vertex"),
    LOWER_BOUND_NOT_IMPROVABLE("remaining clique already needs all current colors; cannot beat best"),
    SYMMETRY_NEW_COLOR_UNUSED("color renaming symmetry: a never-used color is canonical"),

    // Stopping
    NODE_BUDGET_EXHAUSTED("node budget exhausted before the gap was closed"),
    TIME_BUDGET_EXHAUSTED("time budget exhausted before the gap was closed"),

    // Component handling
    COMPONENT_SOLVED("connected component solved independently");

    private final String description;

    Reason(String description) {
        this.description = description;
    }

    public String description() {
        return description;
    }
}
