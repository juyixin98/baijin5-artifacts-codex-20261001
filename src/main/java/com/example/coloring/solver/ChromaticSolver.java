package com.example.coloring.solver;

import com.example.coloring.bounds.CliqueCertificate;
import com.example.coloring.bounds.CliqueLowerBound;
import com.example.coloring.bounds.GreedyColorer;
import com.example.coloring.cert.Coloring;
import com.example.coloring.diag.DecisionKind;
import com.example.coloring.diag.Reason;
import com.example.coloring.diag.RequestContext;
import com.example.coloring.diag.SearchEvent;
import com.example.coloring.diag.SearchListener;
import com.example.coloring.graph.Graph;

import java.util.ArrayList;
import java.util.BitSet;
import java.util.List;

/**
 * Minimum vertex coloring by branch-and-bound.
 *
 * <p>The lower bound is a clique certificate (pairwise adjacent vertices need
 * distinct colors); the upper bound is a concrete proper coloring. Both are
 * maintained independently. Color-renaming symmetry is handled by using one
 * canonical fresh label. Stopping on budget never claims an exact value.</p>
 *
 * <p>Disconnected graphs are solved per connected component: the chromatic
 * number of a disjoint union is the maximum of the components' numbers, and
 * component colorings reuse labels across components (no edges join them).</p>
 */
public final class ChromaticSolver {

    private final SolverConfig config;

    public ChromaticSolver(SolverConfig config) {
        this.config = config;
    }

    public ChromaticSolver() {
        this(SolverConfig.standard());
    }

    public ChromaticResult solve(Graph graph) {
        return solve(graph, RequestContext.create(false), SearchListener.noop());
    }

    public ChromaticResult solve(Graph graph, RequestContext context, SearchListener listener) {
        int n = graph.order();
        if (n == 0) {
            return new ChromaticResult(SearchStatus.OPTIMAL, 0, 0,
                    new CliqueCertificate(List.of(), true),
                    new Coloring(new int[0], 0), 0, false, false);
        }

        List<List<Integer>> components = graph.components();
        Budget budget = new Budget(config.nodeBudget(), config.timeBudgetMillis());

        int[] globalColors = new int[n];
        int globalLower = 0;
        int globalUpper = 0;
        long totalNodes = 0;
        boolean exact = true;
        boolean nodeStop = false;
        boolean timeStop = false;
        List<Integer> globalClique = List.of();

        for (int ci = 0; ci < components.size(); ci++) {
            List<Integer> vertices = components.get(ci);
            Graph sub = graph.inducedBy(vertices);
            ComponentResult result = solveConnected(sub, ci, components.size(), budget, context, listener);
            nodeStop |= result.stoppedByNodeBudget;
            timeStop |= result.stoppedByTimeBudget;
            exact &= result.status == SearchStatus.OPTIMAL;
            totalNodes += result.nodesVisited;

            if (result.lowerBound > globalLower) {
                globalLower = result.lowerBound;
                List<Integer> mapped = new ArrayList<>(result.clique.size());
                for (int local : result.clique) {
                    mapped.add(vertices.get(local));
                }
                globalClique = List.copyOf(mapped);
            }
            if (result.upperBound > globalUpper) {
                globalUpper = result.upperBound;
            }
            for (int local = 0; local < vertices.size(); local++) {
                globalColors[vertices.get(local)] = result.bestColors[local];
            }
        }

        CliqueCertificate witness = new CliqueCertificate(globalClique, true);
        Coloring coloring = new Coloring(globalColors, globalUpper);
        SearchStatus status = exact ? SearchStatus.OPTIMAL : SearchStatus.BOUNDS_PROVEN;
        return new ChromaticResult(status, globalLower, globalUpper, witness,
                coloring, totalNodes, nodeStop, timeStop);
    }

