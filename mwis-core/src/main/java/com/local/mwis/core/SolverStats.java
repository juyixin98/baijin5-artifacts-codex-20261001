package com.local.mwis.core;

/** Counters describing one solver run, reported back to callers for auditability. */
public record SolverStats(
        int nodesProcessed,
        int leafNodes,
        int introduceNodes,
        int forgetNodes,
        int joinNodes,
        long tableEntries,
        long maxTableSize,
        long tableEntryBudget) {
}
