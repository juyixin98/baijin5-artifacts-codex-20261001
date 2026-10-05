package clique.search;

import clique.config.CliqueConfig;
import clique.graph.Graph;
import clique.run.RunLog;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * 带枢轴的 BronKerbosch 极大团枚举，外层按退化排序驱动（Tomita 式）：
 * 对序中第 i 个顶点 v=order[i]，递归枚举 R={v}、P=N(v)∩后继、X=N(v)∩前驱。
 * 每个极大团 C 恰在最小的序下标 i=min{j: order[j] in C} 处被报告一次，
 * 因此输出天然唯一且完备（不遗漏、不重复）。
 *
 * 取消/预算以"外层下标"为提交粒度：下标 i 的产出先进入缓冲区，递归完整结束才提交；
 * 中途取消则丢弃该缓冲、以 nextOuterIndex=i 形成续扫状态，保证续扫后不重不漏。
 */
public final class BronKerbosch {
    private final Graph graph;
    private final CliqueConfig config;
    private final RunLog log;
    private final DegeneracyOrder deg;

    private int currentOuterIndex;

    public BronKerbosch(Graph graph, CliqueConfig config, RunLog log) {
        this.graph = Objects.requireNonNull(graph, "graph");
        this.config = config == null ? CliqueConfig.defaults() : config;
        this.log = log == null ? RunLog.create() : log;
        this.deg = DegeneracyOrder.compute(graph);
    }

    public Graph graph() {
        return graph;
    }

    public DegeneracyOrder degeneracyOrder() {
        return deg;
    }

    public String runId() {
        return log.runId();
    }

    /** 全量枚举。 */
    public SearchResult enumerate(CancellationToken token) {
        return run(0, 0L, token);
    }

    /** 从续扫状态继续。状态与图不一致时抛 STATE_CONFLICT。 */
    public SearchResult resume(ResumeState state, CancellationToken token) {
        Objects.requireNonNull(state, "state");
        state.validateFor(graph);
        return run(state.nextOuterIndex(), state.emittedBefore(), token);
    }

    private SearchResult run(int startIndex, long emittedBefore, CancellationToken token) {
        Objects.requireNonNull(token, "token");
        int n = graph.n();
        int[] order = deg.order();
        log.log("RUN_START",
                "n=" + n + " m=" + graph.edgeCount() + " degeneracy=" + deg.degeneracy()
                        + " startIndex=" + startIndex + " emittedBefore=" + emittedBefore,
                "config maxSteps=" + config.maxSteps() + " pivot=" + config.pivot());

        // later[i] = { order[i], order[i+1], ..., order[n-1] } 的位集；earlier[i] = { order[0..i-1] }
        BigInteger[] later = new BigInteger[n + 1];
        later[n] = BigInteger.ZERO;
        for (int i = n - 1; i >= 0; i--) {
            later[i] = later[i + 1].setBit(order[i]);
        }
        BigInteger[] earlier = new BigInteger[n];
        BigInteger acc = BigInteger.ZERO;
        for (int i = 0; i < n; i++) {
            earlier[i] = acc;
            acc = acc.setBit(order[i]);
        }

        List<BigInteger> committed = new ArrayList<>();
        long[] steps = {0L};
        try {
            for (int i = startIndex; i < n; i++) {
                currentOuterIndex = i;
                int v = order[i];
                List<BigInteger> buffer = new ArrayList<>();
                BigInteger p = graph.neighbors(v).and(later[i + 1]);
                BigInteger x = graph.neighbors(v).and(earlier[i]);
                expand(BigInteger.ZERO.setBit(v), p, x, buffer, token, steps);
                committed.addAll(buffer);
                token.notifyCommitted(committed.size());
                if (config.verbosity() != CliqueConfig.Verbosity.SUMMARY) {
                    log.log("BUFFER_COMMIT",
                            "outerIndex=" + i + " vertex=" + v + " cliques=" + buffer.size(),
                            "committedTotal=" + committed.size());
                }
                if (token.isCancelled()) {
                    log.log("CANCEL_DETECTED", "outerIndex=" + (i + 1) + " committed=" + committed.size(),
                            "token cancelled after commit; resume from next outer index");
                    return partial(committed, i + 1, steps[0], emittedBefore);
                }
            }
        } catch (SearchCancelled c) {
            log.log("CANCEL_DETECTED", "outerIndex=" + c.outerIndex + " committed=" + committed.size(),
                    c.reason);
            return partial(committed, c.outerIndex, steps[0], emittedBefore);
        } catch (BudgetExceeded b) {
            log.log("BUDGET_EXHAUSTED",
                    "steps=" + steps[0] + " limit=" + config.maxSteps() + " outerIndex=" + currentOuterIndex,
                    "step budget hit; partial result is resumable");
            SearchResult p = partial(committed, currentOuterIndex, steps[0], emittedBefore);
            throw new ResourceExhaustedException(p,
                    "step budget exhausted: steps=" + steps[0] + " limit=" + config.maxSteps());
        }
        log.log("RUN_COMPLETE", "cliques=" + committed.size() + " steps=" + steps[0], "completed=true");
        return new SearchResult(committed, true, steps[0], log.runId(), null);
    }

    private SearchResult partial(List<BigInteger> committed, int nextOuterIndex, long steps, long emittedBefore) {
        ResumeState state = new ResumeState(graph.n(), graph.edgeCount(), graph.fingerprint(),
                nextOuterIndex, emittedBefore + committed.size());
        return new SearchResult(committed, false, steps, log.runId(), state);
    }

    /** 带枢轴的递归核心：BK(R, P, X)，P 为空且 X 为空时 R 为极大团。 */
    private void expand(BigInteger r, BigInteger p, BigInteger x, List<BigInteger> sink,
                        CancellationToken token, long[] steps) {
        steps[0]++;
        if (steps[0] > config.maxSteps()) {
            throw new BudgetExceeded();
        }
        if (token.isCancelled()) {
            throw new SearchCancelled(currentOuterIndex,
                    "token cancelled inside recursion, |R|=" + r.bitCount());
        }
        if (p.signum() == 0) {
            if (x.signum() == 0) {
                sink.add(r);
            }
            return;
        }
        int pivot = choosePivot(p, x);
        BigInteger rest = pivot >= 0 ? p.andNot(graph.neighbors(pivot)) : p;
        while (rest.signum() != 0) {
            int v = rest.getLowestSetBit();
            rest = rest.clearBit(v);
            BigInteger nv = graph.neighbors(v);
            expand(r.setBit(v), p.and(nv), x.and(nv), sink, token, steps);
            p = p.clearBit(v);
            x = x.setBit(v);
        }
    }

    private int choosePivot(BigInteger p, BigInteger x) {
        BigInteger union = p.or(x);
        if (union.signum() == 0) {
            return -1;
        }
        if (config.pivot() == CliqueConfig.PivotStrategy.FIRST) {
            return union.getLowestSetBit();
        }
        // MAX_INTERSECTION：最大化 |P ∩ N(u)|
        int best = -1;
        int bestCount = -1;
        for (int u : clique.graph.Bits.setBits(union)) {
            int c = p.and(graph.neighbors(u)).bitCount();
            if (c > bestCount) {
                bestCount = c;
                best = u;
            }
        }
        return best;
    }

    private static final class SearchCancelled extends RuntimeException {
        final int outerIndex;
        final String reason;

        SearchCancelled(int outerIndex, String reason) {
            super(reason);
            this.outerIndex = outerIndex;
            this.reason = reason;
        }
    }

    private static final class BudgetExceeded extends RuntimeException {
    }
}