    private ComponentResult solveConnected(Graph graph, int componentIndex, int componentCount,
                                           Budget budget, RequestContext context, SearchListener listener) {
        int n = graph.order();
        CliqueCertificate clique = CliqueLowerBound.maximumClique(graph);
        int[] bestColors;
        int best;
        if (config.warmStartGreedy()) {
            Coloring seed = GreedyColorer.color(graph);
            bestColors = seed.colors();
            best = seed.colorCount();
        } else {
            bestColors = new int[n];
            for (int v = 0; v < n; v++) {
                bestColors[v] = v;
            }
            best = n;
        }

        int[] colors = new int[n];
        java.util.Arrays.fill(colors, -1);
        BitSet[] neighborColors = new BitSet[n];
        int[] saturation = new int[n];
        for (int v = 0; v < n; v++) {
            neighborColors[v] = new BitSet(n);
        }

        SearchState state = new SearchState(graph, colors, neighborColors, saturation,
                bestColors, best, clique, budget, context, listener, componentIndex);

        if (clique.size() == state.best) {
            state.emit(SearchKind.MATCH, Reason.LOWER_BOUND_MATCHES_UPPER, -1, -1, 0);
            return state.toComponentResult(SearchStatus.OPTIMAL);
        }

        state.dfs(0);

        if (state.aborted) {
            return state.toComponentResult(SearchStatus.BOUNDS_PROVEN);
        }
        return state.toComponentResult(SearchStatus.OPTIMAL);
    }

    private enum SearchKind { BRANCH, FEASIBLE, REJECT, MATCH, BUDGET }

    private static final class SearchState {
        final Graph graph;
        final int n;
        final int[] colors;
        final BitSet[] neighborColors;
        final int[] saturation;
        final int[] bestColors;
        final CliqueCertificate clique;
        final Budget budget;
        final RequestContext context;
        final SearchListener listener;
        final int componentIndex;

        int best;
        int used;
        boolean aborted;
        boolean stoppedByNodeBudget;
        boolean stoppedByTimeBudget;
        private long sequence;

        SearchState(Graph graph, int[] colors, BitSet[] neighborColors, int[] saturation,
                    int[] bestColors, int best, CliqueCertificate clique, Budget budget,
                    RequestContext context, SearchListener listener, int componentIndex) {
            this.graph = graph;
            this.n = graph.order();
            this.colors = colors;
            this.neighborColors = neighborColors;
            this.saturation = saturation;
            this.bestColors = bestColors;
            this.best = best;
            this.clique = clique;
            this.budget = budget;
            this.context = context;
            this.listener = listener;
            this.componentIndex = componentIndex;
        }

        void dfs(int depth) {
            if (aborted) {
                return;
            }
            budget.chargeNode();
            Reason stop = budget.exhaustedReason();
            if (stop != null) {
                aborted = true;
                stoppedByNodeBudget = stop == Reason.NODE_BUDGET_EXHAUSTED;
                stoppedByTimeBudget = stop == Reason.TIME_BUDGET_EXHAUSTED;
                emit(SearchKind.BUDGET, stop, -1, -1, used);
                return;
            }

            int vertex = pickVertex();
            if (vertex < 0) {
                if (used < best) {
                    best = used;
                    System.arraycopy(colors, 0, bestColors, 0, n);
                    emit(SearchKind.FEASIBLE, Reason.FEASIBLE_COMPLETE, -1, -1, used);
                }
                return;
            }

            for (int candidate = 0; candidate < best; candidate++) {
                if (aborted) {
                    return;
                }
                if (candidate < used) {
                    if (neighborColors[vertex].get(candidate)) {
                        emit(SearchKind.REJECT, Reason.ADJACENT_SAME_COLOR, vertex, candidate, used);
                        continue;
                    }
                    if (!canImprove(used)) {
                        emit(SearchKind.REJECT, Reason.LOWER_BOUND_NOT_IMPROVABLE, vertex, candidate, used);
                        continue;
                    }
                    assign(vertex, candidate);
                    emit(SearchKind.BRANCH, Reason.BRANCH_EXPLORED, vertex, candidate, used);
                    dfs(depth + 1);
                    unassign(vertex, candidate);
                } else if (candidate == used) {
                    if (!canImprove(used + 1)) {
                        emit(SearchKind.REJECT, Reason.LOWER_BOUND_NOT_IMPROVABLE, vertex, candidate, used);
                        continue;
                    }
                    assignFresh(vertex, candidate);
                    emit(SearchKind.BRANCH, Reason.BRANCH_EXPLORED, vertex, candidate, used);
                    dfs(depth + 1);
                    unassignFresh(vertex, candidate);
                } else {
                    // candidate > used: an unused, non-canonical fresh label.
                    // Renaming symmetry makes it identical to label == used.
                    emit(SearchKind.REJECT, Reason.SYMMETRY_NEW_COLOR_UNUSED, vertex, candidate, used);
                }
            }
        }

