package com.example.coloring.bound;

import com.example.coloring.model.Graph;
import java.util.Arrays;

/**
 * Feasible coloring heuristic giving a proven upper bound on the chromatic number.
 * Uses DSATUR: color the uncolored vertex with the largest number of distinct
 * neighbor colors (saturation degree), breaking ties by current uncolored degree
 * and then by the smallest vertex id; assign the smallest non-conflicting color.
 *
 * <p>The returned assignment is always a proper coloring of the given graph, so
 * its number of colors is a valid upper bound regardless of heuristic quality.
 */
public final class GreedyColoring {

    private GreedyColoring() {
    }

    /** Record holding the coloring and the (proper) number of colors used. */
    public record Result(int[] coloring, int colors) {
    }

    public static Result color(Graph graph) {
        int n = graph.n();
        int[] color = new int[n];
        Arrays.fill(color, -1);
        long[] neighborColorMasks = new long[n];
        int[] saturation = new int[n];
        int[] uncoloredDegree = new int[n];
        for (int v = 0; v < n; v++) {
            uncoloredDegree[v] = graph.neighbors(v).size();
        }

        for (int step = 0; step < n; step++) {
            int vertex = selectVertex(n, color, saturation, uncoloredDegree);
            int chosen = smallestFreeColor(neighborColorMasks[vertex]);
            color[vertex] = chosen;
            long bit = 1L << chosen;
            for (int u : graph.neighbors(vertex)) {
                if (color[u] == -1) {
                    uncoloredDegree[u]--;
                    if ((neighborColorMasks[u] & bit) == 0L) {
                        saturation[u]++;
                    }
                }
                // Every neighbor now sees this color on an adjacent vertex.
                neighborColorMasks[u] |= bit;
            }
        }

        int maxColor = 0;
        for (int c : color) {
            maxColor = Math.max(maxColor, c + 1);
        }
        return new Result(color, n == 0 ? 0 : maxColor);
    }

    private static int selectVertex(int n, int[] color, int[] saturation, int[] uncoloredDegree) {
        int best = -1;
        int bestSaturation = -1;
        int bestDegree = -1;
        for (int v = 0; v < n; v++) {
            if (color[v] != -1) {
                continue;
            }
            if (saturation[v] > bestSaturation
                    || (saturation[v] == bestSaturation && uncoloredDegree[v] > bestDegree)
                    || (saturation[v] == bestSaturation
                            && uncoloredDegree[v] == bestDegree
                            && v < best)) {
                best = v;
                bestSaturation = saturation[v];
                bestDegree = uncoloredDegree[v];
            }
        }
        return best;
    }

    private static int smallestFreeColor(long usedMask) {
        long free = ~usedMask;
        int c = 0;
        while ((free & (1L << c)) == 0L) {
            c++;
            if (c >= 63) {
                throw new ArithmeticException(
                        "DSATUR reference heuristic supports at most 63 colors; split the input");
            }
        }
        return c;
    }
}
