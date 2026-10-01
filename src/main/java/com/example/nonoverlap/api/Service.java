package com.example.nonoverlap.api;

import com.example.nonoverlap.io.GridText;
import com.example.nonoverlap.kernel.Budget;
import com.example.nonoverlap.kernel.KernelOutcome;
import com.example.nonoverlap.kernel.SearchState;
import com.example.nonoverlap.kernel.Solver;
import com.example.nonoverlap.model.Instance;
import com.example.nonoverlap.model.Placement;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/**
 * Single service entry point. It owns the mapping from raw kernel outcomes and
 * Java exceptions to the four documented, distinguishable failure families.
 */
public final class Service {

    /**
     * Solve with a freshly created, file-backed run log.
     *
     * @param maxSolutions 1 for one witness, otherwise cap the enumeration
     * @param maxNodes node budget; must be &ge; 1
     * @param maxPairChecks check budget; non-positive means unlimited
     */
    public SolveResult solve(Instance instance, Map<String, ? extends Iterable<Placement>> preassign,
                             int maxSolutions, int maxNodes, long maxPairChecks, Path logDir) {
        String runId = RunLog.newRunId();
        try (RunLog log = new RunLog(logDir, instance == null ? "unknown" : instance.name())) {
            return solveWith(instance, preassign, maxSolutions, maxNodes, maxPairChecks,
                    log, log.runId());
        } catch (LogUnavailableException e) {
            return SolveResult.failure(e.runId(), Status.COMPUTATION_FAILED, FailureKind.COMPUTATION,
                    "run log unavailable: " + e.getMessage(), SearchStats.empty());
        }
    }

    /** Solve with an explicit trace (e.g. an in-memory trace in tests). */
    public SolveResult solveWith(Instance instance,
                                 Map<String, ? extends Iterable<Placement>> preassign,
                                 int maxSolutions, int maxNodes, long maxPairChecks,
                                 Trace trace, String runId) {
        Trace log = trace == null ? Trace.noop() : trace;
        String id = runId == null ? RunLog.newRunId() : runId;
        try {
            validateArguments(instance, maxSolutions, maxNodes);
            log.emit("instance", "loaded", Map.of("summary", GridText.summary(instance, null)));
            SearchState state = new SearchState(instance, preassign);
            Budget budget = new Budget(maxNodes, maxPairChecks <= 0 ? Long.MAX_VALUE : maxPairChecks);
            Solver solver = new Solver(budget, maxSolutions, log);

            KernelOutcome outcome = solver.solve(state);
            return map(id, outcome, instance, log);
        } catch (IllegalArgumentException e) {
            log.emit("error", "invalid-input", Map.of("message", String.valueOf(e.getMessage())));
            return SolveResult.failure(id, Status.INVALID_INPUT, FailureKind.INPUT,
                    e.getMessage(), SearchStats.empty());
        } catch (RuntimeException e) {
            log.emit("error", "computation-failed",
                    Map.of("type", e.getClass().getSimpleName(),
                            "message", String.valueOf(e.getMessage())));
            return SolveResult.failure(id, Status.COMPUTATION_FAILED, FailureKind.COMPUTATION,
                    "internal failure: " + e.getClass().getSimpleName() + ": " + e.getMessage(),
                    SearchStats.empty());
        }
    }

    private SolveResult map(String runId, KernelOutcome outcome, Instance instance, Trace log) {
        switch (outcome) {
            case KernelOutcome.Found found -> {
                Solution witness = found.solution();
                log.emit("verify", "witness", Map.of(
                        "grid", GridText.render(instance, witness)));
                return new SolveResult(runId, Status.SAT, null, null,
                        witness, found.solutions(), found.stats());
            }
            case KernelOutcome.Infeasible inf -> {
                if (inf.atRoot()) {
                    log.emit("result", "state-conflict", Map.of("reason", inf.reason()));
                    return SolveResult.failure(runId, Status.STATE_CONFLICT,
                            FailureKind.STATE, inf.reason(), inf.stats());
                }
                log.emit("result", "unsat", Map.of("reason", inf.reason(),
                        "nodes", inf.stats().nodes()));
                return new SolveResult(runId, Status.UNSAT, null, inf.reason(),
                        null, List.of(), inf.stats());
            }
            case KernelOutcome.Limited lim -> {
                log.emit("result", "resource-exhausted", Map.of("reason", lim.reason()));
                return SolveResult.failure(runId, Status.RESOURCE_EXHAUSTED, FailureKind.RESOURCE,
                        lim.reason(), lim.stats());
            }
        }
    }

    private static void validateArguments(Instance instance, int maxSolutions, int maxNodes) {
        if (instance == null) {
            throw new IllegalArgumentException("instance is null");
        }
        if (maxSolutions < 1) {
            throw new IllegalArgumentException("maxSolutions must be >= 1, got " + maxSolutions);
        }
        if (maxNodes < 1) {
            throw new IllegalArgumentException("maxNodes must be >= 1, got " + maxNodes);
        }
        if (instance.size() == 0) {
            throw new IllegalArgumentException("instance has no rectangles");
        }
    }
}
