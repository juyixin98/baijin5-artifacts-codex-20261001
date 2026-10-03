package com.example.coloring.search;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.example.coloring.model.Graph;
import com.example.coloring.model.Graphs;
import com.example.coloring.model.SimpleGraph;
import com.example.coloring.oracle.GraphEnumerator;
import java.math.BigInteger;
import java.util.HashSet;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * Independently verifies behavior contract 1: color-rename symmetry pruning must
 * merge only renamings of the SAME partition, never delete different partitions.
 *
 * <p>The test collects the normalized partition signatures of every coloring
 * visited by the canonical k-colorability search (unrestricted palette) and
 * compares their count with the independent partition oracle: for an edgeless
 * graph every one of the Bell(n) partitions must be reachable.
 */
class SymmetryPartitionTest {

    /** Counts the distinct proper partitions found by unrestricted canonical search. */
    private static long countReachablePartitions(Graph graph, int k) {
        Set<String> signatures = new HashSet<>();
        PartitionCollector.collect(graph, k, signatures);
        return signatures.size();
    }

    @Test
    void edgelessGraphReachesAllBellPartitions() {
        // Bell numbers B(0)..B(6)
        long[] bell = {1, 1, 2, 5, 15, 52, 203};
        for (int n = 0; n <= 6; n++) {
            assertEquals(bell[n], countReachablePartitions(Graphs.empty(n), n),
                    "missing partitions of edgeless graph n=" + n);
        }
    }

    @ParameterizedTest
    @ValueSource(ints = {2, 3})
    void everySmallGraphReachesExactlyItsProperPartitionsWithPalette(int order) {
        long[] expected = new long[1];
        GraphEnumerator.forEachGraph(order, graph -> {
            long oraclePartitions = com.example.coloring.oracle.PartitionOracle
                    .analyze(graph)
                    .properPartitionsByBlocks()
                    .stream()
                    .mapToLong(BigInteger::longValueExact)
                    .sum();
            expected[0] = oraclePartitions;
            long reachable = countReachablePartitions(graph, order);
            assertEquals(oraclePartitions, reachable,
                    "symmetry pruning lost/duplicated a partition of a graph of order " + order);
        });
    }
}
