package clique.search;

import clique.graph.Graph;

/**
 * 退化排序（removal order）：反复摘除当前最小度顶点。
 * 性质：任意顶点在序列中之后的邻居数 <= degeneracy。
 * 并列时取小编号顶点，保证同一图的序确定（续扫可复现）。
 */
public final class DegeneracyOrder {
    private final int[] order;
    private final int degeneracy;

    private DegeneracyOrder(int[] order, int degeneracy) {
        this.order = order;
        this.degeneracy = degeneracy;
    }

    public int[] order() {
        return order.clone();
    }

    public int degeneracy() {
        return degeneracy;
    }

    public static DegeneracyOrder compute(Graph g) {
        int n = g.n();
        int[] deg = new int[n];
        for (int v = 0; v < n; v++) {
            deg[v] = g.degree(v);
        }
        boolean[] removed = new boolean[n];
        int[] order = new int[n];
        int degeneracy = 0;
        for (int i = 0; i < n; i++) {
            int best = -1;
            for (int v = 0; v < n; v++) {
                if (!removed[v] && (best < 0 || deg[v] < deg[best])) {
                    best = v;
                }
            }
            order[i] = best;
            removed[best] = true;
            degeneracy = Math.max(degeneracy, deg[best]);
            for (int u : clique.graph.Bits.setBits(g.neighbors(best))) {
                if (!removed[u]) {
                    deg[u]--;
                }
            }
        }
        return new DegeneracyOrder(order, degeneracy);
    }
}
