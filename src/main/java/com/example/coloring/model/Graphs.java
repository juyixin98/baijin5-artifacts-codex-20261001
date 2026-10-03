package com.example.coloring.model;

/** Factory for named families of simple graphs used by fixtures and the demo. */
public final class Graphs {

    private Graphs() {
    }

    /** Graph with {@code n} vertices and no edges (n >= 0). */
    public static SimpleGraph empty(int n) {
        return new SimpleGraph.Builder(n).build();
    }

    /** Complete graph K_n. */
    public static SimpleGraph complete(int n) {
        SimpleGraph.Builder b = new SimpleGraph.Builder(n);
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                b.addEdge(u, v);
            }
        }
        return b.build();
    }

    /** Cycle C_n, requires n >= 3. */
    public static SimpleGraph cycle(int n) {
        if (n < 3) {
            throw new IllegalArgumentException("cycle requires n >= 3, got " + n);
        }
        SimpleGraph.Builder b = new SimpleGraph.Builder(n);
        for (int i = 0; i < n; i++) {
            b.addEdge(i, (i + 1) % n);
        }
        return b.build();
    }

    /** Path P_n, requires n >= 1. */
    public static SimpleGraph path(int n) {
        if (n < 1) {
            throw new IllegalArgumentException("path requires n >= 1, got " + n);
        }
        SimpleGraph.Builder b = new SimpleGraph.Builder(n);
        for (int i = 0; i + 1 < n; i++) {
            b.addEdge(i, i + 1);
        }
        return b.build();
    }

    /** Wheel W_n: rim cycle on n-1 vertices plus hub 0 joined to every rim vertex. */
    public static SimpleGraph wheel(int n) {
        if (n < 4) {
            throw new IllegalArgumentException("wheel requires n >= 4, got " + n);
        }
        int rim = n - 1;
        SimpleGraph.Builder b = new SimpleGraph.Builder(n);
        for (int i = 0; i < rim; i++) {
            int a = i + 1;
            int c = ((i + 1) % rim) + 1;
            b.addEdge(a, c);
            b.addEdge(0, a);
        }
        return b.build();
    }

    /**
     * Disjoint union G1 + G2: vertices of {@code g2} are renumbered after those of
     * {@code g1}. The result has no edges between the two components.
     */
    public static SimpleGraph disjointUnion(Graph g1, Graph g2) {
        int n1 = g1.n();
        int n2 = g2.n();
        SimpleGraph.Builder b = new SimpleGraph.Builder(n1 + n2);
        for (int u = 0; u < n1; u++) {
            for (int v : g1.neighbors(u)) {
                if (u < v) {
                    b.addEdge(u, v);
                }
            }
        }
        for (int u = 0; u < n2; u++) {
            for (int v : g2.neighbors(u)) {
                if (u < v) {
                    b.addEdge(u + n1, v + n1);
                }
            }
        }
        return b.build();
    }
}
