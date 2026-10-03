package dev.local.seqprop.model;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;

/**
 * Finite-domain CSP model: {@code n} variables indexed {@code 0..n-1},
 * each taking values in a shared finite universe, plus an ordered list of
 * SEQUENCE constraints. Constraint ranges must stay inside the model.
 */
public final class CspModel {

  private final int n;
  private final List<Integer> universe;
  private final List<SequenceConstraint> constraints = new ArrayList<>();

  public CspModel(int n, List<Integer> universe) {
    if (n <= 0) {
      throw new IllegalArgumentException("n must be positive: " + n);
    }
    if (universe == null || universe.isEmpty()) {
      throw new IllegalArgumentException("universe must be non-empty");
    }
    if (universe.stream().distinct().count() != universe.size()) {
      throw new IllegalArgumentException("universe contains duplicate values");
    }
    this.n = n;
    this.universe = List.copyOf(universe);
  }

  public int n() { return n; }
  public List<Integer> universe() { return universe; }
  public List<SequenceConstraint> constraints() { return Collections.unmodifiableList(constraints); }

  public CspModel addConstraint(SequenceConstraint constraint) {
    Objects.requireNonNull(constraint, "constraint");
    if (constraint.to() >= n) {
      throw new IllegalArgumentException(
          "constraint " + constraint.id() + " range exceeds model size " + n);
    }
    for (Integer v : constraint.targetValues()) {
      if (!universe.contains(v)) {
        throw new IllegalArgumentException(
            "constraint " + constraint.id() + " references unknown value " + v);
      }
    }
    constraints.add(constraint);
    return this;
  }
}
