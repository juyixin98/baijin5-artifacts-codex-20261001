package dev.local.seqprop.diag;

/**
 * Why propagation or search stopped. Categories are specific so tests can
 * assert the exact failure class rather than a generic boolean.
 */
public enum FailureCategory {
  /** No failure detected. */
  NONE,
  /** A variable's domain became empty. */
  EMPTY_DOMAIN,
  /** Some window can contain at most fewer than {@code lo} target values. */
  WINDOW_LOWER_VIOLATION,
  /** Some window must contain more than {@code hi} target values. */
  WINDOW_UPPER_VIOLATION,
  /**
   * Every individual window is locally consistent but the prefix-sum system
   * of overlapping windows contains a negative cycle: overlaps cannot be
   * jointly satisfied.
   */
  JOINT_OVERLAP_CONTRADICTION,
  /** Propagation could not decide (e.g. search node budget exhausted). */
  UNDECIDED
}
