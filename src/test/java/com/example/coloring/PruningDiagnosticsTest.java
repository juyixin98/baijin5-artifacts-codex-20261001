package com.example.coloring;

import com.example.coloring.diag.DecisionKind;
import com.example.coloring.diag.DiagnosticReport;
import com.example.coloring.diag.Reason;
import com.example.coloring.diag.RequestContext;
import com.example.coloring.diag.SearchEvent;
import com.example.coloring.graph.Graph;
import com.example.coloring.graph.Graphs;
import com.example.coloring.solver.ChromaticResult;
import com.example.coloring.solver.ChromaticSolver;
import com.example.coloring.solver.SearchStatus;
import com.example.coloring.solver.SolverConfig;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PruningDiagnosticsTest {

    @Test
    void completeGraphShortcutEmitsBoundMatch() {
        DiagnosticReport report = new DiagnosticReport();
        ChromaticResult result = new ChromaticSolver()
                .solve(Graphs.complete(4), RequestContext.of("req-k4", false), report);

        assertEquals(SearchStatus.OPTIMAL, result.status());
        assertTrue(report.count(DecisionKind.ACCEPT_BOUND_MATCH) >= 1,
                "K4 is closed by clique==upper-bound shortcut");
        SearchEvent match = report.events().stream()
                .filter(e -> e.kind() == DecisionKind.ACCEPT_BOUND_MATCH)
                .findFirst().orElseThrow();
        assertEquals(Reason.LOWER_BOUND_MATCHES_UPPER, match.reason());
        assertEquals("req-k4", match.requestId());
        assertEquals(4, match.cliqueWitness().size());
    }

    @Test
    void c5SearchRecordsEachPruningReason() {
        DiagnosticReport report = new DiagnosticReport();
        // Disable greedy warm start so the search itself finds a feasible coloring.
        ChromaticResult result = new ChromaticSolver(new SolverConfig(5_000_000L, 30_000L, false))
                .solve(Graphs.cycle(5), RequestContext.of("req-c5", false), report);

        assertEquals(SearchStatus.OPTIMAL, result.status());
        assertEquals(3, result.chromaticNumber());
        assertTrue(report.count(Reason.SYMMETRY_NEW_COLOR_UNUSED) > 0,
                "non-canonical fresh labels must be pruned by renaming symmetry");
        assertTrue(report.count(Reason.ADJACENT_SAME_COLOR) > 0,
                "C5 forces neighbor-color conflicts");
        assertTrue(report.count(Reason.LOWER_BOUND_NOT_IMPROVABLE) > 0,
                "branches that cannot beat the best coloring are pruned");
        assertTrue(report.count(DecisionKind.ACCEPT_FEASIBLE) > 0,
                "a feasible coloring is accepted as the upper bound");
        assertTrue(report.count(DecisionKind.ACCEPT_BRANCH) > 0);
    }

    @Test
    void symmetryPruningKeepsDistinctPartitions() {
        // 7-vertex graph on which DSATUR (and the trivial bound) is loose:
        // chi=3 although the heuristic colors it with 4. The full search must
        // reach equivalent unused fresh labels, prune them by color-renaming
        // symmetry, and still find every distinct 3-color partition.
        Graph edge = Graph.builder(7)
                .edge(0, 1).edge(0, 5).edge(0, 6)
                .edge(1, 4).edge(1, 6)
                .edge(2, 3).edge(2, 4).edge(2, 5)
                .edge(3, 4).edge(3, 5)
                .build();
        DiagnosticReport report = new DiagnosticReport();
        ChromaticResult result = new ChromaticSolver(new SolverConfig(5_000_000L, 30_000L, false))
                .solve(edge, RequestContext.of("req-edge", false), report);
        assertEquals(3, result.chromaticNumber());
        assertTrue(result.coloring().isProper(edge));
        assertTrue(report.count(Reason.SYMMETRY_NEW_COLOR_UNUSED) > 0);
    }

    @Test
    void everyEventCarriesRequestIdStateAndReason() {
        DiagnosticReport report = new DiagnosticReport();
        new ChromaticSolver().solve(Graphs.cycle(4), RequestContext.of("req-state", false), report);
        for (SearchEvent event : report.events()) {
            assertEquals("req-state", event.requestId());
            assertTrue(event.sequence() > 0);
            assertNotNull(event.reason());
            assertNotNull(event.kind());
            assertTrue(event.bestUpperBound() >= 0);
            assertTrue(event.nodesVisited() >= 0);
        }
    }
}
