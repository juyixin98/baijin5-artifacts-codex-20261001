package com.example.coloring;

import com.example.coloring.bounds.GreedyColorer;
import com.example.coloring.cert.Coloring;
import com.example.coloring.graph.Graph;
import com.example.coloring.graph.GraphContractException;
import com.example.coloring.graph.Graphs;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Minimal vertical slice: proves build + test entry point and one real result. */
class GreedySliceTest {

    @Test
    void producesProperColoringForC5() {
        Graph c5 = Graphs.cycle(5);
        Coloring coloring = GreedyColorer.color(c5);

        assertTrue(coloring.isProper(c5), "greedy output must be a proper coloring");
        assertEquals(3, coloring.colorCount(), "DSATUR colors C5 with exactly 3 colors");
    }

    @Test
    void rejectsSelfLoopWithConcreteCategory() {
        GraphContractException ex = assertThrows(GraphContractException.class,
                () -> Graph.builder(2).edge(0, 0).build());
        assertEquals(GraphContractException.Violation.SELF_LOOP, ex.violation());
    }

    @Test
    void rejectsOutOfRangeVertex() {
        GraphContractException ex = assertThrows(GraphContractException.class,
                () -> Graph.builder(2).edge(0, 3).build());
        assertEquals(GraphContractException.Violation.VERTEX_OUT_OF_RANGE, ex.violation());
    }
}
