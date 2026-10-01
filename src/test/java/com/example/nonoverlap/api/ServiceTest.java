package com.example.nonoverlap.api;

import com.example.nonoverlap.model.Existence;
import com.example.nonoverlap.model.Instance;
import com.example.nonoverlap.model.Placement;
import com.example.nonoverlap.model.RectDef;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ServiceTest {

    private Instance clash() {
        return new Instance("clash", 1, 1, List.of(
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE)));
    }

    private Instance optionalClash() {
        return new Instance("opt", 1, 1, List.of(
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.UNKNOWN)));
    }

    @Test
    void declarationLevelImpossibilityIsUnsatNotStateConflict() {
        SolveResult r = new Service().solveWith(clash(), Map.of(), 1, 1000, 0,
                Trace.noop(), "t-unsat");
        assertEquals(Status.UNSAT, r.status());
        assertEquals(null, r.failureKind());
    }

    @Test
    void externallyForcedContradictionIsStateConflict() {
        Instance in = new Instance("forced", 2, 2, List.of(
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE)));
        // both externally forced onto the same cell
        Map<String, List<Placement>> pre = Map.of(
                "A", List.of(new Placement(1, 1)),
                "B", List.of(new Placement(1, 1)));
        SolveResult r = new Service().solveWith(in, pre, 1, 1000, 0,
                Trace.noop(), "t-state");
        assertEquals(Status.STATE_CONFLICT, r.status());
        assertEquals(FailureKind.STATE, r.failureKind());
        assertNotNull(r.reason());
    }

    @Test
    void invalidInputIsDistinct() {
        SolveResult nullInstance = new Service().solveWith(null, Map.of(), 1, 1000, 0,
                Trace.noop(), "t-null");
        assertEquals(Status.INVALID_INPUT, nullInstance.status());
        assertEquals(FailureKind.INPUT, nullInstance.failureKind());

        SolveResult badBudget = new Service().solveWith(optionalClash(), Map.of(), 1, 0, 0,
                Trace.noop(), "t-budget");
        assertEquals(Status.INVALID_INPUT, badBudget.status());
        assertEquals(FailureKind.INPUT, badBudget.failureKind());

        Instance in = new Instance("oob", 1, 1,
                List.of(new RectDef("A", 1, 1, Existence.TRUE)));
        SolveResult outOfGrid = new Service().solveWith(in,
                Map.of("A", List.of(new Placement(2, 0))), 1, 1000, 0,
                Trace.noop(), "t-oob");
        assertEquals(Status.INVALID_INPUT, outOfGrid.status());
    }

    @Test
    void resourceExhaustedIsDistinct() {
        SolveResult r = new Service().solveWith(optionalClash(), Map.of(), 1, 1, 0,
                Trace.noop(), "t-resource");
        assertEquals(Status.RESOURCE_EXHAUSTED, r.status());
        assertEquals(FailureKind.RESOURCE, r.failureKind());
        assertTrue(r.reason().contains("exhausted"));
    }

    @Test
    void unwritableLogDirIsComputationFailed(@TempDir Path tmp) throws Exception {
        Path regularFile = tmp.resolve("not-a-dir");
        Files.writeString(regularFile, "x");
        SolveResult r = new Service().solve(optionalClash(), Map.of(), 1, 1000, 0, regularFile);
        assertEquals(Status.COMPUTATION_FAILED, r.status());
        assertEquals(FailureKind.COMPUTATION, r.failureKind());
        assertTrue(r.reason().toLowerCase().contains("log"));
    }

    @Test
    void successfulRunWritesReplayableLogWithRunId(@TempDir Path tmp) throws Exception {
        Path logDir = tmp.resolve("logs");
        SolveResult r = new Service().solve(optionalClash(), Map.of(), 1, 1000, 0, logDir);
        assertEquals(Status.SAT, r.status());
        assertNotNull(r.runId());
        Path written;
        try (var files = Files.list(logDir)) {
            written = files.findFirst().orElseThrow();
        }
        String body = Files.readString(written);
        assertTrue(body.contains("runId=" + r.runId()) || body.contains(r.runId()));
        assertTrue(body.contains("instance"));
        assertTrue(body.contains("branch"), "log must contain branching intermediate state:\n" + body);
        assertTrue(body.contains("result") || body.contains("verify"));
        assertFalse(body.isBlank());
    }
}
