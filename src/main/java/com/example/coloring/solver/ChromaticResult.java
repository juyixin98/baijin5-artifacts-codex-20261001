package com.example.coloring.solver;

import com.example.coloring.bounds.CliqueCertificate;
import com.example.coloring.cert.Coloring;

/**
 * Result of a minimum-coloring search.
 *
 * <p>{@code lowerBound} comes from a clique witness; {@code upperBound} comes
 * from a concrete proper coloring. Both are independently re-checkable. When
 * status is {@link SearchStatus#BOUNDS_PROVEN} the exact chromatic number is
 * intentionally NOT claimed.</p>
 */
public record ChromaticResult(
        SearchStatus status,
        int lowerBound,
        int upperBound,
        CliqueCertificate cliqueWitness,
        Coloring coloring,
        long nodesVisited,
        boolean stoppedByNodeBudget,
        boolean stoppedByTimeBudget) {

    /** Exact chromatic number; only valid when {@link SearchStatus#isExact()}. */
    public int chromaticNumber() {
        if (!status.isExact()) {
            throw new IllegalStateException(
                    "chromatic number not proven: bounds are [" + lowerBound + ", " + upperBound + "]");
        }
        return upperBound;
    }

    public String summary() {
        if (status.isExact()) {
            return "OPTIMAL chi=" + upperBound
                    + " (clique lower bound " + lowerBound + ", coloring certificate, nodes=" + nodesVisited + ")";
        }
        return "BOUNDS_PROVEN chi in [" + lowerBound + ", " + upperBound + "] nodes=" + nodesVisited
                + (stoppedByNodeBudget ? " [node budget]" : "")
                + (stoppedByTimeBudget ? " [time budget]" : "");
    }
}
