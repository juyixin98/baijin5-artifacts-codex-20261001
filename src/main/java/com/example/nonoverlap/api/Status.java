package com.example.nonoverlap.api;

/**
 * Outcome category of a solver run. Four distinct failure families are kept
 * separate so callers never have to parse free-text messages:
 *
 * <ul>
 *   <li>{@link #INVALID_INPUT}     - input error: malformed data/arguments;</li>
 *   <li>{@link #STATE_CONFLICT}    - state conflict: the submitted state is already
 *                                    inconsistent at the root (empty required domain,
 *                                    overlapping forced placements, illegal preassignment);</li>
 *   <li>{@link #RESOURCE_EXHAUSTED}- resource exhaustion: node/check budget spent before proof;</li>
 *   <li>{@link #COMPUTATION_FAILED}- computation failure: unexpected internal/IO failure.</li>
 * </ul>
 *
 * {@link #SAT} and {@link #UNSAT} are ordinary answers rather than errors.
 */
public enum Status {
    SAT,
    UNSAT,
    STATE_CONFLICT,
    INVALID_INPUT,
    RESOURCE_EXHAUSTED,
    COMPUTATION_FAILED;

    public boolean isFailure() {
        return this != SAT && this != UNSAT;
    }
}
