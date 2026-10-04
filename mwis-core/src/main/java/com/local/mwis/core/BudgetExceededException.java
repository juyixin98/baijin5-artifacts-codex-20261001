package com.local.mwis.core;

/** Raised when the DP creates more table entries than the configured budget allows. */
public final class BudgetExceededException extends RuntimeException {

    private final long budget;
    private final long attempted;
    private final int nodeId;

    public BudgetExceededException(long budget, long attempted, int nodeId) {
        super("table entry budget exceeded: budget=" + budget
                + " attempted=" + attempted + " at nice node " + nodeId);
        this.budget = budget;
        this.attempted = attempted;
        this.nodeId = nodeId;
    }

    public long budget() {
        return budget;
    }

    public long attempted() {
        return attempted;
    }

    public int nodeId() {
        return nodeId;
    }
}
