package com.local.mwis.graph;

import java.util.ArrayList;
import java.util.BitSet;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Immutable undirected simple graph on vertices 0..n-1.
 * This is the contract every other module relies on; it carries no weights.
 */
public final class Graph {

    private final int vertexCount;
    private final Set<Edge> edges;
    private final BitSet[] adjacency;

    private Graph(int vertexCount, Set<Edge> edges) {
        this.vertexCount = vertexCount;
        this.edges = Collections.unmodifiableSet(new LinkedHashSet<>(edges));
        this.adjacency = new BitSet[vertexCount];
        for (int i = 0; i < vertexCount; i++) {
            adjacency[i] = new BitSet(vertexCount);
        }
        for (Edge e : edges) {
            adjacency[e.u()].set(e.v());
            adjacency[e.v()].set(e.u());
        }
    }

    /**
     * Builds a graph, rejecting malformed input.
     *
     * @throws IllegalArgumentException on negative vertex count, out-of-range endpoint or self loop
     */
    public static Graph of(int vertexCount, List<Edge> edgeList) {
        if (vertexCount < 0) {
            throw new IllegalArgumentException("vertexCount must be >= 0, got " + vertexCount);
        }
        Set<Edge> clean = new LinkedHashSet<>();
        for (Edge e : edgeList) {
            if (e.u() < 0 || e.v() >= vertexCount) {
                throw new IllegalArgumentException(
                        "edge " + e + " out of range for vertexCount=" + vertexCount);
            }
            clean.add(e); // set semantics: parallel edges collapse
        }
        return new Graph(vertexCount, clean);
    }

    public int vertexCount() {
        return vertexCount;
    }

    public boolean hasEdge(int u, int v) {
        if (u < 0 || v < 0 || u >= vertexCount || v >= vertexCount) {
            return false;
        }
        return adjacency[u].get(v);
    }

    /** Neighbors of v as an unmodifiable snapshot, ascending order. */
    public Set<Integer> neighbors(int v) {
        Set<Integer> out = new LinkedHashSet<>();
        for (int w = adjacency[v].nextSetBit(0); w >= 0; w = adjacency[v].nextSetBit(w + 1)) {
            out.add(w);
        }
        return Collections.unmodifiableSet(out);
    }

    public Set<Edge> edges() {
        return edges;
    }

    public int edgeCount() {
        return edges.size();
    }

    /** Adjacency test helper used by DP hot paths: true when u and v are adjacent. */
    public boolean adjacent(int u, int v) {
        return adjacency[u].get(v);
    }

    /** BitSet view (copy) of the neighborhood, for bitmask intersections. */
    public BitSet neighborMask(int v) {
        return (BitSet) adjacency[v].clone();
    }

    /** All maximal... no: all vertices as an ascending list, convenience for tests. */
    public List<Integer> vertices() {
        List<Integer> out = new ArrayList<>(vertexCount);
        for (int i = 0; i < vertexCount; i++) {
            out.add(i);
        }
        return out;
    }
}
