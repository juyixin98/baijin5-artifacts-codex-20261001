package clique.graph;

import clique.error.CliqueException;
import clique.error.ErrorCategory;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 图契约：非法输入全部归入 INPUT_ERROR，且类别可区分。 */
class GraphContractTest {

    private static void assertInputError(Runnable r, String label) {
        CliqueException e = assertThrows(CliqueException.class, r::run, label);
        assertEquals(ErrorCategory.INPUT_ERROR, e.category(), label + " category");
    }

    @Test
    void negativeVertexCountRejected() {
        assertInputError(() -> new GraphBuilder(-1), "negative n");
    }

    @Test
    void outOfRangeVertexRejected() {
        GraphBuilder b = new GraphBuilder(3);
        assertInputError(() -> b.addEdge(0, 3), "vertex == n");
        assertInputError(() -> b.addEdge(-1, 0), "negative vertex");
    }

    @Test
    void selfLoopRejected() {
        GraphBuilder b = new GraphBuilder(3);
        assertInputError(() -> b.addEdge(1, 1), "self-loop");
    }

    @Test
    void duplicateEdgesAreIdempotent() {
        Graph g = new GraphBuilder(3)
                .addEdge(0, 1).addEdge(0, 1).addEdge(1, 0)
                .addEdge(1, 2)
                .build();
        assertEquals(2, g.edgeCount());
        assertTrue(g.hasEdge(0, 1) && g.hasEdge(1, 0));
    }

    @Test
    void asymmetricAdjacencyRejected() {
        BigInteger[] adj = {BigInteger.valueOf(2), BigInteger.ZERO};
        assertInputError(() -> Graph.fromAdjacency(adj), "asymmetric");
    }

    @Test
    void selfLoopBitInAdjacencyRejected() {
        BigInteger[] adj = {BigInteger.ONE};
        assertInputError(() -> Graph.fromAdjacency(adj), "self-loop bit");
    }

    @Test
    void bitsBeyondVertexRangeRejected() {
        BigInteger[] adj = {BigInteger.ZERO, BigInteger.valueOf(5)};
        assertInputError(() -> Graph.fromAdjacency(adj), "bit beyond n");
    }

    @Test
    void nullAdjacencyRejected() {
        assertInputError(() -> Graph.fromAdjacency(null), "null array");
        assertInputError(() -> Graph.fromAdjacency(new BigInteger[]{null}), "null row");
    }

    @Test
    void fingerprintStableForSameGraph() {
        Graph a = new GraphBuilder(3).addEdge(0, 1).build();
        Graph b = new GraphBuilder(3).addEdge(0, 1).build();
        assertEquals(a.fingerprint(), b.fingerprint());
        assertEquals(a.edgeCount(), b.edgeCount());
    }
}
