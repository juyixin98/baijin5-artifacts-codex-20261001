package com.local.mwis.service;

import com.local.mwis.graph.Edge;
import com.local.mwis.graph.FailureCategory;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MwisServiceTest {

    private final MwisService service = new MwisService();

    private static BigInteger[] w(long... vals) {
        BigInteger[] out = new BigInteger[vals.length];
        for (int i = 0; i < vals.length; i++) {
            out[i] = BigInteger.valueOf(vals[i]);
        }
        return out;
    }

    private static SolveRequest pathRequest(String id, Long budget, boolean crossCheck) {
        return SolveRequest.of(id, 4,
                List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(2, 3)),
                w(3, 2, 5, 1),
                List.of(List.of(0, 1), List.of(1, 2), List.of(2, 3)),
                List.of(Edge.of(0, 1), Edge.of(1, 2)), 0,
                budget, crossCheck);
    }

    @Test
    void successfulSolveIsFullyAttributed() {
        SolveReport r = service.solve(pathRequest("req-001", null, true));
        assertEquals(ServiceStatus.OK_PROVEN, r.status());
        assertEquals(BigInteger.valueOf(8), r.weight());
        assertEquals(List.of(0, 2), r.independentSet());
        assertEquals(BigInteger.valueOf(8), r.crossCheckWeight());
        assertTrue(r.certificate().accepted());
        assertEquals(ServiceVersion.VALUE, r.serviceVersion());
        // every log line carries the request identity and a stage
        assertTrue(r.log().size() >= 4);
        for (LogEntry e : r.log()) {
            assertEquals("req-001", e.requestId());
            assertTrue(e.sequence() >= 1);
        }
        assertTrue(r.log().stream().anyMatch(e -> e.stage().equals("SOLVE")
                && e.message().contains("tableEntries=")));
        assertNull(r.failure());
    }

    @Test
    void invalidDecompositionRejectedWithCategoryAndViolations() {
        // triangle with a decomposition that leaves edge (0,2) uncovered
        SolveRequest bad = SolveRequest.of("req-bad-1", 3,
                List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(0, 2)),
                w(1, 2, 3),
                List.of(List.of(0, 1), List.of(1, 2)),
                List.of(Edge.of(0, 1)), 0,
                null, false);
        SolveReport r = service.solve(bad);
        assertEquals(ServiceStatus.REJECTED, r.status());
        assertNotNull(r.failure());
        assertEquals(FailureCategory.EDGE_NOT_COVERED, r.failure().category());
        assertEquals("VALIDATE", r.failure().stage());
        assertTrue(r.failure().violations().stream().anyMatch(v -> v.contains("(0,2)")));
        assertNull(r.weight());
        assertTrue(r.log().stream().allMatch(e -> e.requestId().equals("req-bad-1")));
    }

    @Test
    void disconnectedOccurrencesRejected() {
        SolveRequest bad = SolveRequest.of("req-bad-2", 4,
                List.of(Edge.of(0, 2), Edge.of(2, 3)),
                w(1, 1, 1, 1),
                List.of(List.of(0, 2), List.of(1), List.of(2, 3)),
                List.of(Edge.of(0, 1), Edge.of(1, 2)), 0,
                null, false);
        SolveReport r = service.solve(bad);
        assertEquals(ServiceStatus.REJECTED, r.status());
        assertEquals(FailureCategory.VERTEX_OCCURRENCES_DISCONNECTED, r.failure().category());
    }

    @Test
    void weightLengthMismatchRejected() {
        SolveRequest bad = SolveRequest.of("req-bad-3", 4,
                List.of(Edge.of(0, 1)),
                w(1, 2), // too short
                List.of(List.of(0, 1)),
                List.of(), 0,
                null, false);
        SolveReport r = service.solve(bad);
        assertEquals(ServiceStatus.REJECTED, r.status());
        assertEquals(FailureCategory.WEIGHT_LENGTH_MISMATCH, r.failure().category());
    }

    @Test
    void tightBudgetProducesBudgetStatus() {
        SolveReport r = service.solve(pathRequest("req-budget", 2L, false));
        assertEquals(ServiceStatus.BUDGET_EXCEEDED, r.status());
        assertEquals("SOLVE", r.failure().stage());
        assertNull(r.weight());
    }

    @Test
    void crossCheckSkippedOnLargeGraphIsListedAsUncertain() {
        // 30 isolated vertices, one bag per vertex chained: valid, but too big to brute force
        int n = 30;
        List<Edge> noEdges = List.of();
        List<List<Integer>> bags = new java.util.ArrayList<>();
        List<Edge> treeEdges = new java.util.ArrayList<>();
        for (int i = 0; i < n; i++) {
            bags.add(List.of(i));
            if (i > 0) {
                treeEdges.add(Edge.of(i - 1, i));
            }
        }
        BigInteger[] weights = w(new long[n]);
        java.util.Arrays.fill(weights, BigInteger.ONE);
        SolveRequest req = SolveRequest.of("req-big", n, noEdges, weights, bags, treeEdges, 0,
                null, true);
        SolveReport r = service.solve(req);
        assertEquals(ServiceStatus.CROSSCHECK_SKIPPED, r.status());
        assertEquals(BigInteger.valueOf(n), r.weight());
        assertTrue(r.uncertainNotes().stream().anyMatch(s -> s.contains("cross-check skipped")));
    }
}
