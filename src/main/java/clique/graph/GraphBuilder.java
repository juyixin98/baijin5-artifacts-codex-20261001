package clique.graph;

import clique.error.CliqueException;
import clique.error.ErrorCategory;

import java.math.BigInteger;
import java.util.Arrays;

/** 可变构建器；重复边幂等去重，非法输入归入 INPUT_ERROR。 */
public final class GraphBuilder {
    private final int n;
    private final BigInteger[] adj;

    public GraphBuilder(int n) {
        if (n < 0) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "negative vertex count: " + n);
        }
        this.n = n;
        this.adj = new BigInteger[n];
        Arrays.setAll(adj, i -> BigInteger.ZERO);
    }

    public GraphBuilder addEdge(int u, int v) {
        checkVertex(u);
        checkVertex(v);
        if (u == v) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "self-loop at vertex " + u);
        }
        adj[u] = adj[u].setBit(v);
        adj[v] = adj[v].setBit(u);
        return this;
    }

    private void checkVertex(int v) {
        if (v < 0 || v >= n) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR,
                    "vertex " + v + " out of range [0," + n + ")");
        }
    }

    public Graph build() {
        return Graph.fromAdjacency(adj);
    }
}
