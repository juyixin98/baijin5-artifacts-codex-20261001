package dev.local.seqprop.prop;

import java.util.HashSet;
import java.util.Set;
import dev.local.seqprop.diag.DiagnosticEvent;
import dev.local.seqprop.diag.Diag;
import dev.local.seqprop.diag.FailureCategory;
import dev.local.seqprop.diag.Redactor;
import dev.local.seqprop.model.CspModel;
import dev.local.seqprop.model.SequenceConstraint;

/**
 * Schedules the per-constraint SEQUENCE propagators to a global fixpoint.
 *
 * <p>Each propagator establishes generalized arc consistency for its own
 * overlapping windows. When several constraints share variables, a removal
 * schedules every propagator touching that variable and the engine iterates
 * until no domain changes or a precise failure is reported.
 */
public final class PropagationEngine {

  private final CspModel model;
  private final Diag diag;
  private final Redactor redactor;

  public PropagationEngine(CspModel model, Diag diag, boolean sensitive) {
    this.model = model;
    this.diag = diag;
    this.redactor = new Redactor(sensitive, diag.requestId());
  }

  public Redactor redactor() { return redactor; }

  public PropagationResult propagate(DomainState domains) {
    long removedBefore = countRemoved(domains);
    Set<Integer> queue = new HashSet<>();
    for (int k = 0; k < model.constraints().size(); k++) {
      queue.add(k);
    }
    int rounds = 0;
    int guard = model.n() * (model.universe().size() + 1) * model.constraints().size() + 1;
    while (!queue.isEmpty()) {
      rounds++;
      if (rounds > guard) {
        diag.add(DiagnosticEvent.Kind.UNDECIDED, null, null,
            "propagation round guard exceeded");
        return PropagationResult.failure(FailureCategory.UNDECIDED, null, null);
      }
      int k = queue.iterator().next();
      queue.remove(k);
      SequenceConstraint c = model.constraints().get(k);
      SequencePropagator.Step step = new SequencePropagator().propagate(c, domains, redactor, diag);
      if (step.outcome == SequencePropagator.Outcome.FAILURE) {
        FailureCategory category = step.category;
        int[] window = step.failedWindow;
        for (int i = 0; i < domains.size(); i++) {
          if (domains.isEmpty(i)) {
            category = FailureCategory.EMPTY_DOMAIN;
            break;
          }
        }
        return PropagationResult.failure(category, c.id(), window);
      }
      if (step.outcome == SequencePropagator.Outcome.CHANGED) {
        for (int j = 0; j < model.constraints().size(); j++) {
          SequenceConstraint other = model.constraints().get(j);
          if (touches(other, c)) {
            queue.add(j);
          }
        }
      }
    }
    diag.add(DiagnosticEvent.Kind.PROPAGATION_FIXPOINT, null, null,
        "global fixpoint after " + rounds + " propagator invocations");
    long removedAfter = countRemoved(domains);
    return PropagationResult.ok((int) (removedAfter - removedBefore), rounds);
  }

  private boolean touches(SequenceConstraint a, SequenceConstraint b) {
    return a.from() <= b.to() && b.from() <= a.to();
  }

  private long countRemoved(DomainState domains) {
    long removed = 0;
    for (int i = 0; i < domains.size(); i++) {
      removed += domains.valueCount() - domains.domainSize(i);
    }
    return removed;
  }
}
