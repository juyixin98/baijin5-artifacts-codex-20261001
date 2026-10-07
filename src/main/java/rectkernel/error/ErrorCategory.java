package rectkernel.error;

/**
 * Distinguishable failure categories of the kernel. Every public entry point
 * maps its outcome to exactly one of these (SAT maps to none).
 */
public enum ErrorCategory {
    /** Malformed problem: bad numbers, negative sizes, inverted/empty domains, duplicate ids. */
    INPUT_ERROR,
    /** Propagation proved an unavoidable overlap between mandatory rectangles (UNSAT). */
    STATE_CONFLICT,
    /** Node, time or solution cap reached; satisfiability unknown (LIMIT). */
    RESOURCE_EXHAUSTED,
    /** Unexpected internal failure (bug guard). */
    COMPUTATION_FAILED
}
