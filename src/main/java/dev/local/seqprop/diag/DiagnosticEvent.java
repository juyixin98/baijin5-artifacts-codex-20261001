package dev.local.seqprop.diag;

/**
 * One traceable diagnostic record. {@code requestId} groups all events of one
 * run; {@code kind} states what happened; payloads are rendered through
 * {@link Redactor} when the model is flagged sensitive.
 */
public record DiagnosticEvent(
    String requestId,
    Kind kind,
    String constraintId,
    Integer position,
    String detail
) {

  public enum Kind {
    WINDOW_ACCEPTED,
    WINDOW_REJECTED_LOWER,
    WINDOW_REJECTED_UPPER,
    VALUE_REMOVED,
    JOINT_CONTRADICTION,
    PROPAGATION_FIXPOINT,
    SEARCH_DECISION,
    SEARCH_BACKTRACK,
    SEARCH_SOLUTION,
    SEARCH_EXHAUSTED,
    UNDECIDED
  }

  public static DiagnosticEvent of(String requestId, Kind kind, String constraintId,
                                   Integer position, String detail) {
    return new DiagnosticEvent(requestId, kind, constraintId, position, detail);
  }
}
