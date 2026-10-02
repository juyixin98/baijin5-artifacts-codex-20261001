package com.example.clique.error;

/**
 * Distinguishable failure categories for the clique engine. Every failure is
 * raised as a {@link CliqueException} carrying exactly one of these categories
 * so callers and test logs can tell input mistakes, lifecycle conflicts,
 * resource guards and genuine computation failures apart.
 */
public enum FailureCategory {
    /** Bad caller input: vertex out of range, self-loop, null argument, negative sizes. */
    INPUT_ERROR,
    /** Lifecycle conflict: builder reused after build(), enumerator re-entered while active. */
    STATE_CONFLICT,
    /** A configured resource guard fired (vertex cap, clique-count cap, brute-force cap). */
    RESOURCE_EXHAUSTED,
    /** Internal invariant or certificate check failed, or an unexpected error was wrapped. */
    COMPUTATION_FAILED
}
