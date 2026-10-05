package clique.bound;

import clique.error.CliqueException;
import clique.error.ErrorCategory;
import clique.graph.Bits;
import clique.graph.Graph;

import java.math.BigInteger;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

/**
 * 极大性证书：对团 C 外的每个顶点 v，给出一个"阻挡点" u in C 使 (u,v) 不是边。
 * 任何人可凭证书独立复核 C 不可扩展，这就是极大性的可验证证据。
 * 注意：极大不等于最大；本证书只证明不可扩展，不证明规模最优。
 */
public final class MaximalityCertificate {
    private final BigInteger clique;
    private final Map<Integer, Integer> blockers; // 外部顶点 -> 团内阻挡点

    private MaximalityCertificate(BigInteger clique, Map<Integer, Integer> blockers) {
        this.clique = clique;
        this.blockers = blockers;
    }

    public static MaximalityCertificate issue(Graph g, BigInteger clique) {
        if (!CliqueValidator.isClique(g, clique)) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "not a clique, cannot certify: " + clique);
        }
        Map<Integer, Integer> blockers = new HashMap<>();
        BigInteger outside = CliqueValidator.fullMask(g.n()).andNot(clique);
        for (int v : Bits.setBits(outside)) {
            BigInteger block = clique.andNot(g.neighbors(v));
            if (block.signum() == 0) {
                throw new CliqueException(ErrorCategory.INPUT_ERROR,
                        "clique is extendable by vertex " + v + ", not maximal");
            }
            blockers.put(v, block.getLowestSetBit());
        }
        return new MaximalityCertificate(clique, Collections.unmodifiableMap(blockers));
    }

    public BigInteger clique() {
        return clique;
    }

    public Map<Integer, Integer> blockers() {
        return blockers;
    }

    /** 独立复核：每个阻挡点确在团内、确与对应外部顶点不相邻。 */
    public boolean verify(Graph g) {
        BigInteger outside = CliqueValidator.fullMask(g.n()).andNot(clique);
        for (int v : Bits.setBits(outside)) {
            Integer u = blockers.get(v);
            if (u == null || !clique.testBit(u) || g.hasEdge(u, v)) {
                return false;
            }
        }
        return CliqueValidator.isClique(g, clique);
    }
}
