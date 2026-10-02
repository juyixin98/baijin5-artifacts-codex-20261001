package com.example.clique;

import com.example.clique.error.CliqueException;
import com.example.clique.error.FailureCategory;
import com.example.clique.graph.Graph;
import com.example.clique.graph.GraphBuilder;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GraphContractTest {

    @Test
    void emptyGraphHasNoVerticesAndNoEdges() {
        Graph g = Graph.builder(0).build();
        assertEquals(0, g.vertexCount());
        assertEquals(0, g.edgeCount());
        assertEquals(0, g.vertices().bitCount());
    }

    @Test
    void duplicateEdgesAreDeduplicated() {
        Graph g = Graph.builder(3)
                .addEdge(0, 1)
                .addEdge(0, 1)   // exact duplicate
                .addEdge(1, 0)   // reversed duplicate
                .addEdge(1, 2)
                .build();
        assertEquals(2, g.edgeCount());
        assertEquals(2, g.degree(0) + g.degree(2));
    }

    @Test
    void adjacencyIsSymmetric() {
        Graph g = TestSupport.graph(4, 0, 3, 1, 2);
        assertTrue(g.adjacent(0, 3) && g.adjacent(3, 0));
        assertFalse(g.adjacent(0, 1));
    }

    @Test
    void selfLoopIsInputError() {
        CliqueException e = assertThrows(CliqueException.class,
                () -> Graph.builder(2).addEdge(1, 1));
        assertEquals(FailureCategory.INPUT_ERROR, e.category());
    }

    @Test
    void outOfRangeVertexIsInputError() {
        GraphBuilder b = Graph.builder(2);
        CliqueException e = assertThrows(CliqueException.class, () -> b.addEdge(0, 2));
        assertEquals(FailureCategory.INPUT_ERROR, e.category());
    }

    @Test
    void negativeVertexCountIsInputError() {
        CliqueException e = assertThrows(CliqueException.class, () -> Graph.builder(-1));
        assertEquals(FailureCategory.INPUT_ERROR, e.category());
    }

    @Test
    void vertexCountAboveGuardIsResourceExhausted() {
        CliqueException e = assertThrows(CliqueException.class,
                () -> Graph.builder(Graph.MAX_VERTICES + 1));
        assertEquals(FailureCategory.RESOURCE_EXHAUSTED, e.category());
    }

    @Test
    void builderReuseAfterBuildIsStateConflict() {
        GraphBuilder b = Graph.builder(2);
        b.addEdge(0, 1);
        b.build();
        CliqueException rebuild = assertThrows(CliqueException.class, b::build);
        assertEquals(FailureCategory.STATE_CONFLICT, rebuild.category());
        CliqueException mutate = assertThrows(CliqueException.class, () -> b.addEdge(0, 1));
        assertEquals(FailureCategory.STATE_CONFLICT, mutate.category());
    }

    @Test
    void vertexQueryOutOfRangeIsInputError() {
        Graph g = Graph.builder(1).build();
        CliqueException e = assertThrows(CliqueException.class, () -> g.neighbors(1));
        assertEquals(FailureCategory.INPUT_ERROR, e.category());
    }
}
