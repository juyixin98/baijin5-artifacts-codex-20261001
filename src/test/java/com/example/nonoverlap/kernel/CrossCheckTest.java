package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.Status;
import com.example.nonoverlap.oracle.BruteOracle;
import com.example.nonoverlap.support.CaseFactory;
import com.example.nonoverlap.support.GenCase;
import com.example.nonoverlap.support.KernelAdapter;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.TreeSet;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Property-style cross check: the kernel enumeration must equal the fully
 * independent brute-force oracle on curated and seeded synthetic cases.
 * The oracle never imports production kernel code, so answers are evidence,
 * not self-generated expectations.
 */
class CrossCheckTest {

    private final BruteOracle oracle = new BruteOracle();
    private final KernelAdapter adapter = new KernelAdapter();

    @TempDir
    Path logs;

    @Test
    void curatedCasesMatchIndependentOracle() {
        for (GenCase c : CaseFactory.curated()) {
            TreeSet<BruteOracle.Answer> expected = oracle.enumerate(c);
            var result = adapter.enumerate(c, 1000, logs.resolve(c.name()));
            if (expected.isEmpty()) {
                assertTrue(result.status() == Status.UNSAT
                                || result.status() == Status.STATE_CONFLICT,
                        c.name() + " expected unsat-like, got " + result.status());
                continue;
            }
            assertEquals(Status.SAT, result.status(),
                    c.name() + " expected solutions, got " + result.status());
            TreeSet<BruteOracle.Answer> actual = adapter.canonical(result);
            assertEquals(expected, actual, () -> "mismatch on " + c.name()
                    + "\n oracle=" + oracle.canonicalAll(expected)
                    + "\n kernel=" + actual.stream().map(BruteOracle.Answer::canonical).toList());
        }
    }

    @Test
    void seededRandomCasesMatchIndependentOracle() {
        int cases = 60;
        for (GenCase c : CaseFactory.seeded(cases, 42)) {
            TreeSet<BruteOracle.Answer> expected = oracle.enumerate(c);
            var result = adapter.enumerate(c, 1000, logs.resolve(c.name()));
            if (expected.isEmpty()) {
                assertTrue(result.status() == Status.UNSAT
                                || result.status() == Status.STATE_CONFLICT,
                        () -> c.name() + " expected unsat-like, got " + result.status());
            } else {
                assertEquals(Status.SAT, result.status(), c.name());
                assertEquals(expected, adapter.canonical(result),
                        () -> "mismatch on " + c.name());
            }
        }
    }

    @Test
    void everyCuratedAnswerIsPairwiseDisjointWitness() {
        for (GenCase c : CaseFactory.curated()) {
            var result = adapter.enumerateInMemory(c, 1000);
            for (var solution : result.solutions()) {
                WitnessChecker.check(c, solution);
            }
        }
    }
}
