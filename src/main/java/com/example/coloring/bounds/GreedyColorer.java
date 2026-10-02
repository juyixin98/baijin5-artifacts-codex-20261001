package com.example.coloring.bounds;

import com.example.coloring.cert.Coloring;
import com.example.coloring.graph.Graph;

import java.util.BitSet;

/**
 * Deterministic DSATUR greedy heuristic producing a feasible (upper-bound)
 * coloring. The result is always a valid coloring; it is not claimed optimal.
 */
public final class GreedyColorer {

    private GreedyColorer() {
    }

    public static Coloring color(Graph graph) {
        int n = graph.order();
        int[] colors = new int[n];
        BitSet[] neighborColors = new BitSet[n];
        int[] saturation = new int[n];
        boolean[] uncolored = new boolean[n];
        for (int v = 0; v < n; v++) {
            neighborColors[v] = new BitSet(n);
            uncolored[v] = true;
        }

        int used = 0;
        for (int step = 0; step < n; step++) {
            int vertex = pick(graph, uncolored, saturation);
            int chosen = neighborColors[vertex].nextClearBit(0);
            colors[vertex] = chosen;
            uncolored[vertex] = false;
            if (chosen == used) {
                used++;
            }
            BitSet neighbors = graph.neighborSet(vertex);
            for (int w = neighbors.nextSetBit(0); w >= 0; w = neighbors.nextSetBit(w + 1)) {
                if (uncolored[w] && !neighborColors[w].get(chosen)) {
                    neighborColors[w].set(chosen);
                    saturation[w]++;
                }
            }
        }
        return new Coloring(colors, used);
    }

    /** Highest saturation; ties broken by highest degree, then lowest index. */
    private static int pick(Graph graph, boolean[] uncolored, int[] saturation) {
        int best = -1;
        int bestSaturation = -1;
        int bestDegree = -1;
        for (int v = 0; v < uncolored.length; v++) {
            if (!uncolored[v]) {
                continue;
            }
            int degree = graph.degree(v);
            if (saturation[v] > bestSaturation
                    || (saturation[v] == bestSaturation && degree > bestDegree)
                    || (saturation[v] == bestSaturation && degree == bestDegree && (best == -1 || v < best))) {
                best = v;
                bestSaturation = saturation[v];
                bestDegree = degree;
            }
        }
        return best;
    }
}
