package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.SearchStats;
import com.example.nonoverlap.api.Solution;
import com.example.nonoverlap.api.Trace;
import com.example.nonoverlap.model.Existence;

import java.util.ArrayList;
import java.util.List;

/**
 * Search scheduler: AC-3 propagation plus depth-first backtracking.
 *
 * <p>Branching order is deliberate:
 * <ol>
 *   <li>if a committed (TRUE) rectangle still has several positions, branch on
 *       its placement (most constrained domain first); this is where ordinary
 *       position backtracking happens;</li>
 *   <li>otherwise branch on an undecided rectangle: TRUE (and then enumerate
 *       positions), then FALSE.</li>
 * </ol>
 *
 * <p>Every branch gets an independent {@link SearchState} copy, so undo is a
 * matter of discarding the copy.
 */
public final class Solver {

    private final Budget budget;
    private final int maxSolutions;
    private final Trace trace;

    public Solver(Budget budget, int maxSolutions, Trace trace) {
        this.budget = budget;
        this.maxSolutions = maxSolutions;
        this.trace = trace == null ? Trace.noop() : trace;
    }

    public KernelOutcome solve(SearchState root) {
        Stats stats = new Stats();
        long start = System.nanoTime();
        List<Solution> found = new ArrayList<>();
        boolean[] limited = {false};
        String[] rootReason = {null};

        dfs(root, "r", stats, found, limited, rootReason);

        SearchStats snapshot = new SearchStats(stats.nodes, stats.branches,
                stats.backtracks, stats.prunes, stats.pairChecks, found.size(),
                (System.nanoTime() - start) / 1_000_000L);

        if (!found.isEmpty()) {
            return maxSolutions == 1
                    ? new KernelOutcome.Found(found.get(0), List.of(found.get(0)), snapshot)
                    : new KernelOutcome.Found(found.get(0), List.copyOf(found), snapshot);
        }
        if (rootReason[0] != null) {
            return new KernelOutcome.Infeasible(rootReason[0], root.externallyForced(), snapshot);
        }
        if (limited[0]) {
            return new KernelOutcome.Limited(
                    "exhausted after " + stats.nodes + " nodes / "
                            + stats.pairChecks + " pair checks", snapshot);
        }
        return new KernelOutcome.Infeasible("search proved no assignment exists", false, snapshot);
    }

    private void dfs(SearchState state, String label, Stats stats,
                     List<Solution> found, boolean[] limited, String[] rootReason) {
        if (found.size() >= maxSolutions) {
            return;
        }
        stats.incNodes();
        boolean rootNode = "r".equals(label);

        Conflict conflict = Propagation.propagate(state, stats, budget,
                rootNode, label, trace);
        if (conflict != null) {
            trace.emit("backtrack", "dead-end",
                    java.util.Map.of("node", label, "reason", conflict.reason()));
            if (rootNode) {
                rootReason[0] = conflict.reason();
            } else {
                stats.incBacktracks();
            }
            return;
        }
        if (budget.exhausted(stats)) {
            limited[0] = true;
            return;
        }
        if (state.allSettled()) {
            Solution sol = state.toSolution();
            trace.emit("solution", "found",
                    java.util.Map.of("node", label, "index", found.size()));
            found.add(sol);
            return;
        }

        int requiredVar = state.selectRequiredVariable();
        if (requiredVar >= 0) {
            branchOnPlacement(state, label, requiredVar, stats, found, limited, rootReason);
            return;
        }
        int undecided = state.selectUndecided();
        if (undecided >= 0) {
            branchOnExistence(state, label, undecided, stats, found, limited, rootReason);
        }
    }

    private void branchOnPlacement(SearchState state, String label, int var,
                                   Stats stats, List<Solution> found, boolean[] limited, String[] rootReason) {
        List<Long> values = List.copyOf(state.domain(var));
        String id = state.instance().rect(var).id();
        int k = 0;
        for (long code : values) {
            if (found.size() >= maxSolutions || limited[0]) {
                return;
            }
            stats.incBranches();
            String childLabel = label + ".p" + var + "-" + k;
            trace.emit("branch", "try-placement",
                    java.util.Map.of("node", childLabel, "rect", id,
                            "anchor", com.example.nonoverlap.model.Placement.decode(code),
                            "candidate", k + 1, "of", values.size()));
            SearchState child = state.copy();
            child.restrictTo(var, code);
            dfs(child, childLabel, stats, found, limited, rootReason);
            k++;
        }
        if (found.size() < maxSolutions && !limited[0]) {
            stats.incBacktracks();
        }
    }

    private void branchOnExistence(SearchState state, String label, int var,
                                   Stats stats, List<Solution> found, boolean[] limited, String[] rootReason) {
        String id = state.instance().rect(var).id();

        stats.incBranches();
        String trueLabel = label + ".e" + var + "=T";
        trace.emit("branch", "try-existence",
                java.util.Map.of("node", trueLabel, "rect", id, "existence", Existence.TRUE));
        SearchState present = state.copy();
        present.setExistence(var, Existence.TRUE);
        dfs(present, trueLabel, stats, found, limited, rootReason);
        if (found.size() >= maxSolutions || limited[0]) {
            return;
        }

        stats.incBranches();
        String falseLabel = label + ".e" + var + "=F";
        trace.emit("branch", "try-existence",
                java.util.Map.of("node", falseLabel, "rect", id, "existence", Existence.FALSE));
        SearchState absent = state.copy();
        absent.setExistence(var, Existence.FALSE);
        dfs(absent, falseLabel, stats, found, limited, rootReason);
        if (found.size() < maxSolutions && !limited[0]) {
            stats.incBacktracks();
        }
    }
}
