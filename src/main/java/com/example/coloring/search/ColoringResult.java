package com.example.coloring.search;

import com.example.coloring.diag.PruningReason;
import java.math.BigInteger;
import java.util.Map;
import java.util.Optional;

/**
 * Outcome of a chromatic-number search.
 *
 * <p>{@code lowerBound} is always proven (clique witness attached) and
 * {@code upperBound} is always feasible (proper coloring attached). When the two
 * coincide the result is {@link ColoringStatus#OPTIMAL}; otherwise it is
 * {@link ColoringStatus#STOPPED} and {@link #chromatic()} is unavailable.
 */
public final class ColoringResult {

    private final String requestId;
    private final ColoringStatus status;
    private final int lowerBound;
    private final int upperBound;
    private final int[] coloring;
    private final int[] clique;
    private final BigInteger nodesUsed;
    private final BigInteger budget;
    private final Map<PruningReason, Long> pruningCounts;

    public ColoringResult(
            String requestId,
            ColoringStatus status,
            int lowerBound,
            int upperBound,
            int[] coloring,
            int[] clique,
            BigInteger nodesUsed,
            BigInteger budget,
            Map<PruningReason, Long> pruningCounts) {
        this.requestId = requestId;
        this.status = status;
        this.lowerBound = lowerBound;
        this.upperBound = upperBound;
        this.coloring = coloring == null ? null : coloring.clone();
        this.clique = clique.clone();
        this.nodesUsed = nodesUsed;
        this.budget = budget;
        this.pruningCounts = Map.copyOf(pruningCounts);
    }

    public String requestId() {
        return requestId;
    }

    public ColoringStatus status() {
        return status;
    }

    /** Proven lower bound omega (clique number): chi >= lowerBound. */
    public int lowerBound() {
        return lowerBound;
    }

    /** Feasible upper bound from an explicit proper coloring: chi <= upperBound. */
    public int upperBound() {
        return upperBound;
    }

    /** Proper coloring with upperBound colors, or null on budget stop before any
     *  feasible coloring was constructed (not currently reachable: the greedy
     *  heuristic runs before the budgeted exact search). */
    public int[] coloring() {
        return coloring == null ? null : coloring.clone();
    }

    /** Clique witnessing the lower bound. */
    public int[] cliqueWitness() {
        return clique.clone();
    }

    public BigInteger nodesUsed() {
        return nodesUsed;
    }

    public BigInteger budget() {
        return budget;
    }

    /** How many decision-tree branches were rejected for each concrete reason. */
    public Map<PruningReason, Long> pruningCounts() {
        return pruningCounts;
    }

    /** Exact chromatic number, present only when bounds meet. */
    public Optional<Integer> chromatic() {
        return status == ColoringStatus.OPTIMAL ? Optional.of(upperBound) : Optional.empty();
    }
}
