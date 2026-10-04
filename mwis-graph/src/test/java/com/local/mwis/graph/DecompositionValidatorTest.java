package com.local.mwis.graph;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DecompositionValidatorTest {

    private final DecompositionValidator validator = new DecompositionValidator();

    private static Graph path4() {
        return Graph.of(4, List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(2, 3)));
    }

    @Test
    void validPathDecompositionAccepted() {
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 1), List.of(1, 2), List.of(2, 3)),
                List.of(Edge.of(0, 1), Edge.of(1, 2)), 0);
        ValidationResult r = validator.validate(path4(), td);
        assertTrue(r.isValid(), () -> "unexpected failures: " + r.failures());
    }

    @Test
    void uncoveredEdgeRejected() {
        // triangle 0-1-2 but no bag contains {0,2}
        Graph triangle = Graph.of(3, List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(0, 2)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 1), List.of(1, 2)),
                List.of(Edge.of(0, 1)), 0);
        ValidationResult r = validator.validate(triangle, td);
        assertFalse(r.isValid());
        assertEquals(FailureCategory.EDGE_NOT_COVERED, r.primaryCategory());
        assertTrue(r.failures().stream().anyMatch(f -> f.context().contains("(0,2)")));
    }

    @Test
    void disconnectedVertexOccurrencesRejected() {
        // vertex 2 sits in bags 0 and 2 but not in bag 1 between them
        Graph g = Graph.of(4, List.of(Edge.of(0, 2), Edge.of(2, 3)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 2), List.of(1), List.of(2, 3)),
                List.of(Edge.of(0, 1), Edge.of(1, 2)), 0);
        ValidationResult r = validator.validate(g, td);
        assertFalse(r.isValid());
        assertEquals(FailureCategory.VERTEX_OCCURRENCES_DISCONNECTED, r.primaryCategory());
    }

    @Test
    void cyclicBagAdjacencyRejected() {
        Graph g = Graph.of(3, List.of(Edge.of(0, 1), Edge.of(1, 2)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 1), List.of(1, 2), List.of(0, 1, 2)),
                List.of(Edge.of(0, 1), Edge.of(1, 2), Edge.of(2, 0)), 0);
        ValidationResult r = validator.validate(g, td);
        assertFalse(r.isValid());
        assertEquals(FailureCategory.DECOMPOSITION_NOT_A_TREE, r.primaryCategory());
    }

    @Test
    void disconnectedBagAdjacencyRejected() {
        Graph g = Graph.of(2, List.of(Edge.of(0, 1)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 1), List.of(0, 1)),
                List.of(), 0);
        ValidationResult r = validator.validate(g, td);
        assertFalse(r.isValid());
        assertEquals(FailureCategory.DECOMPOSITION_NOT_A_TREE, r.primaryCategory());
    }

    @Test
    void duplicateVertexInBagRejected() {
        Graph g = Graph.of(2, List.of(Edge.of(0, 1)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 0, 1)), List.of(), 0);
        ValidationResult r = validator.validate(g, td);
        assertFalse(r.isValid());
        assertTrue(r.failures().stream()
                .anyMatch(f -> f.category() == FailureCategory.DUPLICATE_VERTEX_IN_BAG));
    }

    @Test
    void bagVertexOutOfRangeRejected() {
        Graph g = Graph.of(2, List.of(Edge.of(0, 1)));
        TreeDecomposition td = TreeDecomposition.of(
                List.of(List.of(0, 1, 7)), List.of(), 0);
        ValidationResult r = validator.validate(g, td);
        assertFalse(r.isValid());
        assertTrue(r.failures().stream()
                .anyMatch(f -> f.category() == FailureCategory.BAG_VERTEX_OUT_OF_RANGE));
    }

    @Test
    void emptyDecompositionRejected() {
        Graph g = Graph.of(1, List.of());
        TreeDecomposition td = TreeDecomposition.of(List.of(), List.of(), 0);
        ValidationResult r = validator.validate(g, td);
        assertFalse(r.isValid());
        assertEquals(FailureCategory.EMPTY_DECOMPOSITION, r.primaryCategory());
    }

    @Test
    void graphRejectsOutOfRangeEdges() {
        try {
            Graph.of(2, List.of(Edge.of(0, 5)));
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("expected IllegalArgumentException");
    }
}
