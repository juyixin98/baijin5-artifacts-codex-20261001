package dev.local.seqprop.prop;

import dev.local.seqprop.diag.FailureCategory;

/** Outcome of one propagation pass to fixpoint. */
public record PropagationResult(
    boolean consistent,
    FailureCategory failureCategory,
    int removedValues,
    int fixpointRounds,
    String failedConstraintId,
    int[] failedWindow
) {

  static PropagationResult ok(int removed, int rounds) {
    return new PropagationResult(true, FailureCategory.NONE, removed, rounds, null, null);
  }

  static PropagationResult failure(FailureCategory category, String constraintId, int[] window) {
    return new PropagationResult(false, category, 0, 0, constraintId, window);
  }
}
