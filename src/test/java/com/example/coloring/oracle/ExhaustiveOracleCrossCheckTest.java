package com.example.coloring.oracle;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.coloring.certificate.CertificateVerifier;
import com.example.coloring.model.SimpleGraph;
import com.example.coloring.search.ColoringResult;
import com.example.coloring.search.ColoringSolver;
import com.example.coloring.search.ColoringStatus;
import java.math.BigInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * Independent exhaustive control: for EVERY labeled simple graph up to n=6 the
 * solver must match the partition oracle's chromatic number, and both BigInteger
 * counting methods must agree. The oracle never calls solver code.
 */
class ExhaustiveOracleCrossCheckTest {

    private final ColoringSolver solver = new ColoringSolver();

    @ParameterizedTest
    @ValueSource(ints = {0, 1, 2, 3, 4, 5, 6})
    void solverMatchesOracleOnEveryLabeledGraph(int order) {
        AtomicLong checked = new AtomicLong();
        AtomicReference<String> firstMismatch = new AtomicReference<>();

        GraphEnumerator.forEachGraph(order, graph -> {
            int oracleChi = PartitionOracle.analyze(graph).chromatic();
            ColoringResult result = solver.solve(graph, ColoringSolver.UNLIMITED_BUDGET);
            if (result.status() != ColoringStatus.OPTIMAL
                    || result.chromatic().orElse(-1) != oracleChi
                    || !CertificateVerifier.verify(graph, result).accepted()) {
                firstMismatch.compareAndSet(null,
                        "n=" + order + " edges=" + graph.edgeCount());
            }
            checked.incrementAndGet();
        });

        assertEquals(GraphEnumerator.count(order), BigInteger.valueOf(checked.get()));
        assertTrue(firstMismatch.get() == null,
                () -> "mismatch on a graph: " + firstMismatch.get());
    }

    @Test
    void bothIndependentCountersAgreeOnEveryGraphUpToFive() {
        AtomicLong checked = new AtomicLong();
        for (int order = 0; order <= 5; order++) {
            final int n = order;
            GraphEnumerator.forEachGraph(n, graph -> {
                PartitionOracle.Answer answer = PartitionOracle.analyze(graph);
                for (int k = 0; k <= n; k++) {
                    assertEquals(answer.labeledColorings(k),
                            LabeledColoringCounter.count(graph, k));
                }
                checked.incrementAndGet();
            });
        }
        // 1 + 1 + 2 + 8 + 64 + 1024 labeled graphs for n = 0..5
        assertEquals(1100L, checked.get());
    }

    @Test
    void enumerationCountIsTwoToTheEdgeSlots() {
        assertEquals(BigInteger.ONE, GraphEnumerator.count(0));
        assertEquals(BigInteger.ONE, GraphEnumerator.count(1));
        assertEquals(BigInteger.valueOf(2), GraphEnumerator.count(2));
        assertEquals(BigInteger.valueOf(64), GraphEnumerator.count(4));
        assertEquals(new BigInteger("32768"), GraphEnumerator.count(6));
    }
}
