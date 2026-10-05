package clique.bound;

import clique.graph.Bits;
import clique.graph.Graph;

import java.util.Arrays;

/** 按给定顶点序贪心着色；颜色数是团数 omega 的上界（omega <= 任意合法着色数）。 */
public final class GreedyColoring {
    private GreedyColoring() {
    }

    public static int[] color(Graph g, int[] order) {
        int n = g.n();
        int[] color = new int[n];
        Arrays.fill(color, -1);
        for (int v : order) {
            boolean[] used = new boolean[n + 1];
            for (int u : Bits.setBits(g.neighbors(v))) {
                if (color[u] >= 0) {
                    used[color[u]] = true;
                }
            }
            int c = 0;
            while (used[c]) {
                c++;
            }
            color[v] = c;
        }
        return color;
    }

    /** 校验是合法着色：每条边两端颜色不同。 */
    public static boolean isProper(Graph g, int[] color) {
        for (int u = 0; u < g.n(); u++) {
            for (int v : Bits.setBits(g.neighbors(u))) {
                if (color[u] == color[v]) {
                    return false;
                }
            }
        }
        return true;
    }
}
