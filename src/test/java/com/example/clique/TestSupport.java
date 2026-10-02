package com.example.clique;

import com.example.clique.graph.Graph;
import com.example.clique.graph.GraphBuilder;

import java.math.BigInteger;

/** Shared test helpers. */
final class TestSupport {

    private TestSupport() {
    }

    /** Vertex bit mask, e.g. {@code m(0,2)} == bits {0,2}. */
    static BigInteger m(int... vertices) {
        BigInteger out = BigInteger.ZERO;
        for (int v : vertices) {
            out = out.setBit(v);
        }
        return out;
    }

    /** Builds a graph from a flat edge list: {@code graph(3, 0,1, 1,2)}. */
    static Graph graph(int n, int... edges) {
        if (edges.length % 2 != 0) {
            throw new IllegalArgumentException("edges must be pairs");
        }
        GraphBuilder b = Graph.builder(n);
        for (int i = 0; i < edges.length; i += 2) {
            b.addEdge(edges[i], edges[i + 1]);
        }
        return b.build();
    }

    /**
     * Moon-Moser graph on 3k vertices: complete k-partite with parts of size 3.
     * Has exactly 3^k maximal cliques (all of size k) - the worst-case density
     * of maximal cliques, heavily overlapping.
     */
    static Graph moonMoser(int k) {
        int n = 3 * k;
        GraphBuilder b = Graph.builder(n);
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                if (u / 3 != v / 3) {
                    b.addEdge(u, v);
                }
            }
        }
        return b.build();
    }
}
