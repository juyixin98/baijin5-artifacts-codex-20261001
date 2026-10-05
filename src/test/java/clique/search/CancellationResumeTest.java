package clique.search;

import clique.TestGraphs;
import clique.config.CliqueConfig;
import clique.error.CliqueException;
import clique.error.ErrorCategory;
import clique.graph.Graph;
import clique.run.RunLog;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.TreeSet;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 取消 -> 不完整标志 + 稳定续扫状态；续扫与全量运行不重不漏。 */
class CancellationResumeTest {

    private static final Graph G = TestGraphs.random(18, 0.5, 42);

    private static BronKerbosch bk(RunLog log) {
        return new BronKerbosch(G, CliqueConfig.defaults(), log);
    }

    @Test
    void cancelAfterKCommitsThenResumeEqualsFullRun() {
        RunLog fullLog = RunLog.create();
        TreeSet<BigInteger> full = bk(fullLog).enumerate(CancellationToken.none()).cliqueSet();
        assertTrue(full.size() >= 10, "fixture should have enough cliques, got " + full.size());

        for (long k : new long[]{1, 5, full.size() / 2L, full.size() - 1L}) {
            RunLog log1 = RunLog.create();
            SearchResult part = bk(log1).enumerate(CancellationToken.afterCommits(k));
            assertFalse(part.completed(), "should be incomplete, runId=" + log1.runId());
            assertNotNull(part.resumeState(), "incomplete must carry resume state");
            // 提交按外层下标分批，触发取消时实际提交数 >= k
            assertTrue(part.cliques().size() >= k,
                    "committed >= k expected, k=" + k + " got " + part.cliques().size()
                            + " runId=" + log1.runId());
            int next = part.resumeState().nextOuterIndex();
            assertTrue(next > 0 && next <= G.n(), "resume index in (0,n], got " + next);

            RunLog log2 = RunLog.create();
            SearchResult rest = bk(log2).resume(part.resumeState(), CancellationToken.none());
            assertTrue(rest.completed(), "resume should complete, runId=" + log2.runId());

            TreeSet<BigInteger> union = new TreeSet<>(part.cliques());
            union.addAll(rest.cliques());
            assertEquals(full, union, "k=" + k + " union mismatch, runIds=" + log1.runId() + "," + log2.runId());
            // 两段不相交：|part| + |rest| == |full|
            assertEquals(full.size(), part.cliques().size() + rest.cliques().size(),
                    "overlap between partial and resumed, k=" + k);
        }
    }

    @Test
    void preCancelledTokenYieldsEmptyPartialResumableFromZero() {
        RunLog log = RunLog.create();
        CancellationToken token = CancellationToken.none();
        token.cancel();
        SearchResult part = bk(log).enumerate(token);
        assertFalse(part.completed());
        assertEquals(0, part.cliques().size());
        assertEquals(0, part.resumeState().nextOuterIndex());
        SearchResult rest = bk(RunLog.create()).resume(part.resumeState(), CancellationToken.none());
        assertTrue(rest.completed());
        assertEquals(bk(RunLog.create()).enumerate(CancellationToken.none()).cliqueSet(), rest.cliqueSet());
    }

    @Test
    void resumeStateSurvivesEncodeParseRoundtrip() {
        RunLog log = RunLog.create();
        SearchResult part = bk(log).enumerate(CancellationToken.afterCommits(3));
        String encoded = part.resumeState().encode();
        ResumeState parsed = ResumeState.parse(encoded);
        SearchResult rest = bk(RunLog.create()).resume(parsed, CancellationToken.none());
        assertTrue(rest.completed());
        TreeSet<BigInteger> union = new TreeSet<>(part.cliques());
        union.addAll(rest.cliques());
        assertEquals(bk(RunLog.create()).enumerate(CancellationToken.none()).cliqueSet(), union);
    }

    @Test
    void resumeWithDifferentGraphIsStateConflict() {
        SearchResult part = bk(RunLog.create()).enumerate(CancellationToken.afterCommits(2));
        BronKerbosch other = new BronKerbosch(TestGraphs.random(18, 0.5, 43),
                CliqueConfig.defaults(), RunLog.create());
        CliqueException e = assertThrows(CliqueException.class,
                () -> other.resume(part.resumeState(), CancellationToken.none()));
        assertEquals(ErrorCategory.STATE_CONFLICT, e.category());
    }

    @Test
    void resumeWithOutOfRangeIndexIsStateConflict() {
        String bad = "BKRS1:" + G.n() + ":" + G.edgeCount() + ":" + G.fingerprint() + ":" + (G.n() + 5) + ":0";
        ResumeState state = ResumeState.parse(bad);
        CliqueException e = assertThrows(CliqueException.class,
                () -> bk(RunLog.create()).resume(state, CancellationToken.none()));
        assertEquals(ErrorCategory.STATE_CONFLICT, e.category());
    }

    @Test
    void malformedResumeStateIsInputError() {
        CliqueException e = assertThrows(CliqueException.class, () -> ResumeState.parse("garbage"));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
        CliqueException e2 = assertThrows(CliqueException.class,
                () -> ResumeState.parse("BKRS1:5:4:x:0:0"));
        assertEquals(ErrorCategory.INPUT_ERROR, e2.category());
    }

    @Test
    void stepBudgetExhaustionIsResourceExhaustedAndResumable() {
        CliqueConfig tight = new CliqueConfig(50, CliqueConfig.PivotStrategy.MAX_INTERSECTION,
                CliqueConfig.Verbosity.SUMMARY, 24);
        RunLog log = RunLog.create();
        ResourceExhaustedException e = assertThrows(ResourceExhaustedException.class,
                () -> new BronKerbosch(G, tight, log).enumerate(CancellationToken.none()));
        assertEquals(ErrorCategory.RESOURCE_EXHAUSTED, e.category());
        SearchResult partial = e.partial();
        assertFalse(partial.completed());
        assertNotNull(partial.resumeState());

        // 用充足预算续扫，并集等于全量
        SearchResult rest = bk(RunLog.create()).resume(partial.resumeState(), CancellationToken.none());
        assertTrue(rest.completed());
        TreeSet<BigInteger> union = new TreeSet<>(partial.cliques());
        union.addAll(rest.cliques());
        assertEquals(bk(RunLog.create()).enumerate(CancellationToken.none()).cliqueSet(), union);
    }

    @Test
    void repeatedChainedResumesReconstructFullResult() {
        // 多次取消-续扫链式推进，最终并集仍等于全量
        TreeSet<BigInteger> acc = new TreeSet<>();
        ResumeState state = null;
        TreeSet<BigInteger> full = bk(RunLog.create()).enumerate(CancellationToken.none()).cliqueSet();
        long chunk = Math.max(1, full.size() / 4);
        for (int i = 0; i < 10; i++) {
            BronKerbosch bk = bk(RunLog.create());
            SearchResult r = state == null
                    ? bk.enumerate(CancellationToken.afterCommits(chunk))
                    : bk.resume(state, CancellationToken.afterCommits(chunk));
            acc.addAll(r.cliques());
            if (r.completed()) {
                break;
            }
            state = r.resumeState();
        }
        assertEquals(full, acc);
    }
}
