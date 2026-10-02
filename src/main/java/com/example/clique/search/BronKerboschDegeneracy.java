package com.example.clique.search;

import com.example.clique.graph.Graph;

import java.math.BigInteger;

/**
 * Bron-Kerbosch with pivoting driven by a degeneracy ordering
 * (Eppstein-Loffler-Strash). For each vertex v in degeneracy order the search
 * runs with P = N(v) intersect later vertices and X = N(v) intersect earlier
 * vertices, so every maximal clique is reported exactly once and none is
 * missed: every maximal clique C has a unique earliest vertex in the ordering,
 * and C is found in that vertex's sub-search.
 */
final class BronKerboschDegeneracy {

    private BronKerboschDegeneracy() {
    }

    static void enumerate(Graph graph, CliqueSink sink) {
        int n = graph.vertexCount();
        int[] order = DegeneracyOrdering.compute(graph).order();
        BigInteger later = graph.vertices();
        BigInteger earlier = BigInteger.ZERO;
        for (int v : order) {
            later = later.clearBit(v);
            BigInteger p = graph.neighbors(v).and(later);
            BigInteger x = graph.neighbors(v).and(earlier);
            BronKerboschPivot.expand(graph, BigInteger.ONE.shiftLeft(v), p, x, sink);
            earlier = earlier.setBit(v);
        }
    }
}
