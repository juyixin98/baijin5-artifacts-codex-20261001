package clique.reference;

import clique.bound.CliqueValidator;
import clique.error.CliqueException;
import clique.error.ErrorCategory;
import clique.graph.Graph;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;

/**
 * 穷举对照实现：枚举全部 2^n 个子集，逐一判定团性与极大性。
 * 与被测的 BronKerbosch 完全独立，作为小图上的参考真值。
 * 约定：空图（n=0）返回空列表（不报告空团），与搜索实现一致。
 */
public final class BruteForceMaximalCliques {
    private BruteForceMaximalCliques() {
    }

    public static List<BigInteger> maximalCliques(Graph g, int maxN) {
        int n = g.n();
        if (n > maxN) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR,
                    "brute force reference limited to n<=" + maxN + ", got " + n);
        }
        List<BigInteger> out = new ArrayList<>();
        if (n == 0) {
            return out;
        }
        BigInteger limit = BigInteger.ONE.shiftLeft(n);
        for (BigInteger s = BigInteger.ONE; s.compareTo(limit) < 0; s = s.add(BigInteger.ONE)) {
            if (CliqueValidator.isClique(g, s) && CliqueValidator.isMaximal(g, s)) {
                out.add(s);
            }
        }
        return out;
    }
}
