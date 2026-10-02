package com.example.coloring;

import com.example.coloring.diag.DiagnosticReport;
import com.example.coloring.diag.RequestContext;
import com.example.coloring.graph.Graph;
import com.example.coloring.graph.Graphs;
import com.example.coloring.ref.BruteForceReference;
import com.example.coloring.ref.ChromaticPolynomial;
import com.example.coloring.ref.SetPartitionReference;
import com.example.coloring.solver.ChromaticResult;
import com.example.coloring.solver.ChromaticSolver;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Independent cross-checks. The three references use disjoint machinery:
 * brute-force assignment enumeration, set-partition (Stirling) enumeration,
 * and deletion-contraction polynomials. None of them calls ChromaticSolver.
 */
class IndependentReferenceTest {

    private static long fallingFactorial(long r, int k) {
        long value = 1;
        for (int i = 0; i < k; i++) {
            value *= (r - i);
        }
        return value;
    }

    private static BigInteger polynomialColorings(Graph graph, int r) {
        BigInteger total = BigInteger.ZERO;
        for (int k = 0; k <= graph.order(); k++) {
            BigInteger partitions = SetPartitionReference.properPartitions(graph, k);
            total = total.add(partitions.multiply(BigInteger.valueOf(fallingFactorial(r, k))));
        }
        return total;
    }

    @Test
    void knownChromaticPolynomials() {
        // Empty/edgeless E_n: t^n
        assertEquals(BigInteger.valueOf(3).pow(4), ChromaticPolynomial.evaluate(Graphs.edgeless(4), 3));
        // Complete K_n: t(t-1)...(t-n+1)
        assertEquals(BigInteger.valueOf(3 * 2 * 1), ChromaticPolynomial.evaluate(Graphs.complete(3), 3));
        assertEquals(BigInteger.valueOf(5 * 4 * 3 * 2), ChromaticPolynomial.evaluate(Graphs.complete(4), 5));
        // C5: (t-1)^5 - (t-1) = 30 at t=3
        assertEquals(BigInteger.valueOf(30), ChromaticPolynomial.evaluate(Graphs.cycle(5), 3));
        assertEquals(BigInteger.ZERO, ChromaticPolynomial.evaluate(Graphs.cycle(5), 2));
        // Path P4: t(t-1)^3 = 3*8 = 24 at t=3
        assertEquals(BigInteger.valueOf(24), ChromaticPolynomial.evaluate(Graphs.path(4), 3));
    }

    @Test
    void stirlingNumbersHaveKnownValues() {
        // S(4,2)=7, S(5,2)=15, S(4,4)=1, S(4,1)=1
        assertEquals(BigInteger.valueOf(7), SetPartitionReference.ontoColorings(4, 2));
        assertEquals(BigInteger.valueOf(15), SetPartitionReference.ontoColorings(5, 2));
        assertEquals(BigInteger.ONE, SetPartitionReference.ontoColorings(4, 4));
        assertEquals(BigInteger.ONE, SetPartitionReference.ontoColorings(4, 1));
    }

    @Test
    void allThreeOraclesAgreeOnNamedGraphs() {
        Graph[] graphs = {
                Graphs.empty(), Graphs.edgeless(1), Graphs.edgeless(4),
                Graphs.complete(2), Graphs.complete(3), Graphs.complete(5),
                Graphs.path(4), Graphs.cycle(4), Graphs.cycle(5), Graphs.cycle(6),
                Graphs.petersen(),
                Graphs.union(Graphs.cycle(5), Graphs.complete(2))
        };
        for (Graph graphEach : graphs) {
            final Graph graph = graphEach;
            int chiPartition = SetPartitionReference.chromaticNumber(graph);
            int chiBrute = BruteForceReference.chromaticNumber(graph);
            assertEquals(chiPartition, chiBrute, "chi disagreement on " + graph);
            int rMax = Math.min(graph.order() + 1, 6);
            for (int rEach = 0; rEach <= rMax; rEach++) {
                final int r = rEach;
                BigInteger brute = BruteForceReference.countProperColorings(graph, r);
                BigInteger poly = ChromaticPolynomial.evaluate(graph, r);
                BigInteger viaPartitions = polynomialColorings(graph, r);
                assertEquals(brute, poly, () -> "brute vs polynomial at r=" + r + " on " + graph);
                assertEquals(brute, viaPartitions,
                        () -> "brute vs partition*r! identity at r=" + r + " on " + graph);
            }
        }
    }

    @Test
    void solverAgreesWithIndependentOracleOnAllSmallGraphs() {
        // Exhaustively every unlabeled simple graph up to n=6 (edge subsets).
        ChromaticSolver solver = new ChromaticSolver();
        for (int nEach = 0; nEach <= 5; nEach++) {
            final int n = nEach;
            int[][] pairs = new int[n * (n - 1) / 2][2];
            int index = 0;
            for (int i = 0; i < n; i++) {
                for (int j = i + 1; j < n; j++) {
                    pairs[index][0] = i;
                    pairs[index][1] = j;
                    index++;
                }
            }
            long total = 1L << pairs.length;
            for (long maskEach = 0; maskEach < total; maskEach++) {
                final long mask = maskEach;
                Graph.Builder builder = Graph.builder(n);
                for (int e = 0; e < pairs.length; e++) {
                    if (((mask >>> e) & 1L) == 1L) {
                        builder.edge(pairs[e][0], pairs[e][1]);
                    }
                }
                Graph graph = builder.build();
                int expected = SetPartitionReference.chromaticNumber(graph);
                ChromaticResult result = solver.solve(graph,
                        RequestContext.create(false), new DiagnosticReport());
                assertEquals(expected, result.chromaticNumber(),
                        () -> "solver chi mismatch on n=" + n + " mask=" + mask);
                assertEquals(expected, BruteForceReference.chromaticNumber(graph));
            }
        }
    }

    @Test
    void solverAgreesOnDeterministicRandomFixtures() {
        ChromaticSolver solver = new ChromaticSolver();
        for (int seedEach = 1; seedEach <= 30; seedEach++) {
            final int seed = seedEach;
            int order = 6 + (seed % 3);
            Graph graph = Graphs.randomErdosRenyi(order, seed % 3 + 1, 5, seed * 7919L);
            int expected = SetPartitionReference.chromaticNumber(graph);
            ChromaticResult result = solver.solve(graph);
            assertEquals(expected, result.chromaticNumber(), "seed " + seed);
            assertTrueColorCountEquals(graph, result, expected);
        }
    }

    private void assertTrueColorCountEquals(Graph graph, ChromaticResult result, int expected) {
        assertEquals(expected, result.upperBound());
        org.junit.jupiter.api.Assertions.assertTrue(result.coloring().isProper(graph));
    }
}
