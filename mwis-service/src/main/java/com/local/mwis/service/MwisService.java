package com.local.mwis.service;

import com.local.mwis.bounds.CertificateCheck;
import com.local.mwis.bounds.CertificateVerifier;
import com.local.mwis.bounds.OptimalityAssessment;
import com.local.mwis.bounds.UpperBounds;
import com.local.mwis.core.BagTooWideException;
import com.local.mwis.core.BudgetExceededException;
import com.local.mwis.core.MwisSolver;
import com.local.mwis.core.NiceDecomposition;
import com.local.mwis.core.NiceDecompositionBuilder;
import com.local.mwis.core.SolveResult;
import com.local.mwis.core.SolverOptions;
import com.local.mwis.exhaustive.BruteForceMwis;
import com.local.mwis.exhaustive.ExhaustiveResult;
import com.local.mwis.exhaustive.InputTooLargeException;
import com.local.mwis.graph.DecompositionValidator;
import com.local.mwis.graph.FailureCategory;
import com.local.mwis.graph.ValidationFailure;
import com.local.mwis.graph.ValidationResult;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;

/**
 * Orchestrates one solve request through explicit stages, emitting an attributable
 * log line per step: VALIDATE -&gt; PREPARE -&gt; SOLVE -&gt; CERTIFY -&gt; CROSSCHECK.
 * Never throws for request-level problems; every outcome is a {@link SolveReport}.
 */
public final class MwisService {

    private static final String STAGE_VALIDATE = "VALIDATE";
    private static final String STAGE_PREPARE = "PREPARE";
    private static final String STAGE_SOLVE = "SOLVE";
    private static final String STAGE_CERTIFY = "CERTIFY";
    private static final String STAGE_CROSSCHECK = "CROSSCHECK";

    private final DecompositionValidator validator = new DecompositionValidator();
    private final CertificateVerifier verifier = new CertificateVerifier();

