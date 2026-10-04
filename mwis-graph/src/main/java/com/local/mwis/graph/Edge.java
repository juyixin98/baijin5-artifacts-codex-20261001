package com.local.mwis.graph;

/** Undirected edge with normalized endpoint order (u < v). */
public record Edge(int u, int v) {

    public Edge {
        if (u == v) {
            throw new IllegalArgumentException("self loop not allowed: " + u);
        }
        if (u > v) {
            // normalize
            int tmp = u;
            u = v;
            v = tmp;
        }
    }

    public static Edge of(int a, int b) {
        return new Edge(Math.min(a, b), Math.max(a, b));
    }

    @Override
    public String toString() {
        return "(" + u + "," + v + ")";
    }
}