        private void assign(int vertex, int color) {
            colors[vertex] = color;
            BitSet neighbors = graph.neighborSet(vertex);
            for (int w = neighbors.nextSetBit(0); w >= 0; w = neighbors.nextSetBit(w + 1)) {
                if (colors[w] == -1 && !neighborColors[w].get(color)) {
                    neighborColors[w].set(color);
                    saturation[w]++;
                }
            }
        }

        private void unassign(int vertex, int color) {
            colors[vertex] = -1;
            BitSet neighbors = graph.neighborSet(vertex);
            for (int w = neighbors.nextSetBit(0); w >= 0; w = neighbors.nextSetBit(w + 1)) {
                if (colors[w] == -1 && neighborColors[w].get(color)) {
                    neighborColors[w].clear(color);
                    saturation[w]--;
                }
            }
        }

        private void assignFresh(int vertex, int color) {
            used++;
            assign(vertex, color);
        }

        private void unassignFresh(int vertex, int color) {
            unassign(vertex, color);
            used--;
        }

        private int pickVertex() {
            int bestVertex = -1;
            int bestSaturation = -1;
            int bestDegree = -1;
            for (int v = 0; v < n; v++) {
                if (colors[v] != -1) {
                    continue;
                }
                int degree = graph.degree(v);
                if (saturation[v] > bestSaturation
                        || (saturation[v] == bestSaturation && degree > bestDegree)
                        || (saturation[v] == bestSaturation && degree == bestDegree
                            && (bestVertex == -1 || v < bestVertex))) {
                    bestVertex = v;
                    bestSaturation = saturation[v];
                    bestDegree = degree;
                }
            }
            return bestVertex;
        }

        /**
         * A node already using {@code distinctColors} different labels can only
         * finish with at least that many classes. It is worth exploring only
         * when a strictly better coloring than {@code best} is still possible.
         */
        private boolean canImprove(int distinctColors) {
            return distinctColors < best;
        }

        private void emit(SearchKind kind, Reason reason, int vertex, int color, int usedNow) {
            DecisionKind decision = switch (kind) {
                case BRANCH -> DecisionKind.ACCEPT_BRANCH;
                case FEASIBLE -> DecisionKind.ACCEPT_FEASIBLE;
                case MATCH -> DecisionKind.ACCEPT_BOUND_MATCH;
                case BUDGET -> DecisionKind.UNDETERMINED_BUDGET;
                case REJECT -> DecisionKind.REJECT;
            };
            SearchEvent event = new SearchEvent(
                    context.requestId(), ++sequence, decision, reason, depthHint(),
                    vertex, color, usedNow, best, budget.nodes(), clique.vertices());
            listener.onEvent(event);
        }

        private int depthHint() {
            int colored = 0;
            for (int color : colors) {
                if (color != -1) {
                    colored++;
                }
            }
            return colored;
        }

        ComponentResult toComponentResult(SearchStatus status) {
            ComponentResult result = new ComponentResult();
            result.status = status;
            result.lowerBound = clique.size();
            result.upperBound = best;
            result.clique = clique.vertices();
            result.bestColors = bestColors.clone();
            result.nodesVisited = budget.nodes();
            result.stoppedByNodeBudget = stoppedByNodeBudget;
            result.stoppedByTimeBudget = stoppedByTimeBudget;
            return result;
        }
    }

    private static final class ComponentResult {
        SearchStatus status;
        int lowerBound;
        int upperBound;
        List<Integer> clique;
        int[] bestColors;
        long nodesVisited;
        boolean stoppedByNodeBudget;
        boolean stoppedByTimeBudget;
    }
}
