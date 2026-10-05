package clique.search;

import clique.TestGraphs;
import clique.bound.CliqueValidator;
import clique.config.CliqueConfig;
import clique.graph.Graph;
import clique.reference.BruteForceMaximalCliques;
import clique.run.RunLog;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.List;
import java.util.TreeSet;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 穷举对照：BK 输出与独立的 2^n 子集穷举参考逐一比对。
 * 参考真值不经过被测核心生成。覆盖空图、稠密图与大量重叠团。
 */
class CrossCheckTest {

    private static SearchResult runBk(Graph g, CliqueConfig.PivotStrategy pivot, RunLog log) {
        CliqueConfig cfg = new CliqueConfig(100_000_000L, pivot, CliqueConfig.Verbosity.SUMMARY, 24);
        SearchResult r = new BronKerbosch(g, cfg, log).enumerate(CancellationToken.none());
        assertTrue(r.completed(), "runId=" + log.runId());
        return r;
    }

    private static void assertMatchesReference(Graph g, String label) {
        RunLog log = RunLog.create();
        SearchResult r = runBk(g, CliqueConfig.PivotStrategy.MAX_INTERSECTION, log);
        List<BigInteger> reference = BruteForceMaximalCliques.maximalCliques(g, 24);
        TreeSet<BigInteger> actual = r.cliqueSet();
        assertEquals(new TreeSet<>(reference), actual,
                label + " mismatch, runId=" + log.runId() + " n=" + g.n() + " m=" + g.edgeCount());
        // 唯一性：列表无重复
        assertEquals(actual.size(), r.cliques().size(),
                label + " duplicate output, runId=" + log.runId());
    }

    @Test
    void allGraphsOnFourVertices() {
        // 2^6 = 64 张图逐一与穷举参考比对
        for (long mask = 0; mask < (1L << 6); mask++) {
            assertMatchesReference(TestGraphs.fromMask(4, mask), "graph4 mask=" + mask);
        }
    }

    @Test
    void allGraphsOnFiveVertices() {
        // 2^10 = 1024 张图逐一与穷举参考比对
        for (long mask = 0; mask < (1L << 10); mask++) {
            assertMatchesReference(TestGraphs.fromMask(5, mask), "graph5 mask=" + mask);
        }
    }

    @Test
    void seededRandomGraphsMatchReference() {
        int[] sizes = {6, 8, 10, 12, 14, 16};
        double[] ps = {0.2, 0.5, 0.8};
        for (int n : sizes) {
            for (double p : ps) {
                for (long seed = 1; seed <= 3; seed++) {
                    Graph g = TestGraphs.random(n, p, seed * 1000 + n);
                    assertMatchesReference(g, "random n=" + n + " p=" + p + " seed=" + seed);
                }
            }
        }
    }

    @Test
    void bothPivotStrategiesAgreeWithReference() {
        for (long seed = 1; seed <= 5; seed++) {
            Graph g = TestGraphs.random(12, 0.5, seed);
            List<BigInteger> reference = BruteForceMaximalCliques.maximalCliques(g, 24);
            for (CliqueConfig.PivotStrategy pivot : CliqueConfig.PivotStrategy.values()) {
                RunLog log = RunLog.create();
                SearchResult r = runBk(g, pivot, log);
                assertEquals(new TreeSet<>(reference), r.cliqueSet(),
                        "pivot=" + pivot + " seed=" + seed + " runId=" + log.runId());
            }
        }
    }

    @Test
    void heavilyOverlappingCliquesUniqueAndComplete() {
        // 大量重叠团 + 额外连边制造更多重叠
        Graph g = TestGraphs.overlappingTriangles(9);
        assertMatchesReference(g, "overlappingTriangles(9)");
    }

    @Test
    void everyEmittedCliqueIndependentlyVerified() {
        // 对较大图（超出穷举范围）逐条独立验证团性与极大性
        Graph g = TestGraphs.random(40, 0.6, 99);
        RunLog log = RunLog.create();
        SearchResult r = runBk(g, CliqueConfig.PivotStrategy.MAX_INTERSECTION, log);
        assertEquals(r.cliqueSet().size(), r.cliques().size(), "duplicate, runId=" + log.runId());
        for (BigInteger c : r.cliques()) {
            assertTrue(CliqueValidator.isClique(g, c), "not a clique: " + c + " runId=" + log.runId());
            assertTrue(CliqueValidator.isMaximal(g, c), "not maximal: " + c + " runId=" + log.runId());
        }
    }
}
