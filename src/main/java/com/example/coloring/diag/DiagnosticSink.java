package com.example.coloring.diag;

/** Sink for structured search diagnostics. Implementations must be thread-agnostic;
 *  the search is single-threaded and publishes events in node order. */
@FunctionalInterface
public interface DiagnosticSink {

    void accept(DiagnosticEvent event);

    /** Sink that discards everything. */
    static DiagnosticSink noop() {
        return event -> { };
    }
}
