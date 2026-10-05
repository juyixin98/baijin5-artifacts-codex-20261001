package clique;

import clique.graph.Graph;
import clique.graph.GraphBuilder;

import java.math.BigInteger;
import java.util.Random;

/** 测试夹具：已知图与确定性随机图。 */
public final class TestGraphs {
    private TestGraphs() {
    }

    public static Graph empty(int n) {
        return new GraphBuilder(n).build();
    }

    public static Graph complete(int n) {
        GraphBuilder b = new GraphBuilder(n);
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                b.addEdge(u, v);
            }
        }
        return b.build();
    }

    public static Graph path(int n) {
        GraphBuilder b = new GraphBuilder(n);
        for (int i = 0; i + 1 < n; i++) {
            b.addEdge(i, i + 1);
        }
        return b.build();
    }

    public static Graph cycle(int n) {
        GraphBuilder b = new GraphBuilder(n);
        for (int i = 0; i < n; i++) {
            b.addEdge(i, (i + 1) % n);
        }
        return b.build();
    }

    /** 两个三角形共一个顶点 2：{0,1,2} 与 {2,3,4}。 */
    public static Graph bowtie() {
        return new GraphBuilder(5)
                .addEdge(0, 1).addEdge(0, 2).addEdge(1, 2)
                .addEdge(2, 3).addEdge(2, 4).addEdge(3, 4)
                .build();
    }

    /** K4 去掉边 (0,1)：极大团 {0,2,3} 与 {1,2,3}。 */
    public static Graph k4MinusEdge() {
        return new GraphBuilder(4)
                .addEdge(0, 2).addEdge(0, 3)
                .addEdge(1, 2).addEdge(1, 3)
                .addEdge(2, 3)
                .build();
    }

    /** 大量重叠团：k 个三角形共顶点 0。 */
    public static Graph overlappingTriangles(int k) {
        GraphBuilder b = new GraphBuilder(1 + 2 * k);
        for (int i = 0; i < k; i++) {
            int u = 1 + 2 * i;
            int v = 2 + 2 * i;
            b.addEdge(0, u).addEdge(0, v).addEdge(u, v);
        }
        return b.build();
    }

    /** 确定性 Erdos-Renyi G(n,p)。 */
    public static Graph random(int n, double p, long seed) {
        Random rnd = new Random(seed);
        GraphBuilder b = new GraphBuilder(n);
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                if (rnd.nextDouble() < p) {
                    b.addEdge(u, v);
                }
            }
        }
        return b.build();
    }

    /** 用位掩码枚举 n 个顶点的全部图（n<=6）：第 k 位对应第 k 个无序点对。 */
    public static Graph fromMask(int n, long mask) {
        GraphBuilder b = new GraphBuilder(n);
        int bit = 0;
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                if (((mask >>> bit) & 1L) != 0L) {
                    b.addEdge(u, v);
                }
                bit++;
            }
        }
        return b.build();
    }

    public static BigInteger setOf(int... vertices) {
        BigInteger s = BigInteger.ZERO;
        for (int v : vertices) {
            s = s.setBit(v);
        }
        return s;
    }
}
