package com.example.coloring.graph;

import java.util.List;

/** Local synthetic graph factory: every fixture is generated, no external data. */
public final class Graphs {

    private Graphs() {
    }

    public static Graph empty() {
        return Graph.builder(0).build();
    }

    public static Graph edgeless(int n) {
        return Graph.builder(n).build();
    }

    public static Graph complete(int n) {
        Graph.Builder builder = Graph.builder(n);
        for (int i = 0; i < n; i++) {
            for (int j = i + 1; j < n; j++) {
                builder.edge(i, j);
            }
        }
        return builder.build();
    }

    public static Graph cycle(int n) {
        if (n < 3) {
            throw new IllegalArgumentException("cycle needs at least 3 vertices, got " + n);
        }
        Graph.Builder builder = Graph.builder(n);
        for (int i = 0; i < n; i++) {
            builder.edge(i, (i + 1) % n);
        }
        return builder.build();
    }

    public static Graph path(int n) {
        Graph.Builder builder = Graph.builder(n);
        for (int i = 0; i + 1 < n; i++) {
            builder.edge(i, i + 1);
        }
        return builder.build();
    }

    /** Disjoint union of two graphs; second component vertex indices are shifted. */
    public static Graph union(Graph left, Graph right) {
        int a = left.order();
        int b = right.order();
        Graph.Builder builder = Graph.builder(a + b);
        for (int[] edge : left.edges()) {
            builder.edge(edge[0], edge[1]);
        }
        for (int[] edge : right.edges()) {
            builder.edge(a + edge[0], a + edge[1]);
        }
        for (int v = 0; v < a; v++) {
            builder.label(v, left.label(v));
        }
        for (int v = 0; v < b; v++) {
            builder.label(a + v, right.label(v));
        }
        return builder.build();
    }

    /** Petersen graph (chi=3, clique=2). */
    public static Graph petersen() {
        Graph.Builder builder = Graph.builder(10);
        for (int i = 0; i < 5; i++) {
            builder.edge(i, (i + 1) % 5);
            builder.edge(i, 5 + i);
            builder.edge(5 + i, 5 + ((i + 2) % 5));
        }
        return builder.build();
    }

    /**
     * Deterministic G(n,p) synthetic graph using a small fixed-step LCG so the
     * same seed always yields the same fixture.
     */
    public static Graph randomErdosRenyi(int n, long seedNumerator, long seedDenominator, long seed) {
        if (seedDenominator <= 0 || seedNumerator < 0 || seedNumerator > seedDenominator) {
            throw new IllegalArgumentException("probability must be num/den with 0 <= num <= den, den > 0");
        }
        long state = seed == 0 ? 0x9E3779B97F4A7C15L : seed;
        Graph.Builder builder = Graph.builder(n);
        for (int i = 0; i < n; i++) {
            for (int j = i + 1; j < n; j++) {
                state = lcg(state);
                long value = state >>> 1;
                long threshold = (Long.MAX_VALUE / seedDenominator) * seedNumerator;
                if (value < threshold) {
                    builder.edge(i, j);
                }
            }
        }
        return builder.build();
    }

    /** Graph from an explicit edge list on vertices [0,n). */
    public static Graph fromEdges(int n, List<int[]> edges) {
        Graph.Builder builder = Graph.builder(n);
        for (int[] edge : edges) {
            builder.edge(edge[0], edge[1]);
        }
        return builder.build();
    }

    private static long lcg(long state) {
        return 6364136223846793005L * state + 1442695040888963407L;
    }
}
