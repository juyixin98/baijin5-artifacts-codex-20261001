package com.example.clique.search;

import com.example.clique.graph.Graph;

import java.math.BigInteger;

/**
 * Degeneracy (core) ordering: repeatedly remove a minimum-degree vertex.
 * The ordering is complete - every vertex appears exactly once - which is what
 * guarantees the degeneracy-driven enumeration does not miss any clique.
 */
public final class DegeneracyOrdering {

    /** @param order vertex ids in removal order; @param degeneracy max degree at removal time. */
    public record Ordering(int[] order, int degeneracy) {
    }

    private DegeneracyOrdering() {
    }

    public static Ordering compute(Graph graph) {
        int n = graph.vertexCount();
        int[] order = new int[n];
        int[] degree = new int[n];
        for (int v = 0; v < n; v++) {
            degree[v] = graph.degree(v);
        }
        BigInteger remaining = graph.vertices();
        int degeneracy = 0;
        for (int i = 0; i < n; i++) {
            int v = argMinDegree(remaining, degree);
            order[i] = v;
            degeneracy = Math.max(degeneracy, degree[v]);
            remaining = remaining.clearBit(v);
            BigInteger nbrs = graph.neighbors(v).and(remaining);
            while (nbrs.signum() != 0) {
                int u = nbrs.getLowestSetBit();
                nbrs = nbrs.clearBit(u);
                degree[u]--;
            }
        }
        return new Ordering(order, degeneracy);
    }

    private static int argMinDegree(BigInteger remaining, int[] degree) {
        int best = -1;
        int bestDegree = Integer.MAX_VALUE;
        BigInteger rest = remaining;
        while (rest.signum() != 0) {
            int v = rest.getLowestSetBit();
            rest = rest.clearBit(v);
            if (degree[v] < bestDegree) {
                bestDegree = degree[v];
                best = v;
            }
        }
        return best;
    }
}
