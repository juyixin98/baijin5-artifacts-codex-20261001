package clique.run;

import clique.TestGraphs;
import clique.config.CliqueConfig;
import clique.graph.Graph;
import clique.search.BronKerbosch;
import clique.search.CancellationToken;
import clique.search.ResourceExhaustedException;
import clique.search.SearchResult;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RunLogTest {

    @Test
    void completedRunLogsStartAndCompleteWithSameRunId() {
        RunLog log = RunLog.create();
        Graph g = TestGraphs.random(10, 0.5, 3);
        SearchResult r = new BronKerbosch(g, CliqueConfig.defaults(), log)
                .enumerate(CancellationToken.none());
        assertTrue(r.completed());
        assertEquals(log.runId(), r.runId());

        List<RunLog.Entry> entries = log.entries();
        assertFalse(entries.isEmpty());
        for (RunLog.Entry e : entries) {
            assertEquals(log.runId(), e.runId(), "every entry carries the run id");
        }
        RunLog.Entry start = log.byEvent("RUN_START").get(0);
        assertTrue(start.detail().contains("n=" + g.n()), "start logs graph size");
        assertTrue(start.detail().contains("degeneracy="), "start logs degeneracy");
        assertTrue(start.reason().contains("maxSteps"), "start logs config reason");
        RunLog.Entry done = log.byEvent("RUN_COMPLETE").get(0);
        assertTrue(done.detail().contains("cliques=" + r.cliques().size()));
    }

    @Test
    void verboseRunLogsPerIndexCommits() {
        RunLog log = RunLog.create();
        Graph g = TestGraphs.path(5);
        CliqueConfig cfg = new CliqueConfig(100_000_000L, CliqueConfig.PivotStrategy.MAX_INTERSECTION,
                CliqueConfig.Verbosity.VERBOSE, 24);
        new BronKerbosch(g, cfg, log).enumerate(CancellationToken.none());
        assertEquals(g.n(), log.byEvent("BUFFER_COMMIT").size(),
                "one commit record per outer index");
    }

    @Test
    void cancelRunLogsDetectionWithReason() {
        RunLog log = RunLog.create();
        Graph g = TestGraphs.random(16, 0.5, 42);
        SearchResult r = new BronKerbosch(g, CliqueConfig.defaults(), log)
                .enumerate(CancellationToken.afterCommits(2));
        assertFalse(r.completed());
        List<RunLog.Entry> cancels = log.byEvent("CANCEL_DETECTED");
        assertEquals(1, cancels.size());
        RunLog.Entry c = cancels.get(0);
        assertTrue(c.reason().contains("cancel"), "cancel reason recorded");
        assertTrue(c.detail().contains("outerIndex="), "cancel position recorded");
    }

    @Test
    void budgetExhaustionLoggedBeforeThrowing() {
        RunLog log = RunLog.create();
        Graph g = TestGraphs.random(16, 0.5, 42);
        CliqueConfig tight = new CliqueConfig(10, CliqueConfig.PivotStrategy.MAX_INTERSECTION,
                CliqueConfig.Verbosity.SUMMARY, 24);
        BronKerbosch bk = new BronKerbosch(g, tight, log);
        assertThrows(ResourceExhaustedException.class, () -> bk.enumerate(CancellationToken.none()));
        List<RunLog.Entry> budget = log.byEvent("BUDGET_EXHAUSTED");
        assertEquals(1, budget.size());
        assertTrue(budget.get(0).detail().contains("limit=10"));
    }

    @Test
    void resumeRunLogsStartIndex() {
        Graph g = TestGraphs.random(16, 0.5, 42);
        SearchResult part = new BronKerbosch(g, CliqueConfig.defaults(), RunLog.create())
                .enumerate(CancellationToken.afterCommits(2));
        RunLog log2 = RunLog.create();
        new BronKerbosch(g, CliqueConfig.defaults(), log2)
                .resume(part.resumeState(), CancellationToken.none());
        RunLog.Entry start = log2.byEvent("RUN_START").get(0);
        assertTrue(start.detail().contains("startIndex=" + part.resumeState().nextOuterIndex()),
                "resume run records where it continues");
    }
}
