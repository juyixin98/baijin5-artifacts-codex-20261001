package com.example.coloring.graph;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.BitSet;
import java.util.Collections;
import java.util.List;

/**
 * Immutable finite undirected simple graph.
 *
 * <p>Vertices are indexed {@code 0 .. order()-1}. The graph stores no
 * self-loops and at most one undirected edge per unordered pair. Edges are
 * added through {@link Builder}, which rejects every contract violation with
 * a categorized {@link GraphContractException}.</p>
 */
public final class Graph {

    private final int order;
    private final List<BitSet> adjacency;
    private final List<int[]> edges;
    private final List<String> labels;

    private Graph(int order, List<BitSet> adjacency, List<int[]> edges, List<String> labels) {
        this.order = order;
        this.adjacency = adjacency;
        this.edges = edges;
        this.labels = labels;
    }

    public int order() {
        return order;
    }

    public int edgeCount() {
        return edges.size();
    }

    public boolean hasEdge(int u, int v) {
        checkVertex(u);
        checkVertex(v);
        return adjacency.get(u).get(v);
    }

    /** Neighbours of {@code v} in ascending index order. */
    public List<Integer> neighbors(int v) {
        checkVertex(v);
        List<Integer> result = new ArrayList<>();
        BitSet row = adjacency.get(v);
        for (int w = 0; w < order; w++) {
            if (row.get(w)) {
                result.add(w);
            }
        }
        return result;
    }

    public int degree(int v) {
        checkVertex(v);
        return adjacency.get(v).cardinality();
    }

    public BitSet neighborSet(int v) {
        return adjacency.get(v);
    }

    public List<int[]> edges() {
        List<int[]> copy = new ArrayList<>(edges.size());
        for (int[] edge : edges) {
            copy.add(edge.clone());
        }
        return copy;
    }

    public String label(int v) {
        checkVertex(v);
        return labels.get(v);
    }

    public List<String> labels() {
        return Collections.unmodifiableList(labels);
    }

    /** Connected components; each component is a sorted list of vertex indices. */
    public List<List<Integer>> components() {
        boolean[] seen = new boolean[order];
        List<List<Integer>> result = new ArrayList<>();
        for (int start = 0; start < order; start++) {
            if (seen[start]) {
                continue;
            }
            List<Integer> component = new ArrayList<>();
            int[] stack = new int[order];
            int top = 0;
            stack[top++] = start;
            seen[start] = true;
            while (top > 0) {
                int v = stack[--top];
                component.add(v);
                BitSet row = adjacency.get(v);
                for (int w = 0; w < order; w++) {
                    if (row.get(w) && !seen[w]) {
                        seen[w] = true;
                        stack[top++] = w;
                    }
                }
            }
            Collections.sort(component);
            result.add(component);
        }
        return result;
    }

    /** Induced sub-graph on the given vertices; labels and indices are restricted. */
    public Graph inducedBy(List<Integer> vertices) {
        int[] localIndex = new int[order];
        Arrays.fill(localIndex, -1);
        Builder builder = new Builder(vertices.size());
        for (int i = 0; i < vertices.size(); i++) {
            int v = vertices.get(i);
            checkVertex(v);
            if (localIndex[v] != -1) {
                throw new IllegalArgumentException("duplicate vertex in sub-graph selection: " + v);
            }
            localIndex[v] = i;
            builder.label(i, labels.get(v));
        }
        for (int i = 0; i < vertices.size(); i++) {
            int u = vertices.get(i);
            BitSet row = adjacency.get(u);
            for (int j = i + 1; j < vertices.size(); j++) {
                int v = vertices.get(j);
                if (row.get(v)) {
                    builder.edge(i, j);
                }
            }
        }
        return builder.build();
    }

    private void checkVertex(int v) {
        if (v < 0 || v >= order) {
            throw new GraphContractException(
                    GraphContractException.Violation.VERTEX_OUT_OF_RANGE,
                    "vertex index " + v + " is outside [0, " + order + ")");
        }
    }

    public static Builder builder(int order) {
        return new Builder(order);
    }

    /** Mutable construction buffer; edges are validated and de-duplicated. */
    public static final class Builder {
        private final int order;
        private final List<BitSet> adjacency;
        private final List<String> labels;
        private final List<int[]> edges = new ArrayList<>();

        public Builder(int order) {
            if (order < 0) {
                throw new GraphContractException(
                        GraphContractException.Violation.NEGATIVE_VERTEX_COUNT,
                        "vertex count must be non-negative, got " + order);
            }
            this.order = order;
            this.adjacency = new ArrayList<>(order);
            this.labels = new ArrayList<>(order);
            for (int v = 0; v < order; v++) {
                adjacency.add(new BitSet(order));
                labels.add("v" + v);
            }
        }

        public Builder edge(int u, int v) {
            if (u == v) {
                throw new GraphContractException(
                        GraphContractException.Violation.SELF_LOOP,
                        "self-loop forbidden on vertex " + u);
            }
            check(u);
            check(v);
            if (adjacency.get(u).get(v)) {
                return this; // simple graph: duplicate edge is a no-op
            }
            adjacency.get(u).set(v);
            adjacency.get(v).set(u);
            edges.add(new int[]{u, v});
            return this;
        }

        public Builder label(int v, String label) {
            check(v);
            if (label == null) {
                throw new GraphContractException(
                        GraphContractException.Violation.NULL_LABEL,
                        "label for vertex " + v + " is null");
            }
            labels.set(v, label);
            return this;
        }

        public Graph build() {
            List<BitSet> rows = new ArrayList<>(order);
            for (BitSet row : adjacency) {
                rows.add((BitSet) row.clone());
            }
            List<int[]> storedEdges = new ArrayList<>(edges.size());
            for (int[] edge : edges) {
                storedEdges.add(edge.clone());
            }
            return new Graph(order, rows, storedEdges, List.copyOf(labels));
        }

        private void check(int v) {
            if (v < 0 || v >= order) {
                throw new GraphContractException(
                        GraphContractException.Violation.VERTEX_OUT_OF_RANGE,
                        "vertex index " + v + " is outside [0, " + order + ")");
            }
        }
    }

    @Override
    public String toString() {
        return "Graph(n=" + order + ", m=" + edges.size() + ")";
    }
}
