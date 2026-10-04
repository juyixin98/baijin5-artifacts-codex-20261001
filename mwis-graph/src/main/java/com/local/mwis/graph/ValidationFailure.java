package com.local.mwis.graph;

/** One concrete validation problem: category plus human-readable detail and context. */
public record ValidationFailure(FailureCategory category, String message, String context) {

    @Override
    public String toString() {
        return category + ": " + message + (context == null || context.isEmpty() ? "" : " [" + context + "]");
    }
}
