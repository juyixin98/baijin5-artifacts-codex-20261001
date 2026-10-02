package com.example.coloring.ref;

import com.example.coloring.graph.Graph;

import java.math.BigInteger;
import java.util.Arrays;

/**
 * Independent oracle based on set partitions.
 *
 * <p>{@code ontoColorings(n,k)} computes the Stirling number S(n,k).
 * {@code properPartitions(g,k)} enumerates partitions into independent sets,
 * which independently defines the chromatic number. The relation
 * {@code properColorings(g,r) = sum_k properPartitions(g,k) * (r)_k}
 * is the mathematical statement that color renaming never deletes a
 * distinct partition.</p>
 */
public final class SetPartitionReference {

    private SetPartitionReference() {
    }

    public static BigInteger ontoColorings(int n, int k) {
        if (k < 0 || k > n) {
            return BigInteger.ZERO;
        }
        BigInteger[] row = new BigInteger[k + 1];
        Arrays.fill(row, BigInteger.ZERO);
        row[0] = BigInteger.ONE;
        for (int i = 1; i <= n; i++) {
            BigInteger[] next = new BigInteger[k + 1];
            Arrays.fill(next, BigInteger.ZERO);
            for (int blocks = 1; blocks <= Math.min(i, k); blocks++) {
                next[blocks] = row[blocks - 1].add(row[blocks].multiply(BigInteger.valueOf(blocks)));
            }
            row = next;
        }
        return row[k];
    }

    public static BigInteger properPartitions(Graph graph, int k) {
        int n = graph.order();
        if (k < 0 || k > n) {
            return BigInteger.ZERO;
        }
        if (n == 0) {
            return k == 0 ? BigInteger.ONE : BigInteger.ZERO;
        }
        BigInteger[] count = new BigInteger[]{BigInteger.ZERO};
        int[] blockOf = new int[n];
        enumerate(graph, blockOf, 0, 0, k, count);
        return count[0];
    }

    private static void enumerate(Graph graph, int[] blockOf, int vertex, int usedBlocks,
                                  int target, BigInteger[] count) {
        if (vertex == graph.order()) {
            if (usedBlocks == target) {
                count[0] = count[0].add(BigInteger.ONE);
            }
            return;
        }
        for (int block = 0; block < usedBlocks; block++) {
            if (independentWith(graph, blockOf, vertex, block)) {
                blockOf[vertex] = block;
                enumerate(graph, blockOf, vertex + 1, usedBlocks, target, count);
            }
        }
        if (usedBlocks < target) {
            blockOf[vertex] = usedBlocks;
            enumerate(graph, blockOf, vertex + 1, usedBlocks + 1, target, count);
        }
    }

    private static boolean independentWith(Graph graph, int[] blockOf, int vertex, int block) {
        var neighbors = graph.neighborSet(vertex);
        for (int w = neighbors.nextSetBit(0); w >= 0; w = neighbors.nextSetBit(w + 1)) {
            if (w < vertex && blockOf[w] == block) {
                return false;
            }
        }
        return true;
    }

    public static int chromaticNumber(Graph graph) {
        for (int k = 0; k <= graph.order(); k++) {
            if (properPartitions(graph, k).signum() > 0) {
                return k;
            }
        }
        throw new IllegalStateException("unreachable");
    }
}
