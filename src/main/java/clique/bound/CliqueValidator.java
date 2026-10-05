package clique.bound;

import clique.graph.Bits;
import clique.graph.Graph;

import java.math.BigInteger;

/** 独立的团/极大性判定器：不依赖搜索算法，供证书与穷举对照复用。 */
public final class CliqueValidator {
    private CliqueValidator() {
    }

    public static BigInteger fullMask(int n) {
        return BigInteger.ONE.shiftLeft(n).subtract(BigInteger.ONE);
    }

    /** s 中任意两点均相邻。 */
    public static boolean isClique(Graph g, BigInteger s) {
        for (int v : Bits.setBits(s)) {
            // s\{v} 必须全部落在 N(v) 内
            if (s.clearBit(v).andNot(g.neighbors(v)).signum() != 0) {
                return false;
            }
        }
        return true;
    }

    /** s 是团，且任意 s 外顶点都与 s 中某点不相邻（无法扩展）。 */
    public static boolean isMaximal(Graph g, BigInteger s) {
        if (!isClique(g, s)) {
            return false;
        }
        BigInteger outside = fullMask(g.n()).andNot(s);
        for (int v : Bits.setBits(outside)) {
            // 若 s 全部落在 N(v) 内，则 v 可加入，s 非极大
            if (s.andNot(g.neighbors(v)).signum() == 0) {
                return false;
            }
        }
        return true;
    }
}
