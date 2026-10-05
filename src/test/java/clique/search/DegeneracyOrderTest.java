package clique.search;

import clique.TestGraphs;
import clique.graph.Bits;
import clique.graph.Graph;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DegeneracyOrderTest {

    @Test
    void knownDegeneracyValues() {
        assertEquals(0, DegeneracyOrder.compute(TestGraphs.empty(5)).degeneracy());
        assertEquals(1, DegeneracyOrder.compute(TestGraphs.path(6)).degeneracy());
        assertEquals(2, DegeneracyOrder.compute(TestGraphs.cycle(6)).degeneracy());
        assertEquals(4, DegeneracyOrder.compute(TestGraphs.complete(5)).degeneracy());
        // k 个三角形共一个hub：叶子度为2，degeneracy=2
        assertEquals(2, DegeneracyOrder.compute(TestGraphs.overlappingTriangles(4)).degeneracy());
    }

    @Test
    void emptyGraphHasEmptyOrder() {
        assertEquals(0, DegeneracyOrder.compute(TestGraphs.empty(0)).order().length);
    }

    @Test
    void laterNeighborCountBoundedByDegeneracy() {
        // 排序关键性质：每个顶点在序中之后的邻居数 <= degeneracy
        for (long seed = 1; seed <= 20; seed++) {
            Graph g = TestGraphs.random(15, 0.4, seed);
            DegeneracyOrder d = DegeneracyOrder.compute(g);
            int[] order = d.order();
            int[] pos = new int[g.n()];
            for (int i = 0; i < order.length; i++) {
                pos[order[i]] = i;
            }
            for (int i = 0; i < order.length; i++) {
                int v = order[i];
                int later = 0;
                for (int u : Bits.setBits(g.neighbors(v))) {
                    if (pos[u] > i) {
                        later++;
                    }
                }
                assertTrue(later <= d.degeneracy(),
                        "vertex " + v + " has " + later + " later neighbors > d=" + d.degeneracy());
            }
        }
    }

    @Test
    void orderIsPermutationAndDeterministic() {
        Graph g = TestGraphs.random(12, 0.5, 7);
        int[] a = DegeneracyOrder.compute(g).order();
        int[] b = DegeneracyOrder.compute(g).order();
        assertEquals(a.length, g.n());
        boolean[] seen = new boolean[g.n()];
        for (int i = 0; i < a.length; i++) {
            assertEquals(a[i], b[i], "deterministic at index " + i);
            assertTrue(a[i] >= 0 && a[i] < g.n() && !seen[a[i]], "permutation at index " + i);
            seen[a[i]] = true;
        }
    }
}
