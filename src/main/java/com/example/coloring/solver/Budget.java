package com.example.coloring.solver;

import com.example.coloring.diag.Reason;

/** Shared mutable counter for the node and wall-clock budgets. */
final class Budget {

    private final long nodeLimit;
    private final long deadlineNanos;
    private long nodes;

    Budget(long nodeLimit, long timeMillis) {
        this.nodeLimit = nodeLimit;
        this.deadlineNanos = System.nanoTime() + timeMillis * 1_000_000L;
    }

    long nodes() {
        return nodes;
    }

    void chargeNode() {
        nodes++;
    }

    /** Explicit stopping condition; identifies which budget (if any) fired. */
    Reason exhaustedReason() {
        if (nodes > nodeLimit) {
            return Reason.NODE_BUDGET_EXHAUSTED;
        }
        if (System.nanoTime() > deadlineNanos) {
            return Reason.TIME_BUDGET_EXHAUSTED;
        }
        return null;
    }
}
