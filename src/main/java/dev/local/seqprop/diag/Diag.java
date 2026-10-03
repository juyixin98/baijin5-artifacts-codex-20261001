package dev.local.seqprop.diag;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Append-only per-run diagnostic log. */
public final class Diag {

  private final String requestId;
  private final boolean sensitive;
  private final List<DiagnosticEvent> events = new ArrayList<>();

  public Diag(String requestId, boolean sensitive) {
    this.requestId = requestId;
    this.sensitive = sensitive;
  }

  public String requestId() { return requestId; }
  public boolean sensitive() { return sensitive; }

  public Diag add(DiagnosticEvent.Kind kind, String constraintId, Integer position, String detail) {
    events.add(DiagnosticEvent.of(requestId, kind, constraintId, position, detail));
    return this;
  }

  public List<DiagnosticEvent> events() {
    return Collections.unmodifiableList(events);
  }
}
