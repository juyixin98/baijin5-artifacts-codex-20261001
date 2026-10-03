package com.example.coloring.oracle;

import com.example.coloring.model.Graph;
import java.math.BigInteger;

/**
 * Second independent counting method: brute-force over every one of the
 * {@code k^n} assignments of a labeled k-color palette, accepting an assignment
 * iff no edge is monochromatic. Uses no partition machinery and no solver code,
 * so agreement with {@link PartitionOracle#surjectiveColorings} cross-checks two
 * independently written enumerations.
 */
public final class LabeledColoringCounter {

    private LabeledColoringCounter() {
    }

    public static BigInteger count(Graph graph, int k) {
        if (k < 0) {
            throw new IllegalArgumentException("k must be non-negative");
        }
        int n = graph.n();
        if (n == 0) {
            return BigInteger.ONE;
        }
        if (k == 0) {
            return BigInteger.ZERO;
        }
        int[] colors = new int[n];
        return new Worker(graph, k).enumerate(colors, 0);
    }

    private static final class Worker {

        private final Graph graph;
        private final int k;

        Worker(Graph graph, int k) {
            this.graph = graph;
            this.k = k;
        }

        BigInteger enumerate(int[] colors, int index) {
            if (index == colors.length) {
                return BigInteger.ONE;
            }
            BigInteger ways = BigInteger.ZERO;
            for (int c = 0; c < k; c++) {
                if (isCompatible(colors, index, c)) {
                    colors[index] = c;
                    ways = ways.add(enumerate(colors, index + 1));
                }
            }
            return ways;
        }

        private boolean isCompatible(int[] colors, int vertex, int candidate) {
            for (int u : graph.neighbors(vertex)) {
                if (u < vertex && colors[u] == candidate) {
                    return false;
                }
            }
            return true;
        }
    }
}
