package com.example.coloring.diag;

/** Receives decisions as the solver runs; implementations must be thread-agnostic (single-threaded search). */
public interface SearchListener {

    void onEvent(SearchEvent event);

    /** No-op listener used when diagnostics are disabled. */
    static SearchListener noop() {
        return event -> {
        };
    }
}