    public SolveReport solve(SolveRequest request) {
        String id = request.requestId();
        List<LogEntry> log = new ArrayList<>();
        List<String> uncertain = new ArrayList<>();
        log(log, id, STAGE_VALIDATE, "received request; version=" + ServiceVersion.VALUE
                + " vertices=" + request.graph().vertexCount()
                + " edges=" + request.graph().edgeCount()
                + " bags=" + request.decomposition().nodeCount());

        // ---- VALIDATE ----
        if (request.weights().length != request.graph().vertexCount()) {
            return rejected(request, log, FailureCategory.WEIGHT_LENGTH_MISMATCH,
                    "weights length " + request.weights().length
                            + " != vertexCount " + request.graph().vertexCount(), List.of());
        }
        ValidationResult validation = validator.validate(request.graph(), request.decomposition());
        if (!validation.isValid()) {
            List<String> violations = validation.failures().stream()
                    .map(ValidationFailure::toString).toList();
            return rejected(request, log, validation.primaryCategory(),
                    "decomposition validation failed with " + validation.failures().size()
                            + " problem(s)", violations);
        }
        log(log, id, STAGE_VALIDATE, "decomposition valid: width="
                + request.decomposition().width() + " edge coverage and vertex connectivity verified");

        // ---- PREPARE ----
        NiceDecomposition nice;
        try {
            nice = new NiceDecompositionBuilder().build(request.decomposition());
        } catch (RuntimeException e) {
            return failed(request, log, ServiceStatus.INTERNAL_ERROR, STAGE_PREPARE,
                    "nice decomposition construction failed: " + e.getMessage());
        }
        log(log, id, STAGE_PREPARE, "nice decomposition built: nodes=" + nice.size()
                + " maxBag=" + nice.maxBagSize());

        // ---- SOLVE ----
        SolverOptions options = request.tableEntryBudget() == null
                ? SolverOptions.unlimited()
                : new SolverOptions(request.tableEntryBudget());
        SolveResult result;
        try {
            result = new MwisSolver(request.graph(), request.weights(), nice, options).solve();
        } catch (BudgetExceededException e) {
            log(log, id, STAGE_SOLVE, "budget exceeded: " + e.getMessage());
            return failed(request, log, ServiceStatus.BUDGET_EXCEEDED, STAGE_SOLVE,
                    e.getMessage());
        } catch (BagTooWideException e) {
            log(log, id, STAGE_SOLVE, "bag too wide: " + e.getMessage());
            return failed(request, log, ServiceStatus.BAG_TOO_WIDE, STAGE_SOLVE,
                    e.getMessage());
        }
        log(log, id, STAGE_SOLVE, "solved: weight=" + result.weight()
                + " setSize=" + result.independentSet().size()
                + " tableEntries=" + result.stats().tableEntries()
                + "/" + (options.tableEntryBudget() == SolverOptions.UNLIMITED
                        ? "unlimited" : options.tableEntryBudget())
                + " niceNodes=" + result.stats().nodesProcessed()
                + " (join=" + result.stats().joinNodes()
                + " introduce=" + result.stats().introduceNodes()
                + " forget=" + result.stats().forgetNodes() + ")");

        // ---- CERTIFY ----
        CertificateCheck cert = verifier.verify(
                request.graph(), request.weights(), result.independentSet(), result.weight());
        if (!cert.accepted()) {
            log(log, id, STAGE_CERTIFY, "certificate REJECTED: " + cert);
            return failed(request, log, ServiceStatus.INTERNAL_ERROR, STAGE_CERTIFY,
                    "solver produced a set that failed independent verification");
        }
        log(log, id, STAGE_CERTIFY, "certificate verified: independent set of "
                + result.independentSet().size() + " vertices, weight " + cert.computedWeight());

        BigInteger bound = UpperBounds.best(request.graph(), request.weights());
        OptimalityAssessment optimality = OptimalityAssessment.of(result.weight(), bound);
        ServiceStatus status;
        if (optimality.status() == OptimalityAssessment.Status.PROVEN_OPTIMAL) {
            status = ServiceStatus.OK_PROVEN;
            log(log, id, STAGE_CERTIFY, "optimality proven: clique-cover/positive bound "
                    + bound + " meets solution weight");
        } else {
            status = ServiceStatus.OK_BOUND_INCONCLUSIVE;
            uncertain.add("bound gap " + optimality.gap()
                    + ": solver value " + result.weight()
                    + " < computable upper bound " + bound
                    + "; optimality rests on the exact DP, not on an independent bound");
            log(log, id, STAGE_CERTIFY, "bound inconclusive: gap=" + optimality.gap());
        }

        // ---- CROSSCHECK (optional) ----
        BigInteger crossWeight = null;
        if (request.runExhaustiveCrossCheck()) {
            try {
                ExhaustiveResult exhaustive = new BruteForceMwis().solve(request.graph(), request.weights());
                crossWeight = exhaustive.weight();
                if (!exhaustive.weight().equals(result.weight())) {
                    log(log, id, STAGE_CROSSCHECK, "MISMATCH: dp=" + result.weight()
                            + " exhaustive=" + exhaustive.weight());
                    return new SolveReport(id, ServiceVersion.VALUE, ServiceStatus.CROSSCHECK_MISMATCH,
                            result.weight(), result.independentSet(), result.stats(), cert,
                            optimality, crossWeight,
                            new FailureInfo(null, STAGE_CROSSCHECK,
                                    "DP result disagrees with exhaustive enumeration", List.of()),
                            List.copyOf(uncertain), List.copyOf(log));
                }
                log(log, id, STAGE_CROSSCHECK, "cross-check ok: exhaustive enumeration over "
                        + exhaustive.subsetsTested() + " subsets agrees at " + crossWeight);
            } catch (InputTooLargeException e) {
                status = ServiceStatus.CROSSCHECK_SKIPPED;
                uncertain.add("exhaustive cross-check skipped: " + e.getMessage());
                log(log, id, STAGE_CROSSCHECK, "skipped: " + e.getMessage());
            }
        }

        return new SolveReport(id, ServiceVersion.VALUE, status,
                result.weight(), result.independentSet(), result.stats(), cert,
                optimality, crossWeight, null, List.copyOf(uncertain), List.copyOf(log));
    }

    private SolveReport rejected(SolveRequest request, List<LogEntry> log,
                                 FailureCategory category, String message, List<String> violations) {
        log(log, request.requestId(), STAGE_VALIDATE, "rejected: " + message);
        return new SolveReport(request.requestId(), ServiceVersion.VALUE, ServiceStatus.REJECTED,
                null, null, null, null, null, null,
                new FailureInfo(category, STAGE_VALIDATE, message, violations),
                List.of(), List.copyOf(log));
    }

    private SolveReport failed(SolveRequest request, List<LogEntry> log,
                               ServiceStatus status, String stage, String message) {
        return new SolveReport(request.requestId(), ServiceVersion.VALUE, status,
                null, null, null, null, null, null,
                new FailureInfo(null, stage, message, List.of()),
                List.of(), List.copyOf(log));
    }

    private void log(List<LogEntry> log, String requestId, String stage, String message) {
        log.add(new LogEntry(requestId, stage, log.size() + 1, message));
    }
}
