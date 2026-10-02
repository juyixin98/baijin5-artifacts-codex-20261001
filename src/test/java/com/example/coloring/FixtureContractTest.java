package com.example.coloring;

import com.example.coloring.bounds.CliqueLowerBound;
import com.example.coloring.graph.GraphFixture;
import com.example.coloring.ref.BruteForceReference;
import com.example.coloring.ref.ChromaticPolynomial;
import com.example.coloring.solver.ChromaticResult;
import com.example.coloring.solver.ChromaticSolver;
import org.junit.jupiter.api.Test;

import java.io.InputStreamReader;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FixtureContractTest {

    private List<GraphFixture> fixtures() {
        try (Reader reader = new InputStreamReader(
                getClass().getResourceAsStream("/fixtures/graphs.gfc"), StandardCharsets.UTF_8)) {
            return GraphFixture.loadAll(reader);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @Test
    void loadsAllReusableFixtures() {
        List<GraphFixture> fixtures = fixtures();
        assertEquals(9, fixtures.size());
    }

    @Test
    void everyFixtureMatchesConcreteExpectedValues() {
        ChromaticSolver solver = new ChromaticSolver();
        for (GraphFixture fixture : fixtures()) {
            ChromaticResult result = solver.solve(fixture.graph);
            assertEquals(fixture.expectedChi, result.chromaticNumber(),
                    () -> "chi mismatch on fixture " + fixture.name);
            assertEquals(fixture.expectedClique,
                    CliqueLowerBound.maximumClique(fixture.graph).size(),
                    () -> "clique mismatch on fixture " + fixture.name);
            assertTrue(result.status().isExact(),
                    () -> "fixture " + fixture.name + " must prove an exact value");
            assertTrue(result.coloring().isProper(fixture.graph),
                    () -> "coloring certificate invalid on " + fixture.name);
        }
    }

    @Test
    void recordedColoringCountsAgreeWithIndependentOracles() {
        for (GraphFixture fixture : fixtures()) {
            fixture.expectedColorings.forEach((palette, expected) -> {
                if (palette <= 6) {
                    assertEquals(expected,
                            BruteForceReference.countProperColorings(fixture.graph, palette),
                            () -> "brute mismatch " + fixture.name + " @ " + palette);
                }
                assertEquals(expected,
                        ChromaticPolynomial.evaluate(fixture.graph, palette),
                        () -> "polynomial mismatch " + fixture.name + " @ " + palette);
            });
        }
    }
}
