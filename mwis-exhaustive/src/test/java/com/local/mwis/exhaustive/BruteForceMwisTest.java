package com.local.mwis.exhaustive;

import com.local.mwis.graph.Edge;
import com.local.mwis.graph.Graph;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class BruteForceMwisTest {

    private static BigInteger[] w(long... vals) {
        BigInteger[] out = new BigInteger[vals.length];
        for (int i = 0; i < vals.length; i++) {
            out[i] = BigInteger.valueOf(vals[i]);
        }
        return out;
    }

    @Test
    void handComputedPath() {
        Graph g = Graph.of(4, List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(2, 3)));
        ExhaustiveResult r = new BruteForceMwis().solve(g, w(3, 2, 5, 1));
        assertEquals(BigInteger.valueOf(8), r.weight());
        assertEquals(List.of(0, 2), r.independentSet());
        assertEquals(16, r.subsetsTested());
    }

    @Test
    void negativeWeightsChooseEmptySet() {
        Graph g = Graph.of(3, List.of(Edge.of(0, 1)));
        ExhaustiveResult r = new BruteForceMwis().solve(g, w(-4, -9, -1));
        assertEquals(BigInteger.ZERO, r.weight());
        assertTrue(r.independentSet().isEmpty());
    }

    @Test
    void completeGraphPicksSingleBestVertex() {
        Graph g = Graph.of(4, List.of(Edge.of(0, 1), Edge.of(0, 2), Edge.of(0, 3),
                Edge.of(1, 2), Edge.of(1, 3), Edge.of(2, 3)));
        ExhaustiveResult r = new BruteForceMwis().solve(g, w(2, 9, 4, 1));
        assertEquals(BigInteger.valueOf(9), r.weight());
        assertEquals(List.of(1), r.independentSet());
    }

    @Test
    void refusesOversizedGraphs() {
        Graph g = Graph.of(25, List.of());
        assertThrows(InputTooLargeException.class,
                () -> new BruteForceMwis().solve(g, w(new long[25])));
    }
}
