package com.example.clique;

import com.example.clique.graph.Graph;
import com.example.clique.search.DegeneracyOrdering;
import org.junit.jupiter.api.Test;

import java.util.Arrays;

import static com.example.clique.TestSupport.graph;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DegeneracyOrderingTest {

    @Test
    void orderingIsPermutationOfAllVertices() {
        Graph g = graph(6, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 0, 5);
        int[] order = DegeneracyOrdering.compute(g).order();
        int[] sorted = order.clone();
        Arrays.sort(sorted);
        assertEquals(6, order.length);
        for (int i = 0; i < 6; i++) {
            assertEquals(i, sorted[i], "every vertex appears exactly once: no clique can be missed");
        }
    }

    @Test
    void treeHasDegeneracyOne() {
        Graph tree = graph(4, 0, 1, 1, 2, 1, 3);
        assertEquals(1, DegeneracyOrdering.compute(tree).degeneracy());
    }

    @Test
    void cycleHasDegeneracyTwo() {
        Graph cycle = graph(5, 0, 1, 1, 2, 2, 3, 3, 4, 4, 0);
        assertEquals(2, DegeneracyOrdering.compute(cycle).degeneracy());
    }

    @Test
    void completeGraphHasDegeneracyNMinusOne() {
        Graph k5 = TestSupport.moonMoser(1); // single part? no: build K5 directly
        Graph clique5 = graph(5, 0, 1, 0, 2, 0, 3, 0, 4, 1, 2, 1, 3, 1, 4, 2, 3, 2, 4, 3, 4);
        assertEquals(4, DegeneracyOrdering.compute(clique5).degeneracy());
        assertEquals(0, DegeneracyOrdering.compute(graph(4)).degeneracy());
    }

    @Test
    void moonMoserDegeneracyIsNMinusPartSize() {
        assertEquals(6, DegeneracyOrdering.compute(TestSupport.moonMoser(3)).degeneracy());
    }

    @Test
    void degeneracyUpperBoundHoldsOnSeededGraphs() {
        java.util.Random rng = new java.util.Random(20261003L);
        for (int trial = 0; trial < 50; trial++) {
            int n = 4 + rng.nextInt(6);
            com.example.clique.graph.GraphBuilder b = Graph.builder(n);
            for (int u = 0; u < n; u++) {
                for (int v = u + 1; v < n; v++) {
                    if (rng.nextDouble() < 0.5) {
                        b.addEdge(u, v);
                    }
                }
            }
            Graph g = b.build();
            var cliques = new com.example.clique.search.CliqueEnumerator()
                    .enumerateMaximal(g, com.example.clique.search.Strategy.DEGENERACY,
                            com.example.clique.search.EnumerationLimits.unlimited());
            int maxSize = com.example.clique.bound.CliqueBounds.observedMaxSize(cliques);
            assertTrue(maxSize <= DegeneracyOrdering.compute(g).degeneracy() + 1,
                    "omega <= degeneracy+1 violated on trial " + trial);
        }
    }
}
