package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.Solution;
import com.example.nonoverlap.io.TextInstanceParser;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Runs the shipped example fixtures (parser + service) and asserts concrete
 * witnesses/counts, including the four-direction exclusion fixture and the
 * backtracking fixtures.
 */
class ExampleFixtureTest {

    private com.example.nonoverlap.api.SolveResult run(String fixture, int cap) throws Exception {
        Path p = Path.of("examples", fixture);
        assertTrue(Files.exists(p), p + " must exist");
        TextInstanceParser.Parsed parsed = new TextInstanceParser().parse(p);
        return new com.example.nonoverlap.api.Service().solve(parsed.instance,
                parsed.preassign, cap, 1_000_000, 0, Path.of("target/example-logs", fixture));
    }

    @Test
    void touchHasExactlyTwoOrderings() throws Exception {
        var r = run("touch.txt", 50);
        assertEquals(com.example.nonoverlap.api.Status.SAT, r.status());
        assertEquals(2, r.solutions().size());
    }

    @Test
    void fourDirectionsBIsOnlySolvableWhenAbsent() throws Exception {
        var r = run("four-directions.txt", 500);
        assertEquals(com.example.nonoverlap.api.Status.SAT, r.status());
        // Every B-present anchor overlaps one of the four fixed blockers
        // (left/right/below/above); only the existence branch B=FALSE survives.
        assertEquals(1, r.solutions().size());
        for (Solution s : r.solutions()) {
            assertEquals(com.example.nonoverlap.model.Existence.FALSE,
                    s.get("B").resolvedExistence());
        }
        assertTrue(r.stats().backtracks() >= 1,
                "existence backtracking must occur: " + r.stats());
    }

    @Test
    void requiredConflictFixtureIsUnsatThroughSearch() throws Exception {
        var r = run("required-conflict.txt", 10);
        assertEquals(com.example.nonoverlap.api.Status.UNSAT, r.status());
        assertTrue(r.stats().nodes() >= 1);
    }

    @Test
    void allDiffOptionalGivesTwoAndRequiredGivesUnsat() throws Exception {
        var opt = run("all-diff.txt", 50);
        assertEquals(com.example.nonoverlap.api.Status.SAT, opt.status());
        assertEquals(2, opt.solutions().size());

        var unsat = run("all-diff-unsat.txt", 50);
        assertEquals(com.example.nonoverlap.api.Status.UNSAT, unsat.status());
        assertTrue(unsat.stats().backtracks() > 0,
                "backtracking required to prove UNSAT: " + unsat.stats());
    }

    @Test
    void unknownSafeDoesNotPruneBeforeExistenceDecision() throws Exception {
        var r = run("unknown-safe.txt", 50);
        assertEquals(com.example.nonoverlap.api.Status.SAT, r.status());
        assertEquals(1, r.solutions().size());
        assertEquals(com.example.nonoverlap.model.Existence.FALSE,
                r.solutions().get(0).get("B").resolvedExistence());
    }
}
