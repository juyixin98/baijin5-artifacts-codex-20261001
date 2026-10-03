package com.example.coloring.search;

import com.example.coloring.bound.GreedyColoring;
import com.example.coloring.bound.MaxClique;
import com.example.coloring.diag.DiagnosticEvent;
import com.example.coloring.diag.DiagnosticSink;
import com.example.coloring.diag.PruningReason;
import com.example.coloring.model.Graph;
import java.math.BigInteger;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Minimum vertex coloring of a finite simple graph with proven bounds.
 *
 * <p>Two bounds are maintained independently:
 *
 * <ul>
 *   <li><b>Lower bound</b>: the clique number omega, exact via pivoted
 *       Bron-Kerbosch, with the clique itself as a witness (chi >= omega).</li>
 *   <li><b>Upper bound</b>: a proper DSATUR coloring, feasible by construction
 *       (chi &lt;= number of colors used).</li>
 * </ul>
 *
 * <p>If the bounds coincide the chromatic number is proven. Otherwise the exact
 * {@link KDecision} search tests k-colorability from the lower bound upward;
 * every search node is charged to a shared {@link BigInteger} node budget. On
 * budget exhaustion the search stops and the result reports only the bounds that
 * were actually proven: the largest infeasibility shown (lower bound) and the
 * best feasible coloring found (upper bound).
 */
public final class ColoringSolver {

    /** Default effectively-unlimited node budget for the decision search. */
    public static final BigInteger UNLIMITED_BUDGET = BigInteger.TEN.pow(30);

    private final DiagnosticSink sink;

    public ColoringSolver() {
        this(DiagnosticSink.noop());
    }

    public ColoringSolver(DiagnosticSink sink) {
        this.sink = sink;
    }

    public ColoringResult solve(Graph graph) {
        return solve(graph, UNLIMITED_BUDGET);
    }

    public ColoringResult solve(Graph graph, BigInteger budget) {
        return solve(graph, budget, "req-" + UUID.randomUUID());
    }

    public ColoringResult solve(Graph graph, BigInteger budget, String requestId) {
        if (graph == null) {
            throw new IllegalArgumentException("graph must not be null");
        }
        if (budget == null || budget.signum() < 0) {
            throw new IllegalArgumentException("budget must be a non-null, non-negative BigInteger");
        }

        int[] clique = MaxClique.find(graph);
        GreedyColoring.Result feasible = GreedyColoring.color(graph);
        int provenLower = clique.length;
        int feasibleUpper = feasible.colors();
        int[] bestColoring = feasible.coloring();

        BigInteger remaining = budget;
        Map<PruningReason, Long> totals = new LinkedHashMap<>();

        // The clique witness independently excludes every k < omega, including
        // when no exact search is needed (bounds already meet).
        totals.merge(PruningReason.CLIQUE_LOWER_BOUND, 1L, Long::sum);
        sink.accept(new DiagnosticEvent(
                requestId, 0, PruningReason.CLIQUE_LOWER_BOUND, -1, -1,
                provenLower - 1, remaining,
                Map.of("uncolored", (long) graph.n(),
                        "colorsInUse", 0L,
                        "cliqueSize", (long) clique.length)));

        if (graph.n() == 0 || provenLower == feasibleUpper) {
            return optimal(requestId, provenLower, feasibleUpper, bestColoring, clique,
                    BigInteger.ZERO, budget, totals);
        }

        // Test k-colorability downward from the proven feasible upper bound.
        // Feasibility at k immediately improves the upper bound (and the attached
        // coloring); stopping with budget intact leaves a tight proven interval.
        int k = feasibleUpper - 1;
        while (k >= provenLower) {
            KDecision.Outcome outcome = KDecision.run(graph, k, remaining, requestId, sink);
            remaining = remaining.subtract(outcome.nodesUsed());
            outcome.pruningCounts().forEach((reason, count) ->
                    totals.merge(reason, count, Long::sum));

            if (outcome.verdict() == KDecision.Verdict.FEASIBLE) {
                feasibleUpper = k;
                bestColoring = outcome.coloring();
                k--;
                continue;
            }
            if (outcome.verdict() == KDecision.Verdict.INFEASIBLE) {
                provenLower = k + 1;
                break;
            }
            return stopped(requestId, provenLower, feasibleUpper, bestColoring, clique,
                    budget.subtract(remaining), budget, totals);
        }

        if (provenLower == feasibleUpper) {
            return optimal(requestId, provenLower, feasibleUpper, bestColoring, clique,
                    budget.subtract(remaining), budget, totals);
        }
        return stopped(requestId, provenLower, feasibleUpper, bestColoring, clique,
                budget.subtract(remaining), budget, totals);
    }

    private ColoringResult optimal(String requestId, int value, int upper, int[] coloring,
                                   int[] clique, BigInteger used, BigInteger budget,
                                   Map<PruningReason, Long> counts) {
        return new ColoringResult(requestId, ColoringStatus.OPTIMAL, value, upper,
                coloring, clique, used, budget, counts);
    }

    private ColoringResult stopped(String requestId, int lower, int upper, int[] coloring,
                                   int[] clique, BigInteger used, BigInteger budget,
                                   Map<PruningReason, Long> counts) {
        return new ColoringResult(requestId, ColoringStatus.STOPPED, lower, upper,
                coloring, clique, used, budget, counts);
    }
}
