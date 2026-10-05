package clique.graph;

import clique.error.CliqueException;
import clique.error.ErrorCategory;

import java.math.BigInteger;
import java.util.Arrays;

/**
 * 图契约：无向简单图，顶点编号 0..n-1，邻接关系用 BigInteger 位集表示
 * （第 v 位置位表示 v 是邻居）。不可变。
 *
 * 契约不变式：
 *  - 无自环（adj[v] 的第 v 位为 0）
 *  - 对称（u in N(v) 当且仅当 v in N(u)）
 *  - 不允许出现 >= n 的位
 * 构建期重复边自动去重（位集幂等）。
 */
public final class Graph {
    private final int n;
    private final BigInteger[] adj;
    private final int edgeCount;
    private final int fingerprint;

    private Graph(int n, BigInteger[] adj, int edgeCount) {
        this.n = n;
        this.adj = adj;
        this.edgeCount = edgeCount;
        this.fingerprint = 31 * n + Arrays.hashCode(adj);
    }

    /** 从邻接位集数组构造，并完整校验契约。 */
    public static Graph fromAdjacency(BigInteger[] adjacency) {
        if (adjacency == null) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "adjacency array is null");
        }
        int n = adjacency.length;
        BigInteger[] adj = new BigInteger[n];
        BigInteger mask = BigInteger.ONE.shiftLeft(n).subtract(BigInteger.ONE);
        for (int v = 0; v < n; v++) {
            BigInteger row = adjacency[v];
            if (row == null) {
                throw new CliqueException(ErrorCategory.INPUT_ERROR, "adjacency row " + v + " is null");
            }
            if (row.signum() < 0) {
                throw new CliqueException(ErrorCategory.INPUT_ERROR, "adjacency row " + v + " is negative");
            }
            if (row.testBit(v)) {
                throw new CliqueException(ErrorCategory.INPUT_ERROR, "self-loop at vertex " + v);
            }
            if (row.andNot(mask).signum() != 0) {
                throw new CliqueException(ErrorCategory.INPUT_ERROR,
                        "adjacency row " + v + " has bits outside vertex range [0," + n + ")");
            }
            adj[v] = row;
        }
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                if (adj[u].testBit(v) != adj[v].testBit(u)) {
                    throw new CliqueException(ErrorCategory.INPUT_ERROR,
                            "asymmetric adjacency between " + u + " and " + v);
                }
            }
        }
        int m = 0;
        for (int v = 0; v < n; v++) {
            m += adj[v].bitCount();
        }
        return new Graph(n, adj, m / 2);
    }

    public int n() {
        return n;
    }

    public int edgeCount() {
        return edgeCount;
    }

    /** 返回 v 的邻居位集。调用方不得依赖可变性（返回值只读使用）。 */
    public BigInteger neighbors(int v) {
        return adj[v];
    }

    public boolean hasEdge(int u, int v) {
        return adj[u].testBit(v);
    }

    public int degree(int v) {
        return adj[v].bitCount();
    }

    /** 图内容指纹，用于续扫状态与图的一致性校验（配合 n 与 edgeCount）。 */
    public int fingerprint() {
        return fingerprint;
    }
}
