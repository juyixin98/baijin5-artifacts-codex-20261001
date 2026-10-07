package rectkernel;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;
import rectkernel.evidence.BruteForceEnumerator;
import rectkernel.evidence.RunLog;
import rectkernel.model.Domain;
import rectkernel.model.Problem;
import rectkernel.model.RectSpec;
import rectkernel.search.Solution;
import rectkernel.search.SolveOptions;
import rectkernel.search.SolveResult;
import rectkernel.search.SolveStatus;
import rectkernel.search.Solver;

/**
 * Independent-evidence tests: the solver's layout sets are compared against
 * (a) fully hand-computed references and (b) the brute-force enumerator,
 * which shares no code with the propagation kernel.
 */
class CrossCheckTest {

    private static final long SEED = 20261007L;
    private static final int CASES = 300;
    private static final long COMBO_LIMIT = 50_000;

    @Test
    void handEnumeratedPresenceSetsAndLayouts() {
        // Hand-computed reference: grid 2x1, A mandatory 1x1, O optional 1x1.
        // O absent: A sits on either cell (2 layouts).
        // O present: {A, O} occupy the two cells in 2 orders (2 layouts).
        Problem p = Problem.of(2, 1, List.of(
                new RectSpec("A", 1, 1, false, new Domain(0, 1, 0, 0)),
                new RectSpec("O", 1, 1, true, new Domain(0, 1, 0, 0))), "X-hand");
        SolveResult r = new Solver(SolveOptions.all(1000), RunLog.inMemory(), "X-hand").solve(p);
        Set<String> expected = Set.of(
                "A=P(0,0),O=A",
                "A=P(1,0),O=A",
                "A=P(0,0),O=P(1,0)",
                "A=P(1,0),O=P(0,0)");
        assertEquals(expected, canonicalSet(r));
    }

    @Test
    void handEnumeratedZeroAreaAndEdgeTouchingLayouts() {
        // Hand-computed: A 1x1 fixed at (0,0); B 1x1 with x in {0,1} on a 2x1
        // grid; Z 0x1 with x in {0,1,2}. B must take x=1 (x=0 overlaps A);
        // Z never conflicts -> 3 layouts.
        Problem p = Problem.of(2, 1, List.of(
                new RectSpec("A", 1, 1, false, Domain.singleton(0, 0)),
                new RectSpec("B", 1, 1, false, new Domain(0, 1, 0, 0)),
                new RectSpec("Z", 0, 1, false, new Domain(0, 2, 0, 0))), "X-zero");
        SolveResult r = new Solver(SolveOptions.all(1000), RunLog.inMemory(), "X-zero").solve(p);
        Set<String> expected = Set.of(
                "A=P(0,0),B=P(1,0),Z=P(0,0)",
                "A=P(0,0),B=P(1,0),Z=P(1,0)",
                "A=P(0,0),B=P(1,0),Z=P(2,0)");
        assertEquals(expected, canonicalSet(r));
    }

    @Test
    void solverMatchesIndependentEnumeratorOnRandomSmallProblems() throws Exception {
        Random rnd = new Random(SEED);
        Path logFile = Path.of("target", "run-logs", "cross-check-" + SEED + ".log");
        int compared = 0;
        int skipped = 0;
        try (RunLog log = RunLog.toFile(logFile)) {
            for (int caseNo = 0; caseNo < CASES; caseNo++) {
                String runId = "X" + caseNo;
                Problem p = randomProblem(rnd, caseNo);
                long bound = BruteForceEnumerator.combinationBound(p);
                if (bound > COMBO_LIMIT) {
                    skipped++;
                    log.event(runId, "cross-check-skip",
                            "case=" + caseNo + " combinationBound=" + bound + " problem=[" + p + "]");
                    continue;
                }
                SolveResult r = new Solver(SolveOptions.all(200_000), log, runId).solve(p);
                List<List<BruteForceEnumerator.Assignment>> layouts =
                        BruteForceEnumerator.enumerate(p, 200_000, runId);
                Set<String> expected = layouts.stream()
                        .map(BruteForceEnumerator::canonical).collect(Collectors.toSet());
                Set<String> actual = canonicalSet(r);
                log.event(runId, "cross-check", "case=" + caseNo + " seed=" + SEED
                        + " solverStatus=" + r.status() + " solverSolutions=" + actual.size()
                        + " enumeratorLayouts=" + expected.size() + " problem=[" + p + "]");
                assertEquals(expected.isEmpty() ? SolveStatus.UNSAT : SolveStatus.SAT, r.status(),
                        "status mismatch case=" + caseNo + " problem=" + p);
                assertEquals(expected, actual, "layout set mismatch case=" + caseNo + " problem=" + p);
                compared++;
            }
        }
        // Guard against the generator silently degenerating to trivial cases.
        assertEquals(CASES, compared + skipped);
        org.junit.jupiter.api.Assertions.assertTrue(compared >= 250,
                "too many cases skipped: compared=" + compared + " skipped=" + skipped);
    }

    private static Set<String> canonicalSet(SolveResult r) {
        return r.solutions().stream().map(Solution::canonical).collect(Collectors.toSet());
    }

    private static Problem randomProblem(Random rnd, int caseNo) {
        long gw = 1 + rnd.nextInt(4);
        long gh = 1 + rnd.nextInt(4);
        int n = 1 + rnd.nextInt(4);
        List<RectSpec> specs = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            // Sizes 0..3 clamped to the grid: degenerate rectangles included on purpose.
            long w = Math.min(rnd.nextInt(4), gw);
            long h = Math.min(rnd.nextInt(4), gh);
            boolean optional = rnd.nextDouble() < 0.4;
            long xMax = gw - w;
            long yMax = gh - h;
            long x1 = randIncl(rnd, xMax);
            long x2 = randIncl(rnd, xMax);
            long y1 = randIncl(rnd, yMax);
            long y2 = randIncl(rnd, yMax);
            Domain d = new Domain(Math.min(x1, x2), Math.max(x1, x2),
                    Math.min(y1, y2), Math.max(y1, y2));
            specs.add(new RectSpec("R" + i, w, h, optional, d));
        }
        return Problem.of(gw, gh, specs, "X" + caseNo);
    }

    private static long randIncl(Random rnd, long hi) {
        return rnd.nextInt((int) (hi + 1));
    }
}
