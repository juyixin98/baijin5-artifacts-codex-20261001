package com.example.coloring.oracle;

import com.example.coloring.model.Graph;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;

/**
 * Independent exhaustive oracle for small graphs.
 *
 * <p>It deliberately shares no code with the solver: the chromatic number is
 * obtained by enumerating every set partition of the labeled vertex set via
 * restricted-growth sequences, testing directly whether each partition is a
 * proper coloring. The minimum number of blocks is therefore the chromatic
 * number by the definition "minimum number of independent sets partitioning
 * V(G)".
 *
 * <p>The same enumeration independently counts proper partitions per block
 * number and (via the surjective formula) the number of proper colorings with a
 * labeled palette of k colors. Counts use {@link BigInteger}.
 */
public final class PartitionOracle {

    /** Full oracle answer for one graph. */
    public record Answer(
            BigInteger totalPartitions,
            List<BigInteger> properPartitionsByBlocks,
            int chromatic,
            List<BigInteger> labeledColoringsByK) {

        /** Number of proper partitions with exactly {@code blocks} blocks (S_G(blocks)). */
        public BigInteger properPartitions(int blocks) {
            return blocks < properPartitionsByBlocks.size()
                    ? properPartitionsByBlocks.get(blocks)
                    : BigInteger.ZERO;
        }

        /** Number of proper colorings using a labeled palette of {@code k} colors.
         *  For the zero-vertex graph this is 1 for every palette size. */
        public BigInteger labeledColorings(int k) {
            if (chromatic == 0) {
                return BigInteger.ONE;
            }
            return k < labeledColoringsByK.size()
                    ? labeledColoringsByK.get(k)
                    : labeledColoringsByK.get(labeledColoringsByK.size() - 1)
                        .signum() == 0 ? BigInteger.ZERO : extend(k);
        }

        private BigInteger extend(int k) {
            // Recompute from the partition counts on demand for palettes larger
            // than n: sum_b S_G(b) * falling(k, b).
            BigInteger total = BigInteger.ZERO;
            for (int b = 1; b < properPartitionsByBlocks.size(); b++) {
                BigInteger ways = BigInteger.ONE;
                for (int i = 0; i < b; i++) {
                    ways = ways.multiply(BigInteger.valueOf(k - i));
                }
                total = total.add(properPartitionsByBlocks.get(b).multiply(ways));
            }
            return total;
        }
    }

    private final Graph graph;
    private final int n;
    private final BigInteger[] proper;
    private BigInteger totalPartitions = BigInteger.ZERO;

    private PartitionOracle(Graph graph) {
        this.graph = graph;
        this.n = graph.n();
        this.proper = new BigInteger[Math.max(n + 2, 2)];
        for (int i = 0; i < proper.length; i++) {
            proper[i] = BigInteger.ZERO;
        }
    }

    /**
     * Enumerates all Bell(n) set partitions. Intended for n &le; 12; feasibility
     * is the caller's responsibility (tests cap n at 6 for exhaustive runs).
     */
    public static Answer analyze(Graph graph) {
        PartitionOracle oracle = new PartitionOracle(graph);
        if (oracle.n == 0) {
            List<BigInteger> partitions = new ArrayList<>(List.of(BigInteger.ONE));
            return new Answer(BigInteger.ONE, partitions, 0,
                    List.of(BigInteger.ONE, BigInteger.ONE));
        }
        int[] a = new int[oracle.n];
        oracle.enumerate(a, 0, 0);

        int chi = 1;
        while (chi <= oracle.n && oracle.proper[chi].signum() == 0) {
            chi++;
        }

        List<BigInteger> labeled = new ArrayList<>(oracle.n + 1);
        for (int k = 0; k <= oracle.n; k++) {
            labeled.add(oracle.surjectiveColorings(k));
        }
        return new Answer(oracle.totalPartitions, List.of(oracle.proper),
                chi, List.copyOf(labeled));
    }

    private void enumerate(int[] a, int index, int blocks) {
        if (index == n) {
            totalPartitions = totalPartitions.add(BigInteger.ONE);
            int usedBlocks = 0;
            for (int c : a) {
                usedBlocks = Math.max(usedBlocks, c + 1);
            }
            if (isProperPartition(a, usedBlocks)) {
                proper[usedBlocks] = proper[usedBlocks].add(BigInteger.ONE);
            }
            return;
        }
        // Restricted-growth sequences: vertex 'index' joins one of the existing
        // blocks (0 .. blocks-1), or opens the canonical next block.
        for (int c = 0; c < blocks; c++) {
            a[index] = c;
            enumerate(a, index + 1, blocks);
        }
        a[index] = blocks;
        enumerate(a, index + 1, blocks + 1);
    }

    private boolean isProperPartition(int[] a, int blocks) {
        for (int u = 0; u < n; u++) {
            for (int v : graph.neighbors(u)) {
                if (u < v && a[u] == a[v]) {
                    return false;
                }
            }
        }
        return true;
    }

    /**
     * Proper colorings with a labeled palette of k colors:
     * sum_b S_G(b) * b! * C(k, b), i.e. every proper b-block partition onto an
     * ordered choice of b distinct palette colors.
     */
    private BigInteger surjectiveColorings(int k) {
        BigInteger total = BigInteger.ZERO;
        for (int b = 0; b <= Math.min(k, n); b++) {
            BigInteger choices = falling(k, b);
            total = total.add(proper[b].multiply(choices));
        }
        return total;
    }

    private static BigInteger falling(int k, int b) {
        BigInteger value = BigInteger.ONE;
        for (int i = 0; i < b; i++) {
            value = value.multiply(BigInteger.valueOf(k - i));
        }
        return value;
    }
}
