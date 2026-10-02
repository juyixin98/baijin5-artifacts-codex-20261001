package com.example.coloring.solver;

/**
 * Outcome class asserted by independent tests.
 *
 * <ul>
 *   <li>{@link #OPTIMAL}: lower and upper bound coincide; chi is certified.</li>
 *   <li>{@link #BOUNDS_PROVEN}: stopped by budget; lowerBound and upperBound
 *       are both valid but the gap is not closed. Nothing is guessed.</li>
 * </ul>
 */
public enum SearchStatus {
    OPTIMAL,
    BOUNDS_PROVEN;

    public boolean isExact() {
        return this == OPTIMAL;
    }
}
