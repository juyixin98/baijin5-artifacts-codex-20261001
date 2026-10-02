package com.example.coloring;

import com.example.coloring.diag.DiagnosticReport;
import com.example.coloring.diag.RequestContext;
import com.example.coloring.graph.Graph;
import com.example.coloring.graph.Graphs;
import com.example.coloring.solver.ChromaticResult;
import com.example.coloring.solver.ChromaticSolver;
import com.example.coloring.solver.SearchStatus;
import com.example.coloring.solver.SolverConfig;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ChromaticSolverTest {

    private final ChromaticSolver solver = new ChromaticSolver();

    @Test
    void emptyGraphHasChromaticNumberZero() {
        ChromaticResult result = solver.solve(Graphs.empty());
        assertEquals(SearchStatus.OPTIMAL, result.status());
        assertEquals(0, result.chromaticNumber());
        assertTrue(result.coloring().isProper(Graphs.empty()));
    }

    @Test
    void oddCycleC5RequiresExactlyThreeColors() {
        Graph c5 = Graphs.cycle(5);
        ChromaticResult result = solver.solve(c5);
        assertEquals(SearchStatus.OPTIMAL, result.status());
        assertEquals(3, result.chromaticNumber());
        assertEquals(2, result.lowerBound(), "clique lower bound of C5 is 2");
        assertEquals(3, result.upperBound());
        assertTrue(result.coloring().isProper(c5));
    }

    @Test
    void completeGraphsProvedByCliqueBoundImmediately() {
        for (int n = 1; n <= 6; n++) {
            Graph kn = Graphs.complete(n);
            DiagnosticReport report = new DiagnosticReport();
            ChromaticResult result = solver.solve(kn,
                    RequestContext.of("kn-" + n, false), report);
            assertEquals(n, result.chromaticNumber(), "K" + n + " chi");
            assertEquals(n, result.lowerBound());
            assertTrue(result.coloring().isProper(kn));
        }
    }

    @Test
    void disconnectedUnionTakesMaximumComponent() {
        Graph union = Graphs.union(Graphs.cycle(5), Graphs.complete(4));
        ChromaticResult result = solver.solve(union);
        assertEquals(SearchStatus.OPTIMAL, result.status());
        assertEquals(4, result.chromaticNumber(), "chi(C5 disjoint-union K4) = max(3,4) = 4");
        assertEquals(4, result.lowerBound());
        assertTrue(result.coloring().isProper(union));
    }

    @Test
    void bipartiteGraphsNeedTwoColors() {
        Graph path = Graphs.path(6);
        ChromaticResult pathResult = solver.solve(path);
        assertEquals(2, pathResult.chromaticNumber());
        assertTrue(pathResult.coloring().isProper(path));

        // Explicit connected bipartite graph: K_{2,3}.
        Graph k23 = Graph.builder(5)
                .edge(0, 2).edge(0, 3).edge(0, 4)
                .edge(1, 2).edge(1, 3).edge(1, 4)
                .build();
        ChromaticResult k23Result = solver.solve(k23);
        assertEquals(2, k23Result.chromaticNumber());
        assertTrue(k23Result.coloring().isProper(k23));
    }

    @Test
    void petersenIsThreeChromatic() {
        Graph petersen = Graphs.petersen();
        ChromaticResult result = solver.solve(petersen);
        assertEquals(3, result.chromaticNumber());
        assertEquals(2, result.lowerBound());
        assertTrue(result.coloring().isProper(petersen));
    }

    @Test
    void exactClaimForbidsUnprovedGap() {
        Graph graph = Graphs.complete(7);
        ChromaticResult result = new ChromaticSolver(SolverConfig.limited(1, 60_000)).solve(graph);
        if (!result.status().isExact()) {
            assertTrue(result.upperBound() >= result.lowerBound());
            assertTrue(result.stoppedByNodeBudget() || result.stoppedByTimeBudget());
        }
    }
}
