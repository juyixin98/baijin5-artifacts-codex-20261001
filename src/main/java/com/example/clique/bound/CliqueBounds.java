package com.example.clique.bound;

import com.example.clique.graph.Graph;
import com.example.clique.search.DegeneracyOrdering;

import java.math.BigInteger;
import java.util.Collection;

/**
 * Bounds relating maximal and maximum cliques. Kept separate from the
 * enumeration API: enumerating maximal cliques is not the same problem as
 * finding a maximum clique, but degeneracy gives a cheap upper bound on the
 * maximum clique size (omega <= degeneracy + 1).
 */
public final class CliqueBounds {

    private CliqueBounds() {
    }

    /** Degeneracy of the graph (max, over removal steps, of min degree). */
    public static int degeneracy(Graph graph) {
        return DegeneracyOrdering.compute(graph).degeneracy();
    }

    /** Upper bound on the maximum clique size: degeneracy + 1. */
    public static int maxCliqueSizeUpperBound(Graph graph) {
        return degeneracy(graph) + 1;
    }

    /** Largest clique size observed in a (maximal) clique collection; 0 when empty. */
    public static int observedMaxSize(Collection<BigInteger> cliques) {
        int max = 0;
        for (BigInteger clique : cliques) {
            max = Math.max(max, clique.bitCount());
        }
        return max;
    }
}
