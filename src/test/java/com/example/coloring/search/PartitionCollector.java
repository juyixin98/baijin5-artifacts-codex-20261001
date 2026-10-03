package com.example.coloring.search;

import com.example.coloring.model.Graph;
import java.util.Set;

/**
 * Third, test-local independent enumerator: generates every canonical
 * restricted-growth partition of n labeled vertices (the same equivalence
 * classes the solver's symmetry pruning claims to preserve) and records exactly
 * those that are proper colorings. It shares no code with either the solver or
 * the main oracle package, so agreement across the three implementations
 * validates the "distinct partitions are never deleted" contract.
 */
final class PartitionCollector {

    private PartitionCollector() {
    }

    static void collect(Graph graph, int maxBlocks, Set<String> signatures) {
        int n = graph.n();
        int[] a = new int[n];
        enumerate(graph, a, 0, 0, maxBlocks, signatures);
    }

    private static void enumerate(Graph graph, int[] a, int index, int blocks,
                                  int maxBlocks, Set<String> signatures) {
        if (index == a.length) {
            if (isProper(graph, a)) {
                signatures.add(signature(a));
            }
            return;
        }
        for (int c = 0; c < blocks; c++) {
            a[index] = c;
            enumerate(graph, a, index + 1, blocks, maxBlocks, signatures);
        }
        if (blocks < maxBlocks) {
            a[index] = blocks;
            enumerate(graph, a, index + 1, blocks + 1, maxBlocks, signatures);
        }
    }

    private static boolean isProper(Graph graph, int[] a) {
        for (int u = 0; u < a.length; u++) {
            for (int v : graph.neighbors(u)) {
                if (u < v && a[u] == a[v]) {
                    return false;
                }
            }
        }
        return true;
    }

    private static String signature(int[] a) {
        StringBuilder sb = new StringBuilder();
        for (int c : a) {
            sb.append(c).append('.');
        }
        return sb.toString();
    }
}
