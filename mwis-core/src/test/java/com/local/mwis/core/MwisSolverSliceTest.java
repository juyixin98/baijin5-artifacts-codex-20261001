package com.local.mwis.core;

import com.local.mwis.graph.Edge;
import com.local.mwis.graph.Graph;
import com.local.mwis.graph.TreeDecomposition;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Minimal vertical slice: hand-computed optima on tiny graphs. */
class MwisSolverSliceTest {

    private static SolveResult solve(Graph g, long[] w, TreeDecomposition td) {
        BigInteger[] weights = new BigInteger[w.length];
        for (int i = 0; i < w.length; i++) {
            weights[i] = BigInteger.valueOf(w[i]);
        }
        NiceDecomposition nice = new NiceDecompositionBuilder().build(td);
        return new MwisSolver(g, weights, nice, SolverOptions.unlimited()).solve();
    }

    @Test
    void pathOfFourHandComputed() {
        // path 0-1-2-3, weights 3,2,5,1 -> optimum {0,2} weight 8 (verified by hand)
        Graph g = Graph.of(4, List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(2, 3)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 1), List.of(1, 2), List.of(2, 3)),
                List.of(Edge.of(0, 1), Edge.of(1, 2)),
                0);
        SolveResult r = solve(g, new long[]{3, 2, 5, 1}, td);
        assertEquals(BigInteger.valueOf(8), r.weight());
        assertEquals(List.of(0, 2), r.independentSet());
    }

    @Test
    void allNegativeWeightsYieldEmptySet() {
        Graph g = Graph.of(3, List.of(Edge.of(0, 1), Edge.of(1, 2)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 1), List.of(1, 2)),
                List.of(Edge.of(0, 1)),
                0);
        SolveResult r = solve(g, new long[]{-5, -1, -7}, td);
        assertEquals(BigInteger.ZERO, r.weight());
        assertTrue(r.independentSet().isEmpty());
    }

    @Test
    void joinNodeDeduplicatesSharedVertexWeights() {
        // path 0-1-2-3-4 rooted so the nice decomposition contains a JOIN;
        // unit weights -> optimum {0,2,4} weight 3 (verified by hand)
        Graph g = Graph.of(5, List.of(
                Edge.of(0, 1), Edge.of(1, 2), Edge.of(2, 3), Edge.of(3, 4)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 1), List.of(1, 2), List.of(2, 3), List.of(3, 4)),
                List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(2, 3)),
                1);
        NiceDecomposition nice = new NiceDecompositionBuilder().build(td);
        assertTrue(nice.nodes().stream().anyMatch(n -> n.kind == NiceNode.Kind.JOIN),
                "expected at least one JOIN node");
        BigInteger[] w = new BigInteger[5];
        java.util.Arrays.fill(w, BigInteger.ONE);
        SolveResult r = new MwisSolver(g, w, nice, SolverOptions.unlimited()).solve();
        assertEquals(BigInteger.valueOf(3), r.weight());
        assertEquals(List.of(0, 2, 4), r.independentSet());
    }
}
