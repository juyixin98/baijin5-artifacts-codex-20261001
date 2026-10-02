package com.example.coloring;

import com.example.coloring.diag.DecisionKind;
import com.example.coloring.diag.DiagnosticReport;
import com.example.coloring.diag.Reason;
import com.example.coloring.diag.RequestContext;
import com.example.coloring.graph.Graph;
import com.example.coloring.graph.Graphs;
import com.example.coloring.ref.SetPartitionReference;
import com.example.coloring.solver.ChromaticResult;
import com.example.coloring.solver.ChromaticSolver;
import com.example.coloring.solver.SearchStatus;
import com.example.coloring.solver.SolverConfig;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class BudgetStopTest {

    private static Graph hardSevenGraph() {
        // chi=3 but DSATUR gives 4; requires real backtracking.
        return Graph.builder(7)
                .edge(0, 1).edge(0, 5).edge(0, 6)
                .edge(1, 4).edge(1, 6)
                .edge(2, 3).edge(2, 4).edge(2, 5)
                .edge(3, 4).edge(3, 5)
                .build();
    }

    @Test
    void nodeBudgetStopsAndReportsOnlyProvenBounds() {
        Graph graph = hardSevenGraph();
        int trueChi = SetPartitionReference.chromaticNumber(graph);
        assertEquals(3, trueChi);

        DiagnosticReport report = new DiagnosticReport();
        ChromaticResult result = new ChromaticSolver(SolverConfig.limited(1, 60_000))
                .solve(graph, RequestContext.of("req-budget", false), report);

        assertFalse(result.status().isExact(), "one node cannot prove optimality");
        assertEquals(SearchStatus.BOUNDS_PROVEN, result.status());
        assertTrue(result.stoppedByNodeBudget());

        // The only claim allowed: chi in [clique lower, feasible upper].
        assertTrue(result.lowerBound() >= 1);
        assertTrue(result.upperBound() >= result.lowerBound());
        assertTrue(result.lowerBound() <= trueChi && trueChi <= result.upperBound(),
                "proven interval must contain the independently computed chi");
        assertTrue(result.coloring().isProper(graph), "upper bound must remain a real coloring");
        assertTrue(result.cliqueWitness().size() == result.lowerBound());

        assertThrows(IllegalStateException.class, result::chromaticNumber,
                "exact chi must not be reported when the gap is open");

        assertTrue(report.count(DecisionKind.UNDETERMINED_BUDGET) >= 1);
        assertTrue(report.count(Reason.NODE_BUDGET_EXHAUSTED) >= 1);
    }

    @Test
    void sameGraphWithFullBudgetIsProvedOptimal() {
        Graph graph = hardSevenGraph();
        ChromaticResult result = new ChromaticSolver(SolverConfig.standard())
                .solve(graph, RequestContext.of("req-full", false), new DiagnosticReport());
        assertEquals(SearchStatus.OPTIMAL, result.status());
        assertEquals(3, result.chromaticNumber());
    }

    @Test
    void timeBudgetIsAlsoAnExplicitUndeterminedReason() {
        Graph graph = Graphs.complete(6);
        DiagnosticReport report = new DiagnosticReport();
        ChromaticResult result = new ChromaticSolver(new SolverConfig(1L, 1L, true))
                .solve(graph, RequestContext.of("req-time", false), report);
        // K6 shortcut may close before search; if not, budget must be explicit.
        if (!result.status().isExact()) {
            assertTrue(report.count(DecisionKind.UNDETERMINED_BUDGET) >= 1);
            assertTrue(result.stoppedByNodeBudget() || result.stoppedByTimeBudget());
        }
    }
}
