package com.local.mwis.bounds;

import com.local.mwis.graph.Graph;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;

/**
 * Computable upper bounds on the weighted independence number. Used to certify
 * optimality when a bound meets the solver's value, and to report the gap otherwise.
 */
public final class UpperBounds {

    /** Trivial valid bound: sum of all positive vertex weights. */
    public static BigInteger positiveWeightSum(BigInteger[] weights) {
        BigInteger sum = BigInteger.ZERO;
        for (BigInteger w : weights) {
            if (w.signum() > 0) {
                sum = sum.add(w);
            }
        }
        return sum;
    }

    /**
     * Clique-cover bound: partition the vertices into cliques of G (a greedy coloring
     * of the complement graph); an independent set meets each clique in at most one
     * vertex, so sum over parts of max(0, max weight in part) is an upper bound.
     */
    public static BigInteger cliqueCoverBound(Graph graph, BigInteger[] weights) {
        int n = graph.vertexCount();
        int[] color = new int[n];
        for (int i = 0; i < n; i++) {
            color[i] = -1;
        }
        int colors = 0;
        for (int v = 0; v < n; v++) {
            // smallest color not used by non-adjacent... by complement-adjacent vertices
            // two vertices may share a color iff they ARE adjacent in G
            boolean[] blocked = new boolean[colors];
            for (int u = 0; u < n; u++) {
                if (u != v && color[u] >= 0 && !graph.adjacent(u, v)) {
                    blocked[color[u]] = true;
                }
            }
            int c = 0;
            while (c < colors && blocked[c]) {
                c++;
            }
            if (c == colors) {
                colors++;
            }
            color[v] = c;
        }
        List<BigInteger> maxPerClass = new ArrayList<>();
        for (int c = 0; c < colors; c++) {
            maxPerClass.add(null);
        }
        for (int v = 0; v < n; v++) {
            BigInteger w = weights[v];
            BigInteger cur = maxPerClass.get(color[v]);
            if (cur == null || w.compareTo(cur) > 0) {
                maxPerClass.set(color[v], w);
            }
        }
        BigInteger bound = BigInteger.ZERO;
        for (BigInteger m : maxPerClass) {
            if (m != null && m.signum() > 0) {
                bound = bound.add(m);
            }
        }
        return bound;
    }

    /** Best (smallest) of the available bounds. */
    public static BigInteger best(Graph graph, BigInteger[] weights) {
        return positiveWeightSum(weights).min(cliqueCoverBound(graph, weights));
    }
}
