package com.local.mwis.exhaustive;

/** Raised when exhaustive enumeration is requested for a graph beyond the safe size. */
public final class InputTooLargeException extends RuntimeException {

    private final int vertexCount;
    private final int limit;

    public InputTooLargeException(int vertexCount, int limit) {
        super("exhaustive enumeration refused: vertexCount=" + vertexCount + " limit=" + limit);
        this.vertexCount = vertexCount;
        this.limit = limit;
    }

    public int vertexCount() {
        return vertexCount;
    }

    public int limit() {
        return limit;
    }
}
