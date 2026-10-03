package com.example.coloring.model;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Immutable finite simple undirected graph backed by per-vertex adjacency lists. */
public final class SimpleGraph implements Graph {

    private final int n;
    private final List<List<Integer>> adjacency;
    private final int edgeCount;

    private SimpleGraph(int n, List<List<Integer>> adjacency, int edgeCount) {
        this.n = n;
        this.adjacency = adjacency;
        this.edgeCount = edgeCount;
    }

    @Override
    public int n() {
        return n;
    }

    @Override
    public int edgeCount() {
        return edgeCount;
    }

    @Override
    public boolean adjacent(int u, int v) {
        if (u < 0 || v < 0 || u >= n || v >= n || u == v) {
            return false;
        }
        return Collections.binarySearch(adjacency.get(u), v) >= 0;
    }

    @Override
    public List<Integer> neighbors(int v) {
        if (v < 0 || v >= n) {
            throw new IndexOutOfBoundsException("vertex " + v + " out of range 0.." + (n - 1));
        }
        return adjacency.get(v);
    }

    /** Mutable builder; {@link #build()} freezes the graph and returns an immutable copy. */
    public static final class Builder {

        private final int n;
        private final List<java.util.BitSet> sets = new ArrayList<>();
        private int edgeCount;

        public Builder(int n) {
            if (n < 0) {
                throw new IllegalArgumentException("vertex count must be non-negative: " + n);
            }
            this.n = n;
            for (int i = 0; i < n; i++) {
                sets.add(new java.util.BitSet(n));
            }
        }

        /** Adds the undirected edge {@code {u,v}}. Adding an existing edge is idempotent. */
        public Builder addEdge(int u, int v) {
            if (u < 0 || v < 0 || u >= n || v >= n) {
                throw new IndexOutOfBoundsException(
                        "edge (" + u + "," + v + ") out of range 0.." + (n - 1));
            }
            if (u == v) {
                throw new IllegalArgumentException("simple graphs cannot contain a loop at " + u);
            }
            if (!sets.get(u).get(v)) {
                sets.get(u).set(v);
                sets.get(v).set(u);
                edgeCount++;
            }
            return this;
        }

        public SimpleGraph build() {
            List<List<Integer>> adjacency = new ArrayList<>(n);
            for (int i = 0; i < n; i++) {
                List<Integer> list = new ArrayList<>(sets.get(i).cardinality());
                for (int j = sets.get(i).nextSetBit(0); j >= 0; j = sets.get(i).nextSetBit(j + 1)) {
                    list.add(j);
                }
                adjacency.add(Collections.unmodifiableList(list));
            }
            return new SimpleGraph(n, Collections.unmodifiableList(adjacency), edgeCount);
        }
    }
}
