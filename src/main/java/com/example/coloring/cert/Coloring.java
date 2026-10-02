package com.example.coloring.cert;

import com.example.coloring.graph.Graph;

import java.util.Arrays;

/**
 * A concrete vertex coloring: {@code color[v]} in {@code 0 .. k-1}.
 *
 * <p>The certificate can be re-checked against any graph: {@link #isProper(Graph)}
 * returns true exactly when no adjacent pair shares a color.</p>
 */
public final class Coloring {

    private final int[] colors;
    private final int colorCount;

    public Coloring(int[] colors, int colorCount) {
        if (colorCount < 0) {
            throw new IllegalArgumentException("colorCount must be non-negative");
        }
        for (int color : colors) {
            if (color < 0 || color >= colorCount) {
                throw new IllegalArgumentException("color " + color + " outside [0," + colorCount + ")");
            }
        }
        this.colors = colors.clone();
        this.colorCount = colorCount;
    }

    public int colorOf(int vertex) {
        return colors[vertex];
    }

    public int colorCount() {
        return colorCount;
    }

    public int[] colors() {
        return colors.clone();
    }

    /** Independent verification of the upper-bound certificate. */
    public boolean isProper(Graph graph) {
        if (colors.length != graph.order()) {
            return false;
        }
        for (int[] edge : graph.edges()) {
            if (colors[edge[0]] == colors[edge[1]]) {
                return false;
            }
        }
        return true;
    }

    /** Colors relabeled to first-appearance order; used in canonical symmetry checks. */
    public Coloring canonicalRelabeling() {
        int[] mapped = new int[colorCount];
        Arrays.fill(mapped, -1);
        int next = 0;
        int[] result = new int[colors.length];
        for (int v = 0; v < colors.length; v++) {
            int c = colors[v];
            if (mapped[c] == -1) {
                mapped[c] = next++;
            }
            result[v] = mapped[c];
        }
        return new Coloring(result, next);
    }

    @Override
    public String toString() {
        return "Coloring(k=" + colorCount + ", c=" + Arrays.toString(colors) + ")";
    }
}
