package com.local.mwis.exhaustive;

import com.local.mwis.graph.Graph;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;

/**
 * Reference solver: enumerates all 2^n vertex subsets and keeps the best feasible one.
 * Deliberately shares no code with the tree-decomposition DP so it can serve as an
 * independent oracle in tests and in the service's cross-check stage.
 *
 * <p>Refuses graphs with more than {@link #DEFAULT_MAX_VERTICES} vertices to keep the
 * enumeration finite in practice.
 */
public final class BruteForceMwis {

    public static final int DEFAULT_MAX_VERTICES = 24;

    private final int maxVertices;

    public BruteForceMwis() {
        this(DEFAULT_MAX_VERTICES);
    }

    public BruteForceMwis(int maxVertices) {
        this.maxVertices = maxVertices;
    }

    public ExhaustiveResult solve(Graph graph, BigInteger[] weights) {
        int n = graph.vertexCount();
        if (n > maxVertices) {
            throw new InputTooLargeException(n, maxVertices);
        }
        if (weights.length != n) {
            throw new IllegalArgumentException("weights length mismatch");
        }
        // adjacency as bitmask per vertex for O(1) conflict tests
        long[] adjMask = new long[n];
        for (int u = 0; u < n; u++) {
            for (int v : graph.neighbors(u)) {
                adjMask[u] |= 1L << v;
            }
        }
        BigInteger best = BigInteger.ZERO;
        long bestMask = 0;
        long total = 1L << n;
        // incremental Gray-code-free evaluation: recompute per subset, n is small
        for (long mask = 0; mask < total; mask++) {
            BigInteger sum = BigInteger.ZERO;
            boolean feasible = true;
            for (int v = 0; v < n && feasible; v++) {
                if ((mask & (1L << v)) != 0) {
                    // conflict if a lower-numbered selected neighbor exists
                    if ((adjMask[v] & mask & ((1L << v) - 1)) != 0) {
                        feasible = false;
                    } else {
                        sum = sum.add(weights[v]);
                    }
                }
            }
            if (feasible && sum.compareTo(best) > 0) {
                best = sum;
                bestMask = mask;
            }
        }
        List<Integer> set = new ArrayList<>();
        for (int v = 0; v < n; v++) {
            if ((bestMask & (1L << v)) != 0) {
                set.add(v);
            }
        }
        return new ExhaustiveResult(best, set, total);
    }
}
