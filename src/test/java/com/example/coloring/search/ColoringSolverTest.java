package com.example.coloring.search;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.coloring.TestFixtures;
import com.example.coloring.certificate.CertificateVerifier;
import com.example.coloring.certificate.CertificateVerdict;
import com.example.coloring.diag.PruningReason;
import com.example.coloring.model.Graph;
import com.example.coloring.model.Graphs;
import java.math.BigInteger;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

class ColoringSolverTest {

    private final ColoringSolver solver = new ColoringSolver();

    @ParameterizedTest
    @MethodSource("com.example.coloring.TestFixtures#all")
    void everyFixtureIsSolvedToExactChromaticNumber(TestFixtures.NamedGraph named) {
        ColoringResult result = solver.solve(named.graph());
        assertEquals(ColoringStatus.OPTIMAL, result.status(),
                "expected exact answer for " + named.name());
        assertEquals(named.chromatic(), result.chromatic().orElseThrow(),
                "chromatic mismatch for " + named.name());
        assertEquals(named.chromatic(), result.lowerBound());
        assertEquals(named.chromatic(), result.upperBound());
        assertProper(named.graph(), result.coloring());

        CertificateVerdict verdict = CertificateVerifier.verify(named.graph(), result);
        assertTrue(verdict.accepted(), () -> "certificate rejected for "
                + named.name() + ": " + verdict.errors());
    }

    @Test
    void oddCycleRequiresThreeColorsWithEdgeAndCliqueWitnesses() {
        ColoringResult c5 = solver.solve(Graphs.cycle(5), BigInteger.valueOf(10_000));
        assertEquals(3, c5.chromatic().orElseThrow());
        assertEquals(2, c5.cliqueWitness().length);
        assertProper(Graphs.cycle(5), c5.coloring());

        ColoringResult c9 = solver.solve(Graphs.cycle(9));
        assertEquals(3, c9.chromatic().orElseThrow());
    }

    @Test
    void completeGraphNeedsOneColorPerVertex() {
        for (int n = 1; n <= 7; n++) {
            ColoringResult result = solver.solve(Graphs.complete(n));
            assertEquals(n, result.chromatic().orElseThrow());
            assertEquals(n, result.cliqueWitness().length);
        }
    }

    @Test
    void disconnectedGraphTakesMaximumOfComponentValues() {
        Graph k3PlusK2 = TestFixtures.load("k3-plus-k2.graph");
        ColoringResult r1 = solver.solve(k3PlusK2);
        assertEquals(3, r1.chromatic().orElseThrow());

        Graph c5PlusK4 = TestFixtures.load("c5-plus-k4.graph");
        ColoringResult r2 = solver.solve(c5PlusK4);
        assertEquals(4, r2.chromatic().orElseThrow());
        assertProper(c5PlusK4, r2.coloring());
    }

    @Test
    void canonicalColoringOnC5MatchesHandWrittenFullColorReference() {
        // Small-graph full color assignment reference for the exact search:
        // canonical restricted-growth assignment in deterministic DSATUR order.
        ColoringResult result = solver.solve(Graphs.cycle(5));
        assertArrayEquals(new int[] {0, 1, 0, 1, 2}, result.coloring());
    }

    @Test
    void evenCycleHasHandWrittenTwoColorReference() {
        ColoringResult result = solver.solve(Graphs.cycle(6));
        assertArrayEquals(new int[] {0, 1, 0, 1, 0, 1}, result.coloring());
        assertEquals(2, result.chromatic().orElseThrow());
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
