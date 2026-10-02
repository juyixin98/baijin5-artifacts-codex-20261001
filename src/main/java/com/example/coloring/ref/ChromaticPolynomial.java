package com.example.coloring.ref;

import com.example.coloring.graph.Graph;

import java.math.BigInteger;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

/**
 * Independent oracle for the chromatic polynomial P_G(t), computed by
 * deletion-contraction {@code P_G = P_{G-e} - P_{G/e}} with BigInteger
 * coefficients and memoization.
 *
 * <p>The internal representation allows multi-edges (contraction can create
 * parallel edges) but they are de-duplicated, since parallel edges carry no
 * extra coloring constraint. {@code evaluate(g,t)} is the number of proper
 * t-colorings and is cross-checked against {@link BruteForceReference}.</p>
 */
public final class ChromaticPolynomial {

    private ChromaticPolynomial() {
    }

    public static BigInteger[] coefficients(Graph graph) {
        MultiGraph start = MultiGraph.from(graph);
        return new Solver().solve(start);
    }

    public static BigInteger evaluate(Graph graph, int t) {
        if (t < 0) {
            throw new IllegalArgumentException("t must be non-negative");
        }
        BigInteger[] coefficients = coefficients(graph);
        BigInteger value = BigInteger.ZERO;
        BigInteger power = BigInteger.ONE;
        for (BigInteger coefficient : coefficients) {
            value = value.add(coefficient.multiply(power));
            power = power.multiply(BigInteger.valueOf(t));
        }
        return value;
    }

    private static final class Solver {
        private final Map<String, BigInteger[]> cache = new HashMap<>();

        BigInteger[] solve(MultiGraph graph) {
            int[] edge = graph.firstEdge();
            if (edge == null) {
                BigInteger[] base = new BigInteger[graph.vertices + 1];
                Arrays.fill(base, BigInteger.ZERO);
                base[graph.vertices] = BigInteger.ONE; // t^n for the edgeless graph
                return base;
            }
            String key = graph.fingerprint();
            BigInteger[] cached = cache.get(key);
            if (cached != null) {
                return cached.clone();
            }
            MultiGraph deleted = graph.copy();
            deleted.deleteEdge(edge[0], edge[1]);
            MultiGraph contracted = graph.copy();
            contracted.contract(edge[0], edge[1]);

            BigInteger[] a = solve(deleted);
            BigInteger[] b = solve(contracted);
            BigInteger[] result = new BigInteger[Math.max(a.length, b.length)];
            Arrays.fill(result, BigInteger.ZERO);
            for (int i = 0; i < a.length; i++) {
                result[i] = result[i].add(a[i]);
            }
            for (int i = 0; i < b.length; i++) {
                result[i] = result[i].subtract(b[i]);
            }
            cache.put(key, result.clone());
            return result;
        }
    }
}
