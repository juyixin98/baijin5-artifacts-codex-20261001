package com.example.coloring.solver;

/**
 * Immutable solver configuration.
 *
 * <p>{@code nodeBudget}/{@code timeBudgetMillis} bound the work; when either
 * is exceeded the solver stops and reports only the bounds it has actually
 * proved. {@code warmStartGreedy} controls whether the DSATUR coloring seeds
 * the initial upper bound (used by a dedicated symmetry/acceptance test).</p>
 */
public record SolverConfig(long nodeBudget, long timeBudgetMillis, boolean warmStartGreedy) {

    public SolverConfig {
        if (nodeBudget <= 0) {
            throw new IllegalArgumentException("nodeBudget must be positive, got " + nodeBudget);
        }
        if (timeBudgetMillis <= 0) {
            throw new IllegalArgumentException("timeBudgetMillis must be positive, got " + timeBudgetMillis);
        }
    }

    public static SolverConfig standard() {
        return new SolverConfig(5_000_000L, 30_000L, true);
    }

    public static SolverConfig limited(long nodeBudget, long timeBudgetMillis) {
        return new SolverConfig(nodeBudget, timeBudgetMillis, true);
    }
}
