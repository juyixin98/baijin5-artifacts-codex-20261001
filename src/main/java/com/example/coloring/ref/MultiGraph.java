package com.example.coloring.ref;

import com.example.coloring.graph.Graph;

import java.util.BitSet;

/**
 * Adjacency-matrix multigraph for deletion-contraction. Parallel edges are
 * de-duplicated; contraction merges an endpoint and removes its row/column by
 * swapping it with the last live vertex. {@code dimension} is the fixed
 * allocated matrix size; {@code vertices} is the current live vertex count.
 */
final class MultiGraph {

    private final int dimension;
    int vertices;
    private final BitSet matrix;

    private MultiGraph(int dimension, int vertices, BitSet matrix) {
        this.dimension = dimension;
        this.vertices = vertices;
        this.matrix = matrix;
    }

    static MultiGraph from(Graph graph) {
        int n = graph.order();
        BitSet bits = new BitSet(n * n);
        for (int[] edge : graph.edges()) {
            bits.set(edge[0] * n + edge[1]);
            bits.set(edge[1] * n + edge[0]);
        }
        return new MultiGraph(n, n, bits);
    }

    MultiGraph copy() {
        return new MultiGraph(dimension, vertices, (BitSet) matrix.clone());
    }

    int[] firstEdge() {
        for (int u = 0; u < vertices; u++) {
            for (int v = u + 1; v < vertices; v++) {
                if (matrix.get(u * dimension + v)) {
                    return new int[]{u, v};
                }
            }
        }
        return null;
    }

    void deleteEdge(int u, int v) {
        matrix.clear(u * dimension + v);
        matrix.clear(v * dimension + u);
    }

    void contract(int u, int v) {
        for (int w = 0; w < vertices; w++) {
            if (w == u || w == v) {
                continue;
            }
            if (matrix.get(v * dimension + w)) {
                matrix.set(u * dimension + w);
                matrix.set(w * dimension + u);
            }
        }
        swapVertex(v, vertices - 1);
        int last = vertices - 1;
        for (int w = 0; w < dimension; w++) {
            matrix.clear(last * dimension + w);
            matrix.clear(w * dimension + last);
        }
        matrix.clear(u * dimension + u);
        vertices--;
    }

    private void swapVertex(int a, int b) {
        if (a == b) {
            return;
        }
        for (int w = 0; w < dimension; w++) {
            boolean at = matrix.get(a * dimension + w);
            boolean bt = matrix.get(b * dimension + w);
            matrix.set(a * dimension + w, bt);
            matrix.set(b * dimension + w, at);
        }
        for (int w = 0; w < dimension; w++) {
            boolean at = matrix.get(w * dimension + a);
            boolean bt = matrix.get(w * dimension + b);
            matrix.set(w * dimension + a, bt);
            matrix.set(w * dimension + b, at);
        }
    }

    String fingerprint() {
        StringBuilder sb = new StringBuilder();
        sb.append(vertices).append(';');
        for (int u = 0; u < vertices; u++) {
            for (int v = u + 1; v < vertices; v++) {
                if (matrix.get(u * dimension + v)) {
                    sb.append(u).append('-').append(v).append(',');
                }
            }
        }
        return sb.toString();
    }
}
