package com.local.mwis.cli;

import com.local.mwis.graph.FailureCategory;
import com.local.mwis.service.MwisService;
import com.local.mwis.service.ServiceStatus;
import com.local.mwis.service.SolveReport;
import com.local.mwis.service.SolveRequest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** End-to-end: generate fixtures, parse them back, solve, assert concrete outcomes. */
class CliAndFixtureTest {

    @TempDir
    Path dir;

    private SolveReport solveFixture(String name) throws Exception {
        SolveRequest req = new JsonRequestParser().parse(dir.resolve(name));
        return new MwisService().solve(req);
    }

    @Test
    void generatedFixturesBehaveAsDocumented() throws Exception {
        FixtureGenerator.generateAll(dir);
        List<String> files;
        try (var s = Files.list(dir)) {
            files = s.map(p -> p.getFileName().toString()).sorted().toList();
        }
        assertEquals(7, files.size());

        SolveReport path = solveFixture("request-path.json");
        assertEquals(ServiceStatus.OK_PROVEN, path.status());
        assertEquals(BigInteger.valueOf(8), path.weight());
        assertEquals(List.of(0, 2), path.independentSet());
        assertEquals(BigInteger.valueOf(8), path.crossCheckWeight());

        SolveReport join = solveFixture("request-join.json");
        assertEquals(BigInteger.valueOf(3), join.weight());
        assertEquals(List.of(0, 2, 4), join.independentSet());
        assertTrue(join.stats().joinNodes() > 0);

        SolveReport neg = solveFixture("request-negative.json");
        assertEquals(BigInteger.ZERO, neg.weight());
        assertTrue(neg.independentSet().isEmpty());

        SolveReport rnd = solveFixture("request-random-2tree.json");
        assertTrue(rnd.status() == ServiceStatus.OK_PROVEN
                || rnd.status() == ServiceStatus.OK_BOUND_INCONCLUSIVE);
        assertEquals(rnd.weight(), rnd.crossCheckWeight());
        assertTrue(rnd.certificate().accepted());

        SolveReport uncovered = solveFixture("request-broken-uncovered-edge.json");
        assertEquals(ServiceStatus.REJECTED, uncovered.status());
        assertEquals(FailureCategory.EDGE_NOT_COVERED, uncovered.failure().category());

        SolveReport disconnected = solveFixture("request-broken-disconnected.json");
        assertEquals(ServiceStatus.REJECTED, disconnected.status());
        assertEquals(FailureCategory.VERTEX_OCCURRENCES_DISCONNECTED,
                disconnected.failure().category());

        SolveReport budget = solveFixture("request-tight-budget.json");
        assertEquals(ServiceStatus.BUDGET_EXCEEDED, budget.status());
    }

    @Test
    void requestIdentityFlowsIntoEveryLogLine() throws Exception {
        FixtureGenerator.generateAll(dir);
        SolveReport r = solveFixture("request-path.json");
        assertEquals("demo-path-4", r.requestId());
        assertTrue(r.log().stream().allMatch(e -> e.requestId().equals("demo-path-4")));
    }
}
