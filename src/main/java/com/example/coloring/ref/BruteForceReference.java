package com.example.coloring.ref;

import com.example.coloring.graph.Graph;

import java.math.BigInteger;

/**
 * Independent oracle that counts proper colorings over a fixed palette by
 * direct backtracking over every assignment {@code f: V -> [0,r)}.
 *
 * <p>The enumeration applies exactly one test per assignment prefix (does an
 * edge to an earlier vertex become monochromatic?) and shares no search
 * machinery with {@code ChromaticSolver}: no symmetry breaking, no clique
 * bound, no incumbent. It is an obviously correct specification. Intended for
 * small graphs and small palette sizes.</p>
 */
public final class BruteForceReference {

    private BruteForceReference() {
    }

    public static BigInteger countProperColorings(Graph graph, int r) {
        if (r < 0) {
            throw new IllegalArgumentException("palette size must be non-negative");
        }
        int n = graph.order();
        if (n == 0) {
            return BigInteger.ONE;
        }
        return new Dfs(graph, n, r).count();
    }

    public static int chromaticNumber(Graph graph) {
        int n = graph.order();
        for (int r = 0; r <= n; r++) {
            if (countProperColorings(graph, r).signum() > 0) {
                return r;
            }
        }
        throw new IllegalStateException("unreachable: n colors always color a simple graph");
    }

    private static final class Dfs {
        private final Graph graph;
        private final int n;
        private final int r;
        private final int[] colors;
        private BigInteger count = BigInteger.ZERO;

        Dfs(Graph graph, int n, int r) {
            this.graph = graph;
            this.n = n;
            this.r = r;
            this.colors = new int[n];
        }

        BigInteger count() {
            enumerate(0);
            return count;
        }

        private void enumerate(int vertex) {
            if (vertex == n) {
                count = count.add(BigInteger.ONE);
                return;
            }
            for (int color = 0; color < r; color++) {
                colors[vertex] = color;
                if (isConsistent(vertex)) {
                    enumerate(vertex + 1);
                }
            }
        }

        private boolean isConsistent(int vertex) {
            var neighbors = graph.neighborSet(vertex);
            for (int w = neighbors.nextSetBit(0); w >= 0; w = neighbors.nextSetBit(w + 1)) {
                if (w < vertex && colors[w] == colors[vertex]) {
                    return false;
                }
            }
            return true;
        }
    }
}
