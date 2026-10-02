package com.example.clique;

import com.example.clique.bound.CliqueBounds;
import com.example.clique.bound.MaximumClique;
import com.example.clique.graph.Graph;
import com.example.clique.search.CliqueEnumerator;
import com.example.clique.search.EnumerationLimits;
import com.example.clique.search.Strategy;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import static com.example.clique.TestSupport.graph;
import static com.example.clique.TestSupport.m;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Concrete, hand-computed expectations for both strategies. Expected cliques
 * are hardcoded here - not produced by the implementation under test.
 */
class MaximalCliqueEnumerationTest {

    private final CliqueEnumerator enumerator = new CliqueEnumerator();

    private void assertMaximal(Graph g, Set<BigInteger> expected) {
        for (Strategy strategy : Strategy.values()) {
            List<BigInteger> actual = enumerator.enumerateMaximal(
                    g, strategy, EnumerationLimits.unlimited());
            assertEquals(expected, Set.copyOf(actual), "strategy=" + strategy);
            assertEquals(actual.size(), new HashSet<>(actual).size(),
                    "duplicate output with strategy=" + strategy);
        }
    }

    @Test
    void emptyGraphYieldsNoCliques() {
        assertMaximal(graph(0), Set.of());
    }

    @Test
    void edgelessGraphYieldsSingletons() {
        assertMaximal(graph(3), Set.of(m(0), m(1), m(2)));
    }

    @Test
    void singleEdge() {
        assertMaximal(graph(2, 0, 1), Set.of(m(0, 1)));
    }

    @Test
    void isolatedVertexPlusEdge() {
        assertMaximal(graph(3, 0, 1), Set.of(m(0, 1), m(2)));
    }

    @Test
    void pathOfThree() {
        assertMaximal(graph(3, 0, 1, 1, 2), Set.of(m(0, 1), m(1, 2)));
    }

    @Test
    void triangle() {
        assertMaximal(graph(3, 0, 1, 1, 2, 0, 2), Set.of(m(0, 1, 2)));
    }

    @Test
    void bowtieTwoTrianglesSharingVertex() {
        Graph g = graph(5, 0, 1, 0, 2, 1, 2, 2, 3, 2, 4, 3, 4);
        assertMaximal(g, Set.of(m(0, 1, 2), m(2, 3, 4)));
    }

    @Test
    void chainOfOverlappingTriangles() {
        Graph g = graph(5, 0, 1, 0, 2, 1, 2, 1, 3, 2, 3, 2, 4, 3, 4);
        assertMaximal(g, Set.of(m(0, 1, 2), m(1, 2, 3), m(2, 3, 4)));
    }

    @Test
    void k4WithPendantSeparatesMaximalFromMaximum() {
        Graph g = graph(5, 0, 1, 0, 2, 0, 3, 1, 2, 1, 3, 2, 3, 3, 4);
        // {3,4} is maximal but NOT maximum; {0,1,2,3} is both.
        assertMaximal(g, Set.of(m(0, 1, 2, 3), m(3, 4)));

        List<BigInteger> cliques = enumerator.enumerateMaximal(
                g, Strategy.PIVOT, EnumerationLimits.unlimited());
        MaximumClique.Result max = MaximumClique.fromMaximalCliques(g, cliques);
        assertEquals(4, max.size());
        assertEquals(m(0, 1, 2, 3), max.witness());
        assertEquals(2, cliques.size(), "maximal count != maximum size: concepts stay separate");
        assertEquals(4, CliqueBounds.maxCliqueSizeUpperBound(g));
    }

    @Test
    void moonMoserNineVerticesYields27OverlappingCliques() {
        Graph g = TestSupport.moonMoser(3);
        for (Strategy strategy : Strategy.values()) {
            List<BigInteger> cliques = enumerator.enumerateMaximal(
                    g, strategy, EnumerationLimits.unlimited());
            assertEquals(27, cliques.size(), "strategy=" + strategy);
            assertEquals(27, new HashSet<>(cliques).size(), "unique output, strategy=" + strategy);
            assertTrue(cliques.stream().allMatch(c -> c.bitCount() == 3));
        }
    }

    @Test
    void duplicateEdgesDoNotChangeResult() {
        Graph withDupes = Graph.builder(3)
                .addEdge(0, 1).addEdge(0, 1).addEdge(1, 0)
                .addEdge(1, 2).addEdge(0, 2)
                .build();
        assertMaximal(withDupes, Set.of(m(0, 1, 2)));
    }

    @Test
    void enumerationIsDeterministicAcrossRuns() {
        Graph g = TestSupport.moonMoser(3);
        for (Strategy strategy : Strategy.values()) {
            List<BigInteger> first = enumerator.enumerateMaximal(g, strategy, EnumerationLimits.unlimited());
            List<BigInteger> second = enumerator.enumerateMaximal(g, strategy, EnumerationLimits.unlimited());
            assertEquals(first, second, "stable discovery order, strategy=" + strategy);
        }
    }
}
