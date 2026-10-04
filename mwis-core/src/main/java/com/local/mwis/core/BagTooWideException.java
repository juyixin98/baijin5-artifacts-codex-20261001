package com.local.mwis.core;

/** Raised when a bag exceeds the bitmask width supported by this implementation. */
public final class BagTooWideException extends RuntimeException {

    private final int bagSize;
    private final int limit;

    public BagTooWideException(int bagSize, int limit) {
        super("bag size " + bagSize + " exceeds supported limit " + limit);
        this.bagSize = bagSize;
        this.limit = limit;
    }

    public int bagSize() {
        return bagSize;
    }

    public int limit() {
        return limit;
    }
}
