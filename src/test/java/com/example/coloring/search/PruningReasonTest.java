package com.example.coloring.search;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.coloring.TestFixtures;
import com.example.coloring.diag.CollectingDiagnostics;
import com.example.coloring.diag.PruningReason;
import com.example.coloring.model.Graph;
import com.example.coloring.model.Graphs;
import java.math.BigInteger;
import org.junit.jupiter.api.Test;

/** Each concrete pruning reason must really fire on the graph built to trigger it. */
class PruningReasonTest {

    @Test
    void cliqueLowerBoundRejectsBelowOmegaWithoutSearch() {
        // K4 + isolated vertex: omega=4 and greedy also gives 4, but the event
        // records that every k < 4 was excluded by the clique witness alone.
        CollectingDiagnostics diag = new CollectingDiagnostics();
        ColoringSolver solver = new ColoringSolver(diag);
        Graph graph = Graphs.disjointUnion(Graphs.complete(4), Graphs.empty(1));
        ColoringResult result = solver.solve(graph,
                BigInteger.valueOf(10_000), "req-clique");

        assertEquals(4, result.chromatic().orElseThrow());
        assertEquals(1L, diag.count(PruningReason.CLIQUE_LOWER_BOUND));
        // Bounds meet without entering the exact tree.
        assertEquals(BigInteger.ZERO, result.nodesUsed());
        assertTrue(diag.events().stream().allMatch(e -> e.requestId().equals("req-clique")));
    }

    @Test
    void noFeasibleColorFiresProvingOddCycleNotBipartite() {
        CollectingDiagnostics diag = new CollectingDiagnostics();
        KDecision.Outcome outcome = KDecision.run(
                Graphs.cycle(5), 2, BigInteger.valueOf(100_000), "req-c5-k2", event -> diag.accept(event));

        assertEquals(KDecision.Verdict.INFEASIBLE, outcome.verdict());
        assertTrue(diag.count(PruningReason.NO_FEASIBLE_COLOR) >= 1L,
                "some vertex must be fully saturated to reject 2-colorability");
        assertTrue(diag.events().stream()
                .anyMatch(e -> e.detail().get("uncolored") > 0L));
    }

    @Test
    void symmetrySkipFiresButNeverDeletesDistinctPartitions() {
        // Empty graph: at the root colors 1 and 2 are both renames of color 0.
        CollectingDiagnostics emptyDiag = new CollectingDiagnostics();
        assertEquals(KDecision.Verdict.FEASIBLE,
                KDecision.run(Graphs.empty(4), 3, BigInteger.valueOf(100_000),
                        "req-sym0", emptyDiag::accept).verdict());
        // At the very first decision node colors 1 and 2 are both renames of
        // color 0; later nodes prune additional rename branches.
        long rootSkips = emptyDiag.atNode(1).stream()
                .filter(e -> e.reason() == PruningReason.SYMMETRY_CANONICAL_SKIP)
                .count();
        assertEquals(2L, rootSkips);
        assertTrue(emptyDiag.count(PruningReason.SYMMETRY_CANONICAL_SKIP) >= 2L);

        // Path 0-1-2 with k=3: DSATUR colors the middle vertex first. At node 1
        // colors 1 and 2 are renames of color 0; at node 2 color 2 is a rename
        // of the already-introduced color 1 partition. Each pruned branch keeps
        // the partition set exactly the same; no distinct partition is removed.
        CollectingDiagnostics pathDiag = new CollectingDiagnostics();
        KDecision.Outcome pathOutcome = KDecision.run(
                Graphs.path(3), 3, BigInteger.valueOf(100_000),
                "req-sym1", pathDiag::accept);
        assertEquals(KDecision.Verdict.FEASIBLE, pathOutcome.verdict());
        assertEquals(2L, pathDiag.atNode(1).stream()
                .filter(e -> e.reason() == PruningReason.SYMMETRY_CANONICAL_SKIP)
                .count());
        assertEquals(1L, pathDiag.atNode(2).stream()
                .filter(e -> e.reason() == PruningReason.SYMMETRY_CANONICAL_SKIP)
                .count());
        assertEquals(3L, pathDiag.count(PruningReason.SYMMETRY_CANONICAL_SKIP));
    }

    @Test
    void provedInfeasibleIsRecordedForCompleteGraphAtKTooSmall() {
        CollectingDiagnostics diag = new CollectingDiagnostics();
        KDecision.Outcome outcome = KDecision.run(
                Graphs.complete(4), 3, BigInteger.valueOf(100_000), "req-k4-k3", event -> diag.accept(event));
        assertEquals(KDecision.Verdict.INFEASIBLE, outcome.verdict());
        assertTrue(diag.count(PruningReason.NO_FEASIBLE_COLOR) >= 1L);
    }

    @Test
    void disconnectedGraphIsStillSearchedCorrectly() {
        Graph union = TestFixtures.load("k3-plus-k2.graph");
        CollectingDiagnostics diag = new CollectingDiagnostics();
        ColoringSolver solver = new ColoringSolver(diag);
        ColoringResult result = solver.solve(union, BigInteger.valueOf(100_000));
        assertEquals(3, result.chromatic().orElseThrow());
        // Greedy DSATUR is already optimal here (3 colors), so the proven gap is
        // closed without entering the exact tree; the clique event still records
        // why every k < 3 is rejected.
        assertEquals(BigInteger.ZERO, result.nodesUsed());
        assertEquals(1L, diag.count(PruningReason.CLIQUE_LOWER_BOUND));
    }
}
