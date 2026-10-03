package com.example.coloring.model;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class SimpleGraphTest {

    @Test
    void storesUndirectedEdgesWithSortedNeighbors() {
        SimpleGraph g = new SimpleGraph.Builder(4)
                .addEdge(0, 3)
                .addEdge(0, 1)
                .addEdge(1, 2)
                .build();

        assertTrue(g.adjacent(3, 0));
        assertTrue(g.adjacent(0, 3));
        assertFalse(g.adjacent(0, 2));
        assertEquals(List.of(1, 3), g.neighbors(0));
        assertEquals(List.of(0, 2), g.neighbors(1));
        assertEquals(3, g.edgeCount());
    }

    @Test
    void duplicateEdgeIsIdempotent() {
        SimpleGraph g = new SimpleGraph.Builder(3)
                .addEdge(0, 1)
                .addEdge(1, 0)
                .build();
        assertEquals(1, g.edgeCount());
        assertEquals(List.of(1), g.neighbors(0));
    }

    @Test
    void rejectsLoopsAndOutOfRangeEndpoints() {
        assertThrows(IllegalArgumentException.class,
                () -> new SimpleGraph.Builder(3).addEdge(1, 1));
        assertThrows(IndexOutOfBoundsException.class,
                () -> new SimpleGraph.Builder(3).addEdge(0, 3));
        assertThrows(IllegalArgumentException.class,
                () -> new SimpleGraph.Builder(-1));
    }

    @Test
    void builtGraphIsImmutable() {
        SimpleGraph g = new SimpleGraph.Builder(2).addEdge(0, 1).build();
        assertThrows(UnsupportedOperationException.class, () -> g.neighbors(0).add(1));
    }

    @Test
    void familiesHaveExpectedShape() {
        assertEquals(0, Graphs.empty(3).edgeCount());
        assertEquals(10, Graphs.complete(5).edgeCount());
        assertEquals(5, Graphs.cycle(5).edgeCount());
        SimpleGraph union = Graphs.disjointUnion(Graphs.complete(3), Graphs.cycle(4));
        assertEquals(7, union.n());
        assertEquals(3 + 4, union.edgeCount());
        for (int u = 0; u < 3; u++) {
            for (int v = 3; v < 7; v++) {
                assertFalse(union.adjacent(u, v), "union components must not connect");
            }
        }
    }
}
