package com.example.coloring.diag;

/** What the search decided about one candidate action. */
public enum DecisionKind {

    /** Candidate branch was explored (a child search node was entered). */
    ACCEPT_BRANCH,

    /** A complete feasible coloring was accepted as the new upper bound. */
    ACCEPT_FEASIBLE,

    /** Candidate branch was rejected without exploration. */
    REJECT,

    /** Current node was proven optimal without exploring remaining branches. */
    ACCEPT_BOUND_MATCH,

    /** Search cannot continue: node/millisecond budget exhausted. */
    UNDETERMINED_BUDGET
}
