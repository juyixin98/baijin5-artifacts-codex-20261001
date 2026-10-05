package clique.bound;

import clique.TestGraphs;
import clique.config.CliqueConfig;
import clique.error.CliqueException;
import clique.error.ErrorCategory;
import clique.graph.Graph;
import clique.run.RunLog;
import clique.search.BronKerbosch;
import clique.search.CancellationToken;
import clique.search.SearchResult;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 界与证书；极大与最大的概念区分在此固化。 */
class BoundsCertificateTest {

    private static List<BigInteger> enumerate(Graph g) {
        SearchResult r = new BronKerbosch(g, CliqueConfig.defaults(), RunLog.create())
                .enumerate(CancellationToken.none());
        assertTrue(r.completed());
        return r.cliques();
    }

    @Test
    void completeGraphHasTightBounds() {
        Graph g = TestGraphs.complete(6);
        BoundsCertificate b = BoundsCertificate.assess(g, enumerate(g));
        assertEquals(6, b.lower());
        assertEquals(6, b.upper());
        assertTrue(b.verify(g));
    }

    @Test
    void cycleBoundsAreConsistent() {
        Graph g = TestGraphs.cycle(5);
        BoundsCertificate b = BoundsCertificate.assess(g, enumerate(g));
        assertEquals(2, b.lower()); // 枚举完整时 lower 恰为 omega
        assertTrue(b.upper() >= 2 && b.upper() <= 3, "greedy coloring of C5 uses <=3 colors");
        assertTrue(b.verify(g));
    }

    @Test
    void maximalDoesNotImplyMaximum() {
        // 三角形 {0,1,2} 与独立边 {3,4}：{3,4} 极大但非最大
        Graph g = new clique.graph.GraphBuilder(5)
                .addEdge(0, 1).addEdge(0, 2).addEdge(1, 2)
                .addEdge(3, 4)
                .build();
        List<BigInteger> cliques = enumerate(g);
        assertEquals(2, cliques.size());
        BoundsCertificate bounds = BoundsCertificate.assess(g, cliques);
        assertEquals(3, bounds.lower()); // omega = 3

        BigInteger smallButMaximal = TestGraphs.setOf(3, 4);
        MaximalityCertificate cert = MaximalityCertificate.issue(g, smallButMaximal);
        assertTrue(cert.verify(g), "size-2 clique is maximal yet not maximum");
        assertTrue(smallButMaximal.bitCount() < bounds.lower());
    }

    @Test
    void maximalityCertificateRejectsExtendableClique() {
        Graph g = TestGraphs.complete(4);
        CliqueException e = assertThrows(CliqueException.class,
                () -> MaximalityCertificate.issue(g, TestGraphs.setOf(0, 1)));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
    }

    @Test
    void maximalityCertificateRejectsNonClique() {
        Graph g = TestGraphs.path(3);
        CliqueException e = assertThrows(CliqueException.class,
                () -> MaximalityCertificate.issue(g, TestGraphs.setOf(0, 2)));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
    }

    @Test
    void tamperedColoringFailsVerification() {
        Graph g = TestGraphs.complete(3);
        BoundsCertificate b = BoundsCertificate.assess(g, enumerate(g));
        int[] colors = b.coloring();
        colors[0] = colors[1]; // 制造同色相邻
        assertFalse(GreedyColoring.isProper(g, colors));
        assertTrue(b.verify(g)); // 原证书不受影响
    }

    @Test
    void upperBoundNeverBelowOmegaOnRandomGraphs() {
        for (long seed = 1; seed <= 10; seed++) {
            Graph g = TestGraphs.random(14, 0.6, seed);
            BoundsCertificate b = BoundsCertificate.assess(g, enumerate(g));
            assertTrue(b.verify(g), "seed=" + seed);
            assertTrue(b.lower() <= b.upper(), "seed=" + seed);
        }
    }
}
