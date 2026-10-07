package rectkernel.search;

/** Search outcome; maps to ErrorCategory via SolveResult.category(). */
public enum SolveStatus {
    SAT,
    UNSAT,
    LIMIT
}
