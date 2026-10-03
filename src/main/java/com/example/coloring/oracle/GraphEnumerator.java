package com.example.coloring.oracle;

import com.example.coloring.model.SimpleGraph;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/**
 * Enumerates every labeled simple graph on {@code n} vertices.
 *
 * <p>Each of the {@code n(n-1)/2} possible edges is independently present or
 * absent, so the stream contains exactly {@code 2^(n choose 2)} graphs
 * (complement pairs, disconnected graphs and odd cycles all included). Memory
 * use is linear in {@code n}: graphs are emitted one at a time via a callback.
 */
public final class GraphEnumerator {

    private static final int MAX_EXHAUSTIVE_N = 62;

    private GraphEnumerator() {
    }

    /** Total number of labeled simple graphs on n vertices. */
    public static java.math.BigInteger count(int n) {
        if (n < 0) {
            throw new IllegalArgumentException("n must be non-negative");
        }
        long edgeSlots = (long) n * (n - 1) / 2;
        return java.math.BigInteger.ONE.shiftLeft((int) edgeSlots);
    }

    public static void forEachGraph(int n, Consumer<SimpleGraph> visitor) {
        if (n < 0) {
            throw new IllegalArgumentException("n must be non-negative");
        }
        if (n > MAX_EXHAUSTIVE_N) {
            throw new IllegalArgumentException(
                    "exhaustive enumeration capped at n=" + MAX_EXHAUSTIVE_N);
        }
        List<int[]> possibleEdges = new ArrayList<>();
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                possibleEdges.add(new int[] {u, v});
            }
        }
        int slots = possibleEdges.size();
        if (slatsBeyondInt(slots)) {
            throw new IllegalArgumentException("too many edge slots for this enumerator");
        }
        long total = 1L << slots;
        for (long mask = 0; mask < total; mask++) {
            SimpleGraph.Builder builder = new SimpleGraph.Builder(n);
            for (int i = 0; i < slots; i++) {
                if ((mask & (1L << i)) != 0) {
                    int[] edge = possibleEdges.get(i);
                    builder.addEdge(edge[0], edge[1]);
                }
            }
            visitor.accept(builder.build());
        }
    }

    private static boolean slatsBeyondInt(int slots) {
        return slots > 62;
    }
}
