package com.local.mwis.service;

import com.local.mwis.bounds.CertificateCheck;
import com.local.mwis.bounds.OptimalityAssessment;
import com.local.mwis.core.SolverStats;

import java.math.BigInteger;
import java.util.List;

/**
 * Full auditable outcome of one request.
 *
 * @param requestId        caller-supplied identity
 * @param serviceVersion   build version that produced this report
 * @param status           top-level outcome
 * @param weight           optimum weight (null when no result)
 * @param independentSet   witnessing set (null when no result)
 * @param stats            solver counters (null when solving never ran)
 * @param certificate      independent verification of the returned set
 * @param optimality       bound-based optimality assessment
 * @param crossCheckWeight exhaustive reference weight, when the cross-check ran
 * @param failure          failure details (null on success)
 * @param uncertainNotes   conclusions the service could not settle, listed separately
 * @param log              ordered processing log
 */
public record SolveReport(
        String requestId,
        String serviceVersion,
        ServiceStatus status,
        BigInteger weight,
        List<Integer> independentSet,
        SolverStats stats,
        CertificateCheck certificate,
        OptimalityAssessment optimality,
        BigInteger crossCheckWeight,
        FailureInfo failure,
        List<String> uncertainNotes,
        List<LogEntry> log) {
}
