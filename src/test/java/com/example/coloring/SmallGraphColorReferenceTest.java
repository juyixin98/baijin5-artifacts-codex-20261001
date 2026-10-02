package com.example.coloring;

import com.example.coloring.cert.Coloring;
import com.example.coloring.graph.Graph;
import com.example.coloring.graph.Graphs;
import com.example.coloring.ref.BruteForceReference;
import com.example.coloring.ref.SetPartitionReference;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * "Small graph full color assignment reference": enumerates every concrete
 * proper coloring (not just a count), asserts the exact color arrays, and
 * checks the symmetry identity that each partition contributes (r)_k
 * labeled colorings.
 */
class SmallGraphColorReferenceTest {

    /** Enumerate every proper coloring with palette [0,r) as concrete arrays. */
    private static List<int[]> allColorings(Graph graph, int r) {
        List<int[]> out = new ArrayList<>();
        int[] colors = new int[graph.order()];
        enumerate(graph, r, 0, colors, out);
        return out;
    }

    private static void enumerate(Graph graph, int r, int vertex, int[] colors, List<int[]> out) {
        if (vertex == graph.order()) {
            out.add(colors.clone());
            return;
        }
        outer:
        for (int color = 0; color < r; color++) {
            var neighbors = graph.neighborSet(vertex);
            for (int w = neighbors.nextSetBit(0); w >= 0; w = neighbors.nextSetBit(w + 1)) {
                if (w < vertex && colors[w] == color) {
                    continue outer;
                }
            }
            colors[vertex] = color;
            enumerate(graph, r, vertex + 1, colors, out);
        }
    }

    @Test
    void c3HasExactlySixThreeColorings() {
        Graph k3 = Graphs.complete(3);
        List<int[]> colorings = allColorings(k3, 3);
        assertEquals(6, colorings.size());
        // Every coloring is a permutation of {0,1,2}.
        for (int[] colors : colorings) {
            assertEquals(3, colors.length);
            assertTrue(colors[0] != colors[1] && colors[1] != colors[2] && colors[0] != colors[2]);
        }
        // Spot-check two concrete assignments exist.
        assertTrue(colorings.stream().anyMatch(c -> c[0] == 0 && c[1] == 1 && c[2] == 2));
        assertTrue(colorings.stream().anyMatch(c -> c[0] == 2 && c[1] == 0 && c[2] == 1));
        // And none with two colors.
        assertEquals(BigInteger.ZERO, BruteForceReference.countProperColorings(k3, 2));
    }

    @Test
    void c5FullReferenceCounts() {
        Graph c5 = Graphs.cycle(5);
        assertEquals(0, allColorings(c5, 2).size());
        assertEquals(30, allColorings(c5, 3).size());
        // 30 labeled colorings split into partitions * (r)_k.
        BigInteger partitionsOfThree = SetPartitionReference.properPartitions(c5, 3);
        assertEquals(BigInteger.valueOf(5), partitionsOfThree, "C5 has 5 partitions into 3 classes");
        assertEquals(BigInteger.valueOf(30),
                partitionsOfThree.multiply(BigInteger.valueOf(3 * 2 * 1)));
    }

    @Test
    void everyEnumeratedColoringIsAnIndependentlyValidCertificate() {
        Graph graph = Graphs.union(Graphs.cycle(4), Graphs.complete(2));
        for (int r = 1; r <= 4; r++) {
            List<int[]> colorings = allColorings(graph, r);
            for (int[] colors : colorings) {
                int used = 0;
                for (int color : colors) {
                    used = Math.max(used, color + 1);
                }
                Coloring certificate = new Coloring(colors, r);
                assertTrue(certificate.isProper(graph));
            }
            assertEquals(BruteForceReference.countProperColorings(graph, r),
                    BigInteger.valueOf(colorings.size()));
        }
    }

    @Test
    void edgelessGraphEveryAssignmentIsProper() {
        Graph e3 = Graphs.edgeless(3);
        assertEquals(27, allColorings(e3, 3).size());
        // Only one set partition (the single block) despite 27 labeled assignments.
        assertEquals(BigInteger.ONE, SetPartitionReference.properPartitions(e3, 1));
        assertEquals(BigInteger.valueOf(27), BruteForceReference.countProperColorings(e3, 3));
    }
}
