package com.example.clique.bound;

import com.example.clique.graph.Graph;

import java.math.BigInteger;
import java.util.Collection;

/**
 * Maximum clique facts, derived from - but clearly distinct from - maximal
 * clique enumeration. A maximum clique is always maximal, so the maximum
 * clique size equals the largest maximal clique size; the converse is false
 * (a maximal clique need not be maximum).
 */
public final class MaximumClique {

    /**
     * @param size maximum clique size (0 for the empty graph)
     * @param witness a maximum-size maximal clique, or {@code null} for the empty graph
     * @param degeneracyUpperBound certificate that no larger clique can exist
     */
    public record Result(int size, BigInteger witness, int degeneracyUpperBound) {
    }

    private MaximumClique() {
    }

    public static Result fromMaximalCliques(Graph graph, Collection<BigInteger> maximalCliques) {
        int best = 0;
        BigInteger witness = null;
        for (BigInteger clique : maximalCliques) {
            if (clique.bitCount() > best) {
                best = clique.bitCount();
                witness = clique;
            }
        }
        return new Result(best, witness, CliqueBounds.maxCliqueSizeUpperBound(graph));
    }
}
