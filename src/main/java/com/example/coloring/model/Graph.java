package com.example.coloring.model;

import java.util.List;

/**
 * Read contract for a finite simple undirected graph.
 *
 * <p>Vertices are identified by contiguous integers {@code 0 .. n-1}. A simple
 * graph has no loops and at most one undirected edge between any two distinct
 * vertices. Implementations are immutable.
 */
public interface Graph {

    /** Number of vertices (non-negative). */
    int n();

    /** Number of undirected edges (non-negative). */
    int edgeCount();

    /** Returns true if {@code {u,v}} is an edge. Loops and out of range ids return false. */
    boolean adjacent(int u, int v);

    /** Sorted, unmodifiable list of neighbours of vertex {@code v}. */
    List<Integer> neighbors(int v);
}
