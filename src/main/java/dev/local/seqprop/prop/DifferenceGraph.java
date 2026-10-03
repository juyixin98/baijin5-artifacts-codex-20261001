package dev.local.seqprop.prop;

import java.util.ArrayList;
import java.util.List;
import dev.local.seqprop.model.SequenceConstraint;

/**
 * Prefix-sum difference-constraint encoding of one SEQUENCE constraint.
 *
 * <p>For positions {@code a..b} of the constraint's range, binary counters
 * {@code t_i} say whether position {@code i} takes a target value, and
 * prefix sums {@code P_k = sum_{i=a}^{k-1} t_i} ({@code P_a = 0}) yield:
 * <ul>
 *   <li>{@code 0 <= P_{k+1} - P_k <= 1}</li>
 *   <li>window [s,e]: {@code lo <= P_{e+1} - P_s <= hi}</li>
 *   <li>domains force edges {@code P_{i+1}-P_i = 0} (no target value) or
 *       {@code = 1} (only target values), or leave the variable free when the
 *       domain contains both kinds of values</li>
 * </ul>
 *
 * <p>The counter system is feasible iff the difference graph has no negative
 * cycle. Crucially, all windows share the same prefix variables, so the
 * feasibility check propagates window overlap jointly rather than testing and
 * counting windows independently.
 */
final class DifferenceGraph {

  /** Directed edge u -> v with weight w meaning v - u <= w. */
  record Edge(int u, int v, int w) {}

  static final class NegativeCycleException extends RuntimeException {
    NegativeCycleException(String message) {
      super(message);
    }
  }

  private final SequenceConstraint constraint;
  private final DomainState domains;

  DifferenceGraph(SequenceConstraint constraint, DomainState domains) {
    this.constraint = constraint;
    this.domains = domains;
  }

  private int nodeCount() {
    return constraint.to() - constraint.from() + 2;
  }

  private int nodeOf(int prefixIndex) {
    return prefixIndex - constraint.from();
  }

  private List<Edge> buildEdges(int forcePos, int forceClass) {
    int a = constraint.from();
    int b = constraint.to();
    List<Edge> edges = new ArrayList<>();
    for (int i = a; i <= b; i++) {
      int p = nodeOf(i);
      int q = nodeOf(i + 1);
      int cls = i == forcePos ? forceClass : classify(i);
      if (cls == -2) {
        edges.add(new Edge(p, p, -1));
      } else if (cls == 0) {
        // t_i = 0  => P_{i+1}-P_i <= 0 and P_i-P_{i+1} <= 0
        edges.add(new Edge(p, q, 0));
        edges.add(new Edge(q, p, 0));
      } else if (cls == 1) {
        // t_i = 1
        edges.add(new Edge(p, q, 1));
        edges.add(new Edge(q, p, -1));
      } else {
        // t_i free in {0,1}
        edges.add(new Edge(p, q, 1));
        edges.add(new Edge(q, p, 0));
      }
    }
    for (int s : constraint.windowStarts()) {
      int e = constraint.windowEnd(s);
      edges.add(new Edge(nodeOf(s), nodeOf(e + 1), constraint.hi()));
      edges.add(new Edge(nodeOf(e + 1), nodeOf(s), -constraint.lo()));
    }
    return edges;
  }

  /**
   * @return 0 if the position cannot take target values, 1 if it must,
   *     -1 if its current domain leaves the counter free.
   */
  private int classify(int var) {
    boolean hasTarget = false;
    boolean hasOther = false;
    var bitSet = domains.domain(var);
    for (int vi = bitSet.nextSetBit(0); vi >= 0; vi = bitSet.nextSetBit(vi + 1)) {
      if (constraint.isTarget(domains.valueAt(vi))) {
        hasTarget = true;
      } else {
        hasOther = true;
      }
    }
    if (!hasTarget && !hasOther) {
      // Empty domain: encode as infeasibility with a self negative edge.
      return -2;
    }
    if (hasTarget && hasOther) {
      return -1;
    }
    return hasTarget ? 1 : 0;
  }

  /** Feasibility under current domains. */
  boolean feasible() {
    return feasible(-1, 0);
  }

  /**
   * Feasibility when position {@code forcePos} is hypothetically restricted:
   * {@code forceClass} 0 means "not a target value", 1 means "a target value".
   */
  boolean feasibleWith(int forcePos, int forceClass) {
    return feasible(forcePos, forceClass);
  }

  private boolean feasible(int forcePos, int forceClass) {
    int vCount = nodeCount();
    List<Edge> edges = buildEdges(forcePos, forceClass);
    // Empty domains were marked with a bogus class -2 handled below.
    for (Edge edge : edges) {
      if (edge.u == edge.v && edge.w < 0) {
        return false;
      }
    }
    int[] dist = new int[vCount];
    // Bellman-Ford with a virtual source connected to every node at weight 0:
    // every node starts at distance 0 and at most vCount-1 relaxation rounds
    // are needed. A further successful relaxation proves a negative cycle.
    for (int iteration = 0; iteration < vCount - 1; iteration++) {
      boolean changed = false;
      for (Edge edge : edges) {
        int nd = dist[edge.u] + edge.w;
        if (nd < dist[edge.v]) {
          dist[edge.v] = nd;
          changed = true;
        }
      }
      if (!changed) {
        return true;
      }
    }
    for (Edge edge : edges) {
      if (dist[edge.u] + edge.w < dist[edge.v]) {
        return false;
      }
    }
    return true;
  }
}
