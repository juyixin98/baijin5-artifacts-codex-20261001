package com.example.clique.search;

import com.example.clique.graph.Graph;

import java.math.BigInteger;

/**
 * Bron-Kerbosch with Tomita-style pivoting. The pivot is a vertex of P|X
 * maximising |P & N(u)|; only vertices of P \ N(pivot) are recursed on.
 * Reports maximal cliques only (never maximum-only, never non-maximal).
 */
final class BronKerboschPivot {

    private BronKerboschPivot() {
    }

    static void enumerate(Graph graph, CliqueSink sink) {
        expand(graph, BigInteger.ZERO, graph.vertices(), BigInteger.ZERO, sink);
    }

    static void expand(Graph graph, BigInteger r, BigInteger p, BigInteger x, CliqueSink sink) {
        if (p.signum() == 0) {
            if (x.signum() == 0) {
                sink.accept(r);
            }
            return;
        }
        int pivot = choosePivot(graph, p, x);
        BigInteger candidates = pivot < 0 ? p : p.andNot(graph.neighbors(pivot));
        BigInteger pRest = p;
        BigInteger xRest = x;
        BigInteger todo = candidates;
        while (todo.signum() != 0) {
            int v = todo.getLowestSetBit();
            todo = todo.clearBit(v);
            BigInteger nv = graph.neighbors(v);
            expand(graph, r.setBit(v), pRest.and(nv), xRest.and(nv), sink);
            pRest = pRest.clearBit(v);
            xRest = xRest.setBit(v);
        }
    }

    /** Vertex of P|X maximising |P & N(u)|; -1 when P|X is empty. */
    private static int choosePivot(Graph graph, BigInteger p, BigInteger x) {
        BigInteger union = p.or(x);
        int best = -1;
        int bestCount = -1;
        while (union.signum() != 0) {
            int u = union.getLowestSetBit();
            union = union.clearBit(u);
            int count = p.and(graph.neighbors(u)).bitCount();
            if (count > bestCount) {
                bestCount = count;
                best = u;
            }
        }
        return best;
    }
}
