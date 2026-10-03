package dev.local.seqprop.model;

import java.util.Arrays;
import java.util.Collections;
import java.util.Objects;
import java.util.SortedSet;
import java.util.TreeSet;

/**
 * A single SEQUENCE-style constraint: for every applicable window inside the
 * variable index range {@code [from, to]}, the number of positions whose
 * value belongs to {@code targetValues} must be in {@code [lo, hi]}.
 */
public final class SequenceConstraint {

  private final String id;
  private final int from;
  private final int to;
  private final int windowLength;
  private final int lo;
  private final int hi;
  private final SortedSet<Integer> targetValues;
  private final EndMode endMode;

  public SequenceConstraint(String id, int from, int to, int windowLength, int lo, int hi,
                            SortedSet<Integer> targetValues, EndMode endMode) {
    this.id = Objects.requireNonNull(id, "id");
    if (from < 0 || to < from) {
      throw new IllegalArgumentException("bad variable range [" + from + "," + to + "]");
    }
    if (windowLength <= 0) {
      throw new IllegalArgumentException("windowLength must be positive: " + windowLength);
    }
    if (lo < 0 || hi < lo) {
      throw new IllegalArgumentException("bad bounds [" + lo + "," + hi + "]");
    }
    if (hi > windowLength) {
      throw new IllegalArgumentException("hi " + hi + " exceeds windowLength " + windowLength);
    }
    if (targetValues == null || targetValues.isEmpty()) {
      throw new IllegalArgumentException("targetValues must be non-empty");
    }
    this.from = from;
    this.to = to;
    this.windowLength = windowLength;
    this.lo = lo;
    this.hi = hi;
    this.targetValues = Collections.unmodifiableSortedSet(new TreeSet<>(targetValues));
    this.endMode = Objects.requireNonNull(endMode, "endMode");
  }

  public String id() { return id; }
  public int from() { return from; }
  public int to() { return to; }
  public int windowLength() { return windowLength; }
  public int lo() { return lo; }
  public int hi() { return hi; }
  public SortedSet<Integer> targetValues() { return targetValues; }
  public EndMode endMode() { return endMode; }

  /** Inclusive start indices of every window emitted by this constraint. */
  public int[] windowStarts() {
    int last = endMode == EndMode.TRUNCATED_ENDS ? to : to - windowLength + 1;
    if (last < from) {
      return new int[0];
    }
    int[] starts = new int[last - from + 1];
    for (int i = 0; i < starts.length; i++) {
      starts[i] = from + i;
    }
    return starts;
  }

  /** Inclusive end index of the window starting at {@code s}, clipped to the range. */
  public int windowEnd(int s) {
    return Math.min(to, s + windowLength - 1);
  }

  public int windowLengthAt(int s) {
    return windowEnd(s) - s + 1;
  }

  public boolean isTarget(int value) {
    return targetValues.contains(value);
  }

  @Override
  public String toString() {
    return "SequenceConstraint{" + id + " range=[" + from + "," + to + "] w=" + windowLength
        + " [" + lo + "," + hi + "] S=" + Arrays.toString(targetValues.toArray())
        + " ends=" + endMode + "}";
  }
}
