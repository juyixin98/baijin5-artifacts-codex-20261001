package dev.local.seqprop.model;

/**
 * How windows that do not reach the configured window length near the two
 * ends of the constrained variable range are handled.
 *
 * <ul>
 *   <li>{@link #FULL_ONLY}: only windows of exactly {@code windowLength}
 *       positions are constrained; positions near the ends that cannot form a
 *       full window are left unconstrained by this SEQUENCE.</li>
 *   <li>{@link #TRUNCATED_ENDS}: clipped windows of length 1..windowLength-1
 *       are emitted at both ends and the same [lo, hi] bounds apply to them.</li>
 * </ul>
 */
public enum EndMode {
  FULL_ONLY,
  TRUNCATED_ENDS
}
