package com.example.nonoverlap.api;

import java.util.List;

/**
 * Immutable result envelope of the service entry point. Exactly one of
 * {@code solution} or {@code solutions} carries data depending on the request
 * (one solution vs. bounded enumeration); failures carry a structured
 * {@code failureKind} plus a human-readable reason.
 */
public record SolveResult(
        String runId,
        Status status,
        FailureKind failureKind,
        String reason,
        Solution solution,
        List<Solution> solutions,
        SearchStats stats) {

    public boolean ok() {
        return status == Status.SAT || status == Status.UNSAT;
    }

    public static SolveResult failure(String runId, Status status, FailureKind kind,
                                      String reason, SearchStats stats) {
        return new SolveResult(runId, status, kind, reason, null, List.of(),
                stats == null ? SearchStats.empty() : stats);
    }
}
