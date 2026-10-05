package clique.bound;

import clique.graph.Graph;
import clique.search.DegeneracyOrder;

import java.math.BigInteger;
import java.util.List;

/**
 * 界与证书：
 *  - 下界 lower：已找到的最大团规模（枚举完整时恰等于 omega）及其实例；
 *  - 上界 upper：退化序贪心着色的颜色数（omega <= chi_greedy <= degeneracy+1）。
 * verify() 独立复核下界实例确为团、上界着色确为合法着色。
 */
public final class BoundsCertificate {
    private final int lower;
    private final BigInteger lowerClique;
    private final int upper;
    private final int[] coloring;

    private BoundsCertificate(int lower, BigInteger lowerClique, int upper, int[] coloring) {
        this.lower = lower;
        this.lowerClique = lowerClique;
        this.upper = upper;
        this.coloring = coloring;
    }

    public static BoundsCertificate assess(Graph g, List<BigInteger> maximalCliques) {
        int lower = 0;
        BigInteger lowerClique = BigInteger.ZERO;
        for (BigInteger c : maximalCliques) {
            if (c.bitCount() > lower) {
                lower = c.bitCount();
                lowerClique = c;
            }
        }
        int[] order = DegeneracyOrder.compute(g).order();
        int[] coloring = GreedyColoring.color(g, order);
        int upper = 0;
        for (int c : coloring) {
            upper = Math.max(upper, c + 1);
        }
        return new BoundsCertificate(lower, lowerClique, upper, coloring);
    }

    public int lower() {
        return lower;
    }

    public BigInteger lowerClique() {
        return lowerClique;
    }

    public int upper() {
        return upper;
    }

    public int[] coloring() {
        return coloring.clone();
    }

    public boolean verify(Graph g) {
        if (lower > upper) {
            return false;
        }
        if (lower > 0 && (lowerClique.bitCount() != lower || !CliqueValidator.isClique(g, lowerClique))) {
            return false;
        }
        return GreedyColoring.isProper(g, coloring);
    }
}
