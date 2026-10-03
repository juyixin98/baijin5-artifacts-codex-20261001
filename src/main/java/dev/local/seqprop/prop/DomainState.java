package dev.local.seqprop.prop;

import java.util.Arrays;
import java.util.BitSet;
import java.util.List;

/**
 * Backtrackable finite domains over a shared integer universe.
 * Domains are stored as bitsets of universe indices; {@link #mark()} /
 * {@link #undo(long)} provide a copy-on-write trailing point for search.
 */
public final class DomainState {

  private final List<Integer> universe;
  private final int valueCount;
  private final BitSet[] domains;

  public DomainState(List<Integer> universe, int n) {
    this.universe = List.copyOf(universe);
    this.valueCount = this.universe.size();
    this.domains = new BitSet[n];
    BitSet all = new BitSet(valueCount);
    all.set(0, valueCount);
    for (int i = 0; i < n; i++) {
      domains[i] = (BitSet) all.clone();
    }
  }

  private DomainState(DomainState other) {
    this.universe = other.universe;
    this.valueCount = other.valueCount;
    this.domains = new BitSet[other.domains.length];
    for (int i = 0; i < other.domains.length; i++) {
      this.domains[i] = (BitSet) other.domains[i].clone();
    }
  }

  public int size() { return domains.length; }
  public List<Integer> universe() { return universe; }
  public int valueCount() { return valueCount; }

  public int indexOfValue(int value) {
    int idx = universe.indexOf(value);
    if (idx < 0) {
      throw new IllegalArgumentException("value not in universe: " + value);
    }
    return idx;
  }

  public int valueAt(int index) { return universe.get(index); }

  public BitSet domain(int var) { return domains[var]; }

  public int domainSize(int var) { return domains[var].cardinality(); }

  public boolean contains(int var, int valueIndex) {
    return domains[var].get(valueIndex);
  }

  public boolean containsValue(int var, int value) {
    int idx = universe.indexOf(value);
    return idx >= 0 && domains[var].get(idx);
  }

  public boolean isSingleton(int var) { return domains[var].cardinality() == 1; }

  public int singletonIndex(int var) {
    if (!isSingleton(var)) {
      throw new IllegalStateException("variable " + var + " is not singleton");
    }
    return domains[var].nextSetBit(0);
  }

  /** Remove a value; returns true when the domain actually changed. */
  public boolean removeValue(int var, int valueIndex) {
    if (!domains[var].get(valueIndex)) {
      return false;
    }
    domains[var].clear(valueIndex);
    return true;
  }

  public boolean isEmpty(int var) { return domains[var].isEmpty(); }

  /** Assign a variable to a single value index; returns the removed count. */
  public int assign(int var, int valueIndex) {
    BitSet d = domains[var];
    int removed = d.cardinality() - (d.get(valueIndex) ? 1 : 0);
    d.clear();
    d.set(valueIndex);
    return removed;
  }

  /** Full-domain snapshot used as a search trail point. */
  public DomainState snapshot() {
    return new DomainState(this);
  }

  public void restoreFrom(DomainState snapshot) {
    if (snapshot.domains.length != domains.length) {
      throw new IllegalArgumentException("snapshot size mismatch");
    }
    for (int i = 0; i < domains.length; i++) {
      domains[i].clear();
      domains[i].or(snapshot.domains[i]);
    }
  }

  @Override
  public String toString() {
    return "DomainState" + Arrays.toString(domains);
  }
}
