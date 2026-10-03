package com.example.coloring.bound;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.coloring.TestFixtures;
import com.example.coloring.model.Graph;
import com.example.coloring.model.Graphs;
import org.junit.jupiter.api.Test;

class BoundWitnessTest {

    @Test
    void cliqueWitnessIsAPairwiseAdjointSetOfExpectedSize() {
        for (TestFixtures.NamedGraph named : TestFixtures.all()) {
            int[] clique = MaxClique.find(named.graph());
            assertEquals(named.cliqueNumber(), clique.length,
                    "clique number mismatch for " + named.name());
            for (int i = 0; i < clique.length; i++) {
                for (int j = i + 1; j < clique.length; j++) {
                    assertTrue(named.graph().adjacent(clique[i], clique[j]),
                            "witness for " + named.name() + " is not a clique");
                }
            }
        }
    }

    @Test
    void greedyUpperBoundIsAlwaysAFeasibleColoring() {
        for (TestFixtures.NamedGraph named : TestFixtures.all()) {
            GreedyColoring.Result result = GreedyColoring.color(named.graph());
            assertProper(named.graph(), result.coloring());
            assertTrue(result.colors() >= named.chromatic(),
                    "heuristic upper bound cannot be below chi for " + named.name());
        }
    }

    @Test
    void greedyIsOptimalOnFamiliesAndCycles() {
        assertEquals(3, GreedyColoring.color(Graphs.complete(3)).colors());
        assertEquals(1, GreedyColoring.color(Graphs.empty(5)).colors());
        assertEquals(2, GreedyColoring.color(Graphs.cycle(4)).colors());
        assertEquals(2, GreedyColoring.color(Graphs.path(7)).colors());
    }

    static void assertProper(Graph graph, int[] coloring) {
        for (int u = 0; u < graph.n(); u++) {
            for (int v : graph.neighbors(u)) {
                assertTrue(coloring[u] != coloring[v],
                        "monochromatic edge " + u + "-" + v);
            }
        }
    }
}
