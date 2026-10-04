package com.local.mwis.core;

import com.local.mwis.exhaustive.BruteForceMwis;
import com.local.mwis.graph.Edge;
import com.local.mwis.graph.Graph;
import com.local.mwis.graph.TreeDecomposition;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.List;
import java.util.Random;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MwisSolverTest {

    private static BigInteger[] weightsOf(long... w) {
        BigInteger[] out = new BigInteger[w.length];
        for (int i = 0; i < w.length; i++) {
            out[i] = BigInteger.valueOf(w[i]);
        }
        return out;
    }

    private static SolveResult solve(Graph g, BigInteger[] w, TreeDecomposition td, SolverOptions opts) {
        NiceDecomposition nice = new NiceDecompositionBuilder().build(td);
        return new MwisSolver(g, w, nice, opts).solve();
    }

    @Test
    void trianglePicksHeaviestVertex() {
        Graph g = Graph.of(3, List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(0, 2)));
        TreeDecomposition td = TreeDecomposition.of(List.of(List.of(0, 1, 2)), List.of(), 0);
        SolveResult r = solve(g, weightsOf(1, 2, 3), td, SolverOptions.unlimited());
        assertEquals(BigInteger.valueOf(3), r.weight());
        assertEquals(List.of(2), r.independentSet());
    }

    @Test
    void emptyGraphSelectsEverything() {
        Graph g = Graph.of(3, List.of());
        TreeDecomposition td = TreeDecomposition.of(List.of(List.of(0, 1, 2)), List.of(), 0);
        SolveResult r = solve(g, weightsOf(1, 2, 3), td, SolverOptions.unlimited());
        assertEquals(BigInteger.valueOf(6), r.weight());
        assertEquals(List.of(0, 1, 2), r.independentSet());
    }

    @Test
    void bigIntegerWeightsBeyondLongRange() {
        // two isolated vertices with weights above 2^63: both must be taken
        Graph g = Graph.of(2, List.of());
        TreeDecomposition td = TreeDecomposition.of(List.of(List.of(0, 1)), List.of(), 0);
        BigInteger big1 = BigInteger.TWO.pow(70).add(BigInteger.valueOf(12345));
        BigInteger big2 = BigInteger.TWO.pow(69);
        SolveResult r = solve(g, new BigInteger[]{big1, big2}, td, SolverOptions.unlimited());
        assertEquals(big1.add(big2), r.weight());
        assertEquals(List.of(0, 1), r.independentSet());
    }

    @Test
    void budgetExceededAbortsWithDedicatedException() {
        RandomTwoTrees.Instance inst = RandomTwoTrees.generate(14, 42L);
        BigInteger[] w = weightsOf(new long[14]);
        java.util.Arrays.fill(w, BigInteger.ONE);
        assertThrows(BudgetExceededException.class,
                () -> solve(inst.graph(), w, inst.decomposition(), new SolverOptions(2)));
    }

    @Test
    void statsRespectBudgetAndCountEntries() {
        RandomTwoTrees.Instance inst = RandomTwoTrees.generate(10, 7L);
        BigInteger[] w = weightsOf(new long[10]);
        java.util.Arrays.fill(w, BigInteger.ONE);
        SolveResult r = solve(inst.graph(), w, inst.decomposition(), new SolverOptions(100_000));
        SolverStats s = r.stats();
        assertTrue(s.tableEntries() > 0);
        assertTrue(s.tableEntries() <= 100_000);
        assertEquals(s.nodesProcessed(),
                s.leafNodes() + s.introduceNodes() + s.forgetNodes() + s.joinNodes());
        assertTrue(s.joinNodes() > 0, "2-tree decomposition should produce joins");
    }

    @Test
    void backtrackedSetIsIndependentAndMatchesWeight() {
        Random rng = new Random(99L);
        for (int trial = 0; trial < 30; trial++) {
            RandomTwoTrees.Instance inst = RandomTwoTrees.generate(5 + rng.nextInt(8), rng.nextLong());
            BigInteger[] w = new BigInteger[inst.graph().vertexCount()];
            for (int i = 0; i < w.length; i++) {
                w[i] = BigInteger.valueOf(rng.nextInt(41) - 20);
            }
            SolveResult r = solve(inst.graph(), w, inst.decomposition(), SolverOptions.unlimited());
            // independent?
            for (Edge e : inst.graph().edges()) {
                assertTrue(!(r.independentSet().contains(e.u()) && r.independentSet().contains(e.v())),
                        "selected endpoints of " + e);
            }
            // weight matches?
            BigInteger sum = BigInteger.ZERO;
            for (int v : r.independentSet()) {
                sum = sum.add(w[v]);
            }
            assertEquals(r.weight(), sum);
            // non-negative because the empty set is always feasible
            assertTrue(r.weight().signum() >= 0);
        }
    }

    @Test
    void agreesWithExhaustiveEnumerationOnRandomInstances() {
        Random rng = new Random(20261004L);
        BruteForceMwis oracle = new BruteForceMwis();
        for (int trial = 0; trial < 25; trial++) {
            int n = 4 + rng.nextInt(11); // 4..14 vertices
            RandomTwoTrees.Instance inst = RandomTwoTrees.generate(n, rng.nextLong());
            BigInteger[] w = new BigInteger[n];
            for (int i = 0; i < n; i++) {
                w[i] = BigInteger.valueOf(rng.nextInt(61) - 30);
            }
            SolveResult dp = solve(inst.graph(), w, inst.decomposition(), SolverOptions.unlimited());
            assertEquals(oracle.solve(inst.graph(), w).weight(), dp.weight(),
                    "trial " + trial + " n=" + n);
        }
    }
}
