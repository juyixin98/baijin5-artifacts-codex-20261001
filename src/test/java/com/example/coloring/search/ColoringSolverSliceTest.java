package com.example.coloring.search;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.coloring.model.Graphs;
import java.math.BigInteger;
import org.junit.jupiter.api.Test;

/** Vertical-slice test: bound machinery must already assert concrete chromatic numbers. */
class ColoringSolverSliceTest {

    private final ColoringSolver solver = new ColoringSolver();

    @Test
    void completeGraphIsOptimalWithExactValue() {
        ColoringResult result = solver.solve(Graphs.complete(5), BigInteger.valueOf(1000));
        assertEquals(ColoringStatus.OPTIMAL, result.status());
        assertEquals(5, result.chromatic().orElseThrow());
        assertEquals(5, result.lowerBound());
        assertEquals(5, result.upperBound());
        assertEquals(5, result.cliqueWitness().length);
    }

    @Test
    void edgelessNonEmptyGraphUsesOneColor() {
        ColoringResult result = solver.solve(Graphs.empty(4), BigInteger.ONE);
        assertEquals(ColoringStatus.OPTIMAL, result.status());
        assertEquals(1, result.chromatic().orElseThrow());
        assertEquals(1, result.upperBound());
    }

    @Test
    void zeroVertexGraphHasChromaticNumberZero() {
        ColoringResult result = solver.solve(Graphs.empty(0), BigInteger.ONE);
        assertEquals(ColoringStatus.OPTIMAL, result.status());
        assertEquals(0, result.chromatic().orElseThrow());
        assertEquals(0, result.upperBound());
        assertEquals(0, result.cliqueWitness().length);
    }

    @Test
    void oddCycleIsProvedThreeColorable() {
        ColoringResult result = solver.solve(Graphs.cycle(5), BigInteger.valueOf(1000));
        assertEquals(ColoringStatus.OPTIMAL, result.status());
        assertEquals(3, result.chromatic().orElseThrow());
        assertEquals(2, result.cliqueWitness().length);
        assertTrue(result.nodesUsed().compareTo(BigInteger.ZERO) > 0);
    }
}
