package dev.local.seqprop.prop;

import java.util.ArrayList;
import java.util.List;
import dev.local.seqprop.diag.DiagnosticEvent;
import dev.local.seqprop.diag.Diag;
import dev.local.seqprop.diag.FailureCategory;
import dev.local.seqprop.diag.Redactor;
import dev.local.seqprop.model.SequenceConstraint;

/**
 * Propagator for one SEQUENCE constraint.
 *
 * <p>Pruning is performed against the prefix-sum difference graph, so every
 * removed value is justified by a feasibility check of the full overlapping
 * window system (generalized arc consistency for a single SEQUENCE), not by
 * per-window counting in isolation. Window min/max tallies are used only as
 * an explicit, cheap rejection with a precise failure category.
 */
public final class SequencePropagator {

  /** Internal outcome used by the multi-constraint engine. */
  enum Outcome {
    CHANGED,
    NO_CHANGE,
    FAILURE
  }

  static final class Step {
    Outcome outcome;
    FailureCategory category = FailureCategory.NONE;
    int[] failedWindow;

    static Step changed() {
      Step s = new Step();
      s.outcome = Outcome.CHANGED;
      return s;
    }

    static Step noChange() {
      Step s = new Step();
      s.outcome = Outcome.NO_CHANGE;
      return s;
    }

    static Step failure(FailureCategory category, int[] window) {
      Step s = new Step();
      s.outcome = Outcome.FAILURE;
      s.category = category;
      s.failedWindow = window;
      return s;
    }
  }

  /**
   * Cheap, explicit per-window min/max tally with exact failure categories.
   * Returns null when all windows pass the local bounds test.
   */
  public static int[] checkWindowTallies(SequenceConstraint c, DomainState domains,
                                         Redactor redactor, Diag diag) {
    for (int s : c.windowStarts()) {
      int e = c.windowEnd(s);
      int min = 0;
      int max = 0;
      for (int i = s; i <= e; i++) {
        boolean hasTarget = false;
        boolean hasOther = false;
        var d = domains.domain(i);
        for (int vi = d.nextSetBit(0); vi >= 0; vi = d.nextSetBit(vi + 1)) {
          if (c.isTarget(domains.valueAt(vi))) {
            hasTarget = true;
          } else {
            hasOther = true;
          }
        }
        if (d.isEmpty()) {
          return new int[] {s, e};
        }
        if (hasTarget) {
          max++;
          if (!hasOther) {
            min++;
          }
        }
      }
      int len = e - s + 1;
      if (max < c.lo()) {
        if (diag != null) {
          diag.add(DiagnosticEvent.Kind.WINDOW_REJECTED_LOWER, c.id(), s,
              "window [" + s + "," + e + "] len=" + len + " maxTarget=" + max
                  + " < lo=" + c.lo());
        }
        return new int[] {s, e};
      }
      if (min > c.hi()) {
        if (diag != null) {
          diag.add(DiagnosticEvent.Kind.WINDOW_REJECTED_UPPER, c.id(), s,
              "window [" + s + "," + e + "] len=" + len + " minTarget=" + min
                  + " > hi=" + c.hi());
        }
        return new int[] {s, e};
      }
      if (diag != null) {
        diag.add(DiagnosticEvent.Kind.WINDOW_ACCEPTED, c.id(), s,
            "window [" + s + "," + e + "] len=" + len
                + " targetCount in [" + min + "," + max + "] bounds [" + c.lo() + "," + c.hi()
                + "]");
      }
    }
    return null;
  }

  /**
   * Run this propagator until it reaches its own fixpoint. On failure the
   * returned step carries the precise category; on success it reports whether
   * any domain changed.
   */
  Step propagate(SequenceConstraint c, DomainState domains, Redactor redactor, Diag diag) {
    boolean anyChange = false;
    while (true) {
      int[] failed = checkWindowTallies(c, domains, redactor, diag);
      if (failed != null) {
        FailureCategory category = classifyTallyFailure(c, domains, failed);
        return Step.failure(category, failed);
      }

      DifferenceGraph graph = new DifferenceGraph(c, domains);
      if (!graph.feasible()) {
        diag.add(DiagnosticEvent.Kind.JOINT_CONTRADICTION, c.id(), null,
            "prefix-sum difference graph contains a negative cycle; windows are "
                + "locally countable but overlap counts are jointly impossible");
        return Step.failure(FailureCategory.JOINT_OVERLAP_CONTRADICTION, null);
      }

      boolean roundChanged = prune(c, domains, graph, redactor, diag);
      if (!roundChanged) {
        return anyChange ? Step.changed() : Step.noChange();
      }
      anyChange = true;
    }
  }

  private FailureCategory classifyTallyFailure(SequenceConstraint c, DomainState domains,
                                               int[] window) {
    int s = window[0];
    int e = window[1];
    for (int i = s; i <= e; i++) {
      if (domains.isEmpty(i)) {
        return FailureCategory.EMPTY_DOMAIN;
      }
    }
    int min = 0;
    int max = 0;
    for (int i = s; i <= e; i++) {
      var d = domains.domain(i);
      boolean hasTarget = false;
      boolean hasOther = false;
      for (int vi = d.nextSetBit(0); vi >= 0; vi = d.nextSetBit(vi + 1)) {
        if (c.isTarget(domains.valueAt(vi))) {
          hasTarget = true;
        } else {
          hasOther = true;
        }
      }
      if (hasTarget) {
        max++;
        if (!hasOther) {
          min++;
        }
      }
    }
    if (max < c.lo()) {
      return FailureCategory.WINDOW_LOWER_VIOLATION;
    }
    if (min > c.hi()) {
      return FailureCategory.WINDOW_UPPER_VIOLATION;
    }
    return FailureCategory.EMPTY_DOMAIN;
  }

  private boolean prune(SequenceConstraint c, DomainState domains, DifferenceGraph graph,
                        Redactor redactor, Diag diag) {
    boolean changed = false;
    List<Integer> targetIndices = new ArrayList<>();
    List<Integer> otherIndices = new ArrayList<>();
    for (int i = c.from(); i <= c.to(); i++) {
      var d = domains.domain(i);
      if (d.cardinality() <= 1) {
        continue;
      }
      targetIndices.clear();
      otherIndices.clear();
      for (int vi = d.nextSetBit(0); vi >= 0; vi = d.nextSetBit(vi + 1)) {
        if (c.isTarget(domains.valueAt(vi))) {
          targetIndices.add(vi);
        } else {
          otherIndices.add(vi);
        }
      }
      if (!targetIndices.isEmpty() && !graph.feasibleWith(i, 1)) {
        for (int vi : targetIndices) {
          domains.removeValue(i, vi);
          changed = true;
          diag.add(DiagnosticEvent.Kind.VALUE_REMOVED, c.id(), i,
              "remove target value " + redactor.renderValue(domains.valueAt(vi))
                  + " from " + redactor.renderVar(i) + ": no support in joint prefix-sum graph");
        }
      }
      if (!otherIndices.isEmpty() && !graph.feasibleWith(i, 0)) {
        for (int vi : otherIndices) {
          domains.removeValue(i, vi);
          changed = true;
          diag.add(DiagnosticEvent.Kind.VALUE_REMOVED, c.id(), i,
              "remove non-target value " + redactor.renderValue(domains.valueAt(vi))
                  + " from " + redactor.renderVar(i) + ": no support in joint prefix-sum graph");
        }
      }
    }
    return changed;
  }
}
