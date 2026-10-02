package com.example.coloring.graph;

/**
 * Raised when an input object does not satisfy the finite simple graph contract.
 *
 * <p>The category is machine-readable so callers (and tests) can assert the
 * concrete failure class instead of relying on a free-text message.</p>
 */
public final class GraphContractException extends RuntimeException {

    public enum Violation {
        NEGATIVE_VERTEX_COUNT,
        SELF_LOOP,
        VERTEX_OUT_OF_RANGE,
        LABEL_COUNT_MISMATCH,
        NULL_LABEL
    }

    private final Violation violation;

    public GraphContractException(Violation violation, String message) {
        super(message);
        this.violation = violation;
    }

    public Violation violation() {
        return violation;
    }
}
