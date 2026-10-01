package com.example.nonoverlap.api;

/** Immutable counters describing how the search spent its effort. */
public record SearchStats(
        int nodes,
        int branches,
        int backtracks,
        int prunes,
        long pairChecks,
        int solutions,
        long elapsedMillis) {

    public static SearchStats empty() {
        return new SearchStats(0, 0, 0, 0, 0L, 0, 0L);
    }
}
