package clique.search;

import clique.TestGraphs;
import clique.config.CliqueConfig;
import clique.graph.Graph;
import clique.graph.GraphBuilder;
import clique.run.RunLog;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.Set;
import java.util.TreeSet;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 手工可验证的小图：断言具体极大团集合，而非"接口能调"。 */
class BronKerboschBasicTest {

    private static Set<BigInteger> run(Graph g) {
        RunLog log = RunLog.create();
        BronKerbosch bk = new BronKerbosch(g, CliqueConfig.defaults(), log);
        SearchResult r = bk.enumerate(CancellationToken.none());
        assertTrue(r.completed(), "run should complete, runId=" + log.runId());
        return r.cliqueSet();
    }

    @Test
    void emptyGraphZeroVerticesYieldsNoCliques() {
        assertEquals(Set.of(), run(TestGraphs.empty(0)));
    }

    @Test
    void edgelessGraphYieldsSingletons() {
        assertEquals(Set.of(TestGraphs.setOf(0), TestGraphs.setOf(1), TestGraphs.setOf(2)),
                run(TestGraphs.empty(3)));
    }

    @Test
    void singleEdgeIsOneMaximalClique() {
        assertEquals(Set.of(TestGraphs.setOf(0, 1)), run(TestGraphs.path(2)));
    }

    @Test
    void triangleIsOneClique() {
        assertEquals(Set.of(TestGraphs.setOf(0, 1, 2)), run(TestGraphs.complete(3)));
    }

    @Test
    void completeGraphK5HasExactlyOneMaximalClique() {
        assertEquals(Set.of(TestGraphs.setOf(0, 1, 2, 3, 4)), run(TestGraphs.complete(5)));
    }

    @Test
    void pathP4HasThreeEdgesAsMaximalCliques() {
        assertEquals(Set.of(TestGraphs.setOf(0, 1), TestGraphs.setOf(1, 2), TestGraphs.setOf(2, 3)),
                run(TestGraphs.path(4)));
    }

    @Test
    void bowtieHasTwoTriangles() {
        assertEquals(Set.of(TestGraphs.setOf(0, 1, 2), TestGraphs.setOf(2, 3, 4)),
                run(TestGraphs.bowtie()));
    }

    @Test
    void k4MinusEdgeHasTwoOverlappingTriangles() {
        assertEquals(Set.of(TestGraphs.setOf(0, 2, 3), TestGraphs.setOf(1, 2, 3)),
                run(TestGraphs.k4MinusEdge()));
    }

    @Test
    void isolatesPlusEdge() {
        Graph g = new GraphBuilder(4).addEdge(0, 1).build();
        assertEquals(Set.of(TestGraphs.setOf(0, 1), TestGraphs.setOf(2), TestGraphs.setOf(3)), run(g));
    }

    @Test
    void duplicateEdgesDoNotChangeResult() {
        Graph g = new GraphBuilder(3)
                .addEdge(0, 1).addEdge(0, 1).addEdge(1, 0)
                .addEdge(1, 2).addEdge(2, 1)
                .build();
        assertEquals(2, g.edgeCount());
        assertEquals(Set.of(TestGraphs.setOf(0, 1), TestGraphs.setOf(1, 2)), run(g));
    }

    @Test
    void overlappingTrianglesShareHubVertex() {
        // 3 个三角形共顶点 0：{0,1,2},{0,3,4},{0,5,6}
        Set<BigInteger> expected = Set.of(
                TestGraphs.setOf(0, 1, 2), TestGraphs.setOf(0, 3, 4), TestGraphs.setOf(0, 5, 6));
        assertEquals(expected, run(TestGraphs.overlappingTriangles(3)));
    }

    @Test
    void outputContainsNoDuplicates() {
        // 大量重叠团：唯一性由"列表大小 == 集合大小"直接断言
        Graph g = TestGraphs.overlappingTriangles(12);
        RunLog log = RunLog.create();
        BronKerbosch bk = new BronKerbosch(g, CliqueConfig.defaults(), log);
        SearchResult r = bk.enumerate(CancellationToken.none());
        TreeSet<BigInteger> uniq = new TreeSet<>(r.cliques());
        assertEquals(uniq.size(), r.cliques().size(),
                "duplicate output detected, runId=" + log.runId());
    }
}
