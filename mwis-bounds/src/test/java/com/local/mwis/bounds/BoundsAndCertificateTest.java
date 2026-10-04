package com.local.mwis.bounds;

import com.local.mwis.graph.Edge;
import com.local.mwis.graph.Graph;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.List;
import java.util.Random;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class BoundsAndCertificateTest {

    private final CertificateVerifier verifier = new CertificateVerifier();

    private static BigInteger[] w(long... vals) {
        BigInteger[] out = new BigInteger[vals.length];
        for (int i = 0; i < vals.length; i++) {
            out[i] = BigInteger.valueOf(vals[i]);
        }
        return out;
    }

    @Test
    void validCertificateAccepted() {
        Graph g = Graph.of(4, List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(2, 3)));
        CertificateCheck c = verifier.verify(g, w(3, 2, 5, 1), List.of(0, 2), BigInteger.valueOf(8));
        assertTrue(c.independent());
        assertTrue(c.weightMatches());
        assertTrue(c.accepted());
        assertEquals(BigInteger.valueOf(8), c.computedWeight());
    }

    @Test
    void adjacentSelectionRejected() {
        Graph g = Graph.of(3, List.of(Edge.of(0, 1), Edge.of(1, 2)));
        CertificateCheck c = verifier.verify(g, w(1, 1, 1), List.of(0, 1), BigInteger.valueOf(2));
        assertFalse(c.independent());
        assertFalse(c.accepted());
        assertEquals(1, c.violations().size());
        assertEquals(Edge.of(0, 1), c.violations().get(0));
    }

    @Test
    void weightMismatchRejected() {
        Graph g = Graph.of(3, List.of(Edge.of(0, 1)));
        CertificateCheck c = verifier.verify(g, w(5, 1, 2), List.of(0, 2), BigInteger.valueOf(99));
        assertTrue(c.independent());
        assertFalse(c.weightMatches());
        assertFalse(c.accepted());
        assertEquals(BigInteger.valueOf(7), c.computedWeight());
    }

    @Test
    void unknownVertexRejected() {
        Graph g = Graph.of(2, List.of());
        CertificateCheck c = verifier.verify(g, w(1, 1), List.of(0, 9), BigInteger.valueOf(1));
        assertFalse(c.independent());
        assertEquals(List.of(9), c.unknownVertices());
    }

    @Test
    void cliqueCoverBoundNeverBelowPositiveSum_andNeverBelowOptimum() {
        // exhaustive optimum on random graphs must not exceed either bound
        Random rng = new Random(1234L);
        for (int trial = 0; trial < 40; trial++) {
            int n = 2 + rng.nextInt(8);
            List<Edge> edges = new java.util.ArrayList<>();
            for (int u = 0; u < n; u++) {
                for (int v = u + 1; v < n; v++) {
                    if (rng.nextBoolean()) {
                        edges.add(Edge.of(u, v));
                    }
                }
            }
            Graph g = Graph.of(n, edges);
            BigInteger[] weights = w(new long[n]);
            for (int i = 0; i < n; i++) {
                weights[i] = BigInteger.valueOf(rng.nextInt(21) - 5);
            }
            BigInteger posSum = UpperBounds.positiveWeightSum(weights);
            BigInteger clique = UpperBounds.cliqueCoverBound(g, weights);
            assertTrue(clique.compareTo(posSum) <= 0, "clique bound should dominate positive sum");
            // brute-force optimum for comparison (tiny n, inline enumeration)
            BigInteger optimum = bruteForce(g, weights);
            assertTrue(optimum.compareTo(clique) <= 0,
                    "optimum " + optimum + " exceeds clique bound " + clique);
        }
    }

    @Test
    void optimalityAssessmentClassifies() {
        OptimalityAssessment proven = OptimalityAssessment.of(
                BigInteger.valueOf(8), BigInteger.valueOf(8));
        assertEquals(OptimalityAssessment.Status.PROVEN_OPTIMAL, proven.status());
        assertEquals(BigInteger.ZERO, proven.gap());
        OptimalityAssessment gap = OptimalityAssessment.of(
                BigInteger.valueOf(5), BigInteger.valueOf(9));
        assertEquals(OptimalityAssessment.Status.GAP, gap.status());
        assertEquals(BigInteger.valueOf(4), gap.gap());
    }

    private static BigInteger bruteForce(Graph g, BigInteger[] weights) {
        int n = g.vertexCount();
        BigInteger best = BigInteger.ZERO;
        for (long mask = 0; mask < (1L << n); mask++) {
            BigInteger sum = BigInteger.ZERO;
            boolean ok = true;
            for (Edge e : g.edges()) {
                if ((mask & (1L << e.u())) != 0 && (mask & (1L << e.v())) != 0) {
                    ok = false;
                    break;
                }
            }
            if (ok) {
                for (int v = 0; v < n; v++) {
                    if ((mask & (1L << v)) != 0) {
                        sum = sum.add(weights[v]);
                    }
                }
                best = best.max(sum);
            }
        }
        return best;
    }
}
