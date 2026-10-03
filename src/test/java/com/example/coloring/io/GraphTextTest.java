package com.example.coloring.io;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.example.coloring.model.GraphFormatException;
import com.example.coloring.model.Graphs;
import com.example.coloring.model.SimpleGraph;
import org.junit.jupiter.api.Test;

class GraphTextTest {

    @Test
    void parsesCommentsEdgesAndRoundTrips() {
        SimpleGraph g = GraphText.read("""
                # header comment
                n 4

                e 0 1   # trailing comment
                e 1 2
                e 2 3
                """);
        assertEquals(4, g.n());
        assertEquals(3, g.edgeCount());
        assertEquals(Graphs.path(4).edgeCount(), g.edgeCount());
        assertEquals("n 4\ne 0 1\ne 1 2\ne 2 3\n",
                GraphText.write(g).lines().skip(1).reduce("", (a, l) -> a + l + "\n"));
    }

    @Test
    void rejectsMalformedInputWithLineNumbers() {
        GraphFormatException e1 = assertThrows(GraphFormatException.class,
                () -> GraphText.read("e 0 1\nn 2\n"));
        assertEquals(1, e1.line());

        GraphFormatException e2 = assertThrows(GraphFormatException.class,
                () -> GraphText.read("n 2\ne 0 2\n"));
        assertEquals(2, e2.line());

        assertThrows(GraphFormatException.class, () -> GraphText.read("n 2\nx 0 1\n"));
        assertThrows(GraphFormatException.class, () -> GraphText.read("n 2\ne 0 0\n"));
        assertThrows(GraphFormatException.class, () -> GraphText.read(""));
        assertThrows(GraphFormatException.class, () -> GraphText.read("n 2\nn 3\n"));
    }
}
