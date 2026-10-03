package com.example.coloring.search;

import com.example.coloring.diag.DiagnosticEvent;
import com.example.coloring.diag.DiagnosticSink;
import com.example.coloring.diag.PruningReason;
import com.example.coloring.model.Graph;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;

/**
 * Exact decision procedure: is {@code graph} properly k-colorable?
 *
 * <p>The search is a DSATUR-guided backtracking with two sound pruning rules:
 *
 * <ol>
 *   <li><b>No feasible color</b>: the selected uncolored vertex has every one of
 *       the k colors blocked by an already colored neighbor; the branch is rejected
 *       ({@link PruningReason#NO_FEASIBLE_COLOR}).</li>
 *   <li><b>Color-rename symmetry</b>: colors are interchangeable, so the very first
 *       vertex ever to receive a color must get color 0, the next newly introduced
 *       color must be exactly one above the largest color in use, and so on
 *       (restricted-growth / canonical partitions). A branch that would introduce a
 *       "renamed duplicate" partition is rejected with
 *       {@link PruningReason#SYMMETRY_CANONICAL_SKIP}. Crucially only different
 *       <em>partitions</em> are merged: two structurally different colorings that
 *       induce the same partition are renamings of one another, while distinct
 *       partitions remain distinct branches.</li>
 * </ol>
 *
 * <p>Each call expands one decision-tree node. A shared BigInteger budget bounds
 * the number of nodes; when it reaches zero the search stops and reports only the
 * bounds proven so far.
 */
final class KDecision {

    enum Verdict {
        FEASIBLE,
        INFEASIBLE,
        BUDGET_STOPPED
    }

    record Outcome(Verdict verdict, int[] coloring, BigInteger nodesUsed,
                   Map<PruningReason, Long> pruningCounts) {
    }

    private final Graph graph;
    private final int k;
    private final int n;
    private final String requestId;
    private final DiagnosticSink sink;

    private final int[] color;
    private final long[] blocked;
    private int uncolored;
    private int highestColorInUse = -1;
    private long nodeId = 0;
    private final EnumMap<PruningReason, Long> pruningCounts = new EnumMap<>(PruningReason.class);
    private BigInteger remaining;

    private KDecision(Graph graph, int k, BigInteger budget, String requestId, DiagnosticSink sink) {
        this.graph = graph;
        this.k = k;
        this.n = graph.n();
        this.requestId = requestId;
        this.sink = sink;
        this.color = new int[n];
        this.blocked = new long[n];
        java.util.Arrays.fill(color, -1);
        this.uncolored = n;
        this.remaining = budget;
    }

    static Outcome run(Graph graph, int k, BigInteger budget, String requestId, DiagnosticSink sink) {
        KDecision search = new KDecision(graph, k, budget, requestId, sink);
        Verdict verdict = search.expand();
        return new Outcome(verdict,
                verdict == Verdict.FEASIBLE ? search.color.clone() : null,
                budget.subtract(search.remaining),
                Map.copyOf(search.pruningCounts));
    }

    private void tally(PruningReason reason) {
        pruningCounts.merge(reason, 1L, Long::sum);
    }

    private void emit(PruningReason reason, int vertex, int chosenColor) {
        sink.accept(new DiagnosticEvent(
                requestId,
                nodeId,
                reason,
                vertex,
                chosenColor,
                k,
                remaining,
                Map.of(
                        "uncolored", (long) uncolored,
                        "colorsInUse", (long) (highestColorInUse + 1))));
    }

    private Verdict expand() {
        if (remaining.signum() == 0) {
            tally(PruningReason.BUDGET_EXHAUSTED);
            emit(PruningReason.BUDGET_EXHAUSTED, -1, -1);
            return Verdict.BUDGET_STOPPED;
        }
        remaining = remaining.subtract(BigInteger.ONE);
        nodeId++;

        if (uncolored == 0) {
            tally(PruningReason.FOUND_FEASIBLE);
            emit(PruningReason.FOUND_FEASIBLE, -1, -1);
            return Verdict.FEASIBLE;
        }

        int vertex = selectVertex();
        List<Integer> allowedColors = new ArrayList<>(k);
        for (int c = 0; c < k; c++) {
            if ((blocked[vertex] & (1L << c)) == 0L) {
                allowedColors.add(c);
            }
        }
        if (allowedColors.isEmpty()) {
            tally(PruningReason.NO_FEASIBLE_COLOR);
            emit(PruningReason.NO_FEASIBLE_COLOR, vertex, -1);
            return Verdict.INFEASIBLE;
        }

        // Canonical restricted-growth branching: the vertex may reuse ANY color
        // already in use, or introduce EXACTLY the next new color (maxInUse + 1).
        // Trying a still-unused color above that value only renames a partition
        // that the "next new color" branch already represents; those branches are
        // the only ones pruned. Distinct partitions (which old color is reused,
        // or whether a new block is opened) are all explored separately.
        int nextNewColor = highestColorInUse + 1;

        List<Integer> canonicalColors = new ArrayList<>(allowedColors.size());
        for (int c : allowedColors) {
            if (c > nextNewColor) {
                tally(PruningReason.SYMMETRY_CANONICAL_SKIP);
                emit(PruningReason.SYMMETRY_CANONICAL_SKIP, vertex, c);
            } else {
                canonicalColors.add(c);
            }
        }

        for (int c : canonicalColors) {
            assign(vertex, c);
            Verdict child = expand();
            if (child == Verdict.FEASIBLE) {
                return Verdict.FEASIBLE;
            }
            unassign(vertex, c);
            if (child == Verdict.BUDGET_STOPPED) {
                return Verdict.BUDGET_STOPPED;
            }
        }
        return Verdict.INFEASIBLE;
    }


    private void assign(int vertex, int c) {
        color[vertex] = c;
        uncolored--;
        long bit = 1L << c;
        for (int u : graph.neighbors(vertex)) {
            if (color[u] == -1) {
                blocked[u] |= bit;
            }
        }
        if (c > highestColorInUse) {
            highestColorInUse = c;
        }
    }

    private void unassign(int vertex, int c) {
        color[vertex] = -1;
        uncolored++;
        for (int u : graph.neighbors(vertex)) {
            if (color[u] == -1) {
                long mask = 0L;
                for (int w : graph.neighbors(u)) {
                    if (color[w] != -1) {
                        mask |= 1L << color[w];
                    }
                }
                blocked[u] = mask;
            }
        }
        if (c == highestColorInUse) {
            highestColorInUse = c - 1;
        }
    }

    private int selectVertex() {
        int best = -1;
        int bestBlocked = -1;
        int bestDegree = -1;
        for (int v = 0; v < n; v++) {
            if (color[v] != -1) {
                continue;
            }
            int blockedCount = Long.bitCount(blocked[v]);
            int degree = graph.neighbors(v).size();
            if (blockedCount > bestBlocked
                    || (blockedCount == bestBlocked && degree > bestDegree)
                    || (blockedCount == bestBlocked && degree == bestDegree && v < best)) {
                best = v;
                bestBlocked = blockedCount;
                bestDegree = degree;
            }
        }
        return best;
    }
}
