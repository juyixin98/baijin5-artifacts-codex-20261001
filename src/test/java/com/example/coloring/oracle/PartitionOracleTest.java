package com.example.coloring.oracle;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.example.coloring.model.Graph;
import com.example.coloring.model.Graphs;
import com.example.coloring.model.SimpleGraph;
import java.math.BigInteger;
import java.util.List;
import org.junit.jupiter.api.Test;

class PartitionOracleTest {

    @Test
    void emptyGraphHasBellNumbersAsProperPartitions() {
        PartitionOracle.Answer answer = PartitionOracle.analyze(Graphs.empty(4));
        // Bell(4) = 15 partitions, every one is proper without edges.
        assertEquals(new BigInteger("15"), answer.totalPartitions());
        // S_G(b) for b = 0..4 on the edgeless graph = Stirling numbers row 4.
        assertEquals(List.of(
                BigInteger.ZERO,
                BigInteger.ONE,
                BigInteger.valueOf(7),
                BigInteger.valueOf(6),
                BigInteger.ONE).size() - 1, 4);
        assertEquals(BigInteger.ZERO, answer.properPartitions(0));
        assertEquals(BigInteger.ONE, answer.properPartitions(1));
        assertEquals(BigInteger.valueOf(7), answer.properPartitions(2));
        assertEquals(BigInteger.valueOf(6), answer.properPartitions(3));
        assertEquals(BigInteger.ONE, answer.properPartitions(4));
        assertEquals(BigInteger.ZERO, answer.properPartitions(5));
    }

    @Test
    void completeGraphOnlyAllowsSingletonBlocks() {
        PartitionOracle.Answer answer = PartitionOracle.analyze(Graphs.complete(4));
        for (int b = 0; b < 4; b++) {
            assertEquals(BigInteger.ZERO, answer.properPartitions(b));
        }
        assertEquals(BigInteger.ONE, answer.properPartitions(4));
    }

    @Test
    void oddCycleChromaticNumberIsThree() {
        assertEquals(3, PartitionOracle.analyze(Graphs.cycle(5)).chromatic());
        assertEquals(2, PartitionOracle.analyze(Graphs.cycle(4)).chromatic());
    }

    @Test
    void labeledPaletteCountsAreChromaticPolynomialValues() {
        // chi_P(K_n, k) = k(k-1)...(k-n+1)
        PartitionOracle.Answer k3 = PartitionOracle.analyze(Graphs.complete(3));
        assertEquals(BigInteger.ZERO, k3.labeledColorings(2));
        assertEquals(BigInteger.valueOf(6), k3.labeledColorings(3));
        assertEquals(BigInteger.valueOf(24), k3.labeledColorings(4));

        // C5 chromatic polynomial: (k-1)^5 + (k-1)
        PartitionOracle.Answer c5 = PartitionOracle.analyze(Graphs.cycle(5));
        assertEquals(BigInteger.ZERO, c5.labeledColorings(2));
        assertEquals(BigInteger.valueOf(30), c5.labeledColorings(3));
        assertEquals(BigInteger.valueOf(240), c5.labeledColorings(4));

        // Edgeless graph: k^n
        PartitionOracle.Answer e3 = PartitionOracle.analyze(Graphs.empty(3));
        assertEquals(BigInteger.valueOf(27), e3.labeledColorings(3));
    }

    @Test
    void zeroVertexGraphHasExactlyOneColoringForEveryPalette() {
        PartitionOracle.Answer empty = PartitionOracle.analyze(Graphs.empty(0));
        assertEquals(0, empty.chromatic());
        assertEquals(BigInteger.ONE, empty.labeledColorings(0));
        assertEquals(BigInteger.ONE, empty.labeledColorings(3));
    }

    @Test
    void directPaletteRecursionAgreesWithPartitionFormula() {
        for (SimpleGraph graph : List.of(
                Graphs.cycle(5), Graphs.cycle(4), Graphs.complete(4),
                Graphs.path(5), Graphs.empty(4),
                Graphs.disjointUnion(Graphs.complete(3), Graphs.cycle(4)))) {
            PartitionOracle.Answer answer = PartitionOracle.analyze(graph);
            for (int k = 0; k <= graph.n(); k++) {
                BigInteger direct = LabeledColoringCounter.count(graph, k);
                assertEquals(answer.labeledColorings(k), direct,
                        "counting disagreement for k=" + k);
            }
        }
    }
}
