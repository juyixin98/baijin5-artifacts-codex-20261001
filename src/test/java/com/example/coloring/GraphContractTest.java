package com.example.coloring;

import com.example.coloring.graph.Graph;
import com.example.coloring.graph.GraphContractException;
import com.example.coloring.graph.Graphs;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GraphContractTest {

    @Test
    void rejectsNegativeOrder() {
        GraphContractException ex = assertThrows(GraphContractException.class,
                () -> Graph.builder(-1));
        assertEquals(GraphContractException.Violation.NEGATIVE_VERTEX_COUNT, ex.violation());
    }

    @Test
    void rejectsSelfLoop() {
        GraphContractException ex = assertThrows(GraphContractException.class,
                () -> Graph.builder(3).edge(1, 1));
        assertEquals(GraphContractException.Violation.SELF_LOOP, ex.violation());
    }

    @Test
    void rejectsVertexOutOfRangeLowAndHigh() {
        assertThrows(GraphContractException.class, () -> Graph.builder(3).edge(-1, 0));
        GraphContractException ex = assertThrows(GraphContractException.class,
                () -> Graph.builder(3).edge(0, 3));
        assertEquals(GraphContractException.Violation.VERTEX_OUT_OF_RANGE, ex.violation());
    }

    @Test
    void rejectsNullLabel() {
        GraphContractException ex = assertThrows(GraphContractException.class,
                () -> Graph.builder(2).label(0, null));
        assertEquals(GraphContractException.Violation.NULL_LABEL, ex.violation());
    }

    @Test
    void duplicateEdgeIsIdempotentInSimpleGraph() {
        Graph graph = Graph.builder(2).edge(0, 1).edge(1, 0).build();
        assertEquals(1, graph.edgeCount());
    }

    @Test
    void buildsStandardGraphs() {
        assertEquals(0, Graphs.empty().order());
        assertEquals(0, Graphs.edgeless(4).edgeCount());
        assertEquals(10, Graphs.complete(5).edgeCount());
        assertEquals(5, Graphs.cycle(5).edgeCount());
        assertEquals(4, Graphs.path(5).edgeCount());
        assertEquals(15, Graphs.petersen().edgeCount());
    }

    @Test
    void findsComponentsOfDisconnectedGraph() {
        Graph graph = Graphs.union(Graphs.cycle(3), Graphs.complete(2));
        List<List<Integer>> components = graph.components();
        assertEquals(2, components.size());
        assertEquals(List.of(0, 1, 2), components.get(0));
        assertEquals(List.of(3, 4), components.get(1));
        assertFalse(graph.hasEdge(2, 3));
        assertTrue(graph.hasEdge(3, 4));
    }
}
