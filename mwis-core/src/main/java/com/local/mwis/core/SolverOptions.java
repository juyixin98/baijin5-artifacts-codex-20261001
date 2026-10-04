package com.local.mwis.core;

/**
 * Tunables for {@link MwisSolver}.
 *
 * @param tableEntryBudget maximum number of DP table entries the solver may create
 *                         across all nodes; solving aborts with
 *                         {@link BudgetExceededException} once exceeded
 */
public record SolverOptions(long tableEntryBudget) {

    public static final long UNLIMITED = Long.MAX_VALUE;

    public static SolverOptions unlimited() {
        return new SolverOptions(UNLIMITED);
    }

    public SolverOptions {
        if (tableEntryBudget <= 0) {
            throw new IllegalArgumentException("tableEntryBudget must be positive");
        }
    }
}
