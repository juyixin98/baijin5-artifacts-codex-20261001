package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.Trace;
import com.example.nonoverlap.model.Existence;
import com.example.nonoverlap.model.Geometry;
import com.example.nonoverlap.model.Placement;
import com.example.nonoverlap.model.RectDef;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Arc-consistency propagation for the pair-wise non-overlap constraint.
 *
 * <p>Semantics (see README):
 * <ul>
 *   <li>FALSE rectangles are removed and checked against nothing.</li>
 *   <li>A TRUE rectangle i is revised against j <em>only</em> when j is also
 *       necessarily present; the support test is "j's domain has some placement
 *       whose interior is disjoint from i's candidate (boundary contact allowed,
 *       zero-area rectangles disjoint by definition)".</li>
 *   <li>UNKNOWN j never participates in revision, because the option of setting
 *       j to FALSE guarantees support. Strong pruning against an undecided
 *       rectangle is therefore impossible.</li>
 * </ul>
 */
public final class Propagation {

    private Propagation() {
    }

    /** Runs arc consistency from scratch on a freshly entered node. */
    public static Conflict propagate(SearchState state, Stats stats, Budget budget,
                                     boolean atRoot, String nodeLabel, Trace trace) {
        int n = state.size();
        ArrayDeque<int[]> queue = new ArrayDeque<>();
        for (int i = 0; i < n; i++) {
            if (!state.activeRequired(i)) {
                continue;
            }
            if (state.domain(i).isEmpty()) {
                trace.emit("conflict", "empty-required-domain",
                        Map.of("node", nodeLabel, "rect", state.instance().rect(i).id()));
                return new Conflict(i, "rectangle '" + state.instance().rect(i).id()
                        + "' is required but has no placement", atRoot && state.externallyForced());
            }
            for (int j = 0; j < n; j++) {
                if (j != i && state.activeRequired(j)) {
                    queue.add(new int[]{i, j});
                }
            }
        }
        trace.emit("propagate", "queue-seeded",
                Map.of("node", nodeLabel, "arcs", queue.size()));

        while (!queue.isEmpty()) {
            if (budget.exhausted(stats)) {
                trace.emit("limit", "budget-exhausted",
                        Map.of("node", nodeLabel, "nodes", stats.nodes,
                                "pairChecks", stats.pairChecks));
                return null;
            }
            int[] arc = queue.poll();
            int i = arc[0];
            int j = arc[1];
            if (!state.activeRequired(i) || !state.activeRequired(j)) {
                continue;
            }
            Revision rev = revise(state, i, j, stats, nodeLabel, trace);
            if (rev == null) {
                continue;
            }
            stats.addPrunes(rev.removed.size());
            trace.emit("revise", "pruned",
                    Map.of("node", nodeLabel, "rect", state.instance().rect(i).id(),
                            "against", state.instance().rect(j).id(),
                            "removed", rev.removed.size(),
                            "remaining", state.domainSize(i)));
            if (state.domainSize(i) == 0) {
                trace.emit("conflict", "domain-wipeout",
                        Map.of("node", nodeLabel, "rect", state.instance().rect(i).id(),
                                "against", state.instance().rect(j).id()));
                return new Conflict(i, "domain of '" + state.instance().rect(i).id()
                        + "' wiped out against '" + state.instance().rect(j).id() + "'", atRoot && state.externallyForced());
            }
            for (int k = 0; k < n; k++) {
                if (k != i && k != j && state.activeRequired(k)) {
                    queue.add(new int[]{k, i});
                }
            }
        }
        trace.emit("propagate", "fixpoint",
                Map.of("node", nodeLabel, "pairChecks", stats.pairChecks,
                        "prunesSoFar", stats.prunes));
        return null;
    }

    private static Revision revise(SearchState state, int i, int j, Stats stats,
                                   String nodeLabel, Trace trace) {
        RectDef ri = state.instance().rect(i);
        RectDef rj = state.instance().rect(j);
        Set<Long> dj = state.domain(j);
        Set<Long> di = state.domain(i);
        List<Placement> removed = new ArrayList<>();
        Set<Long> kept = new LinkedHashSet<>();

        for (long codeI : di) {
            stats.incChecks();
            Placement pi = Placement.decode(codeI);
            if (hasSupport(ri, pi, rj, dj, state, stats)) {
                kept.add(codeI);
            } else {
                removed.add(pi);
            }
        }
        if (removed.isEmpty()) {
            return null;
        }
        state.domain(i).retainAll(kept);
        return new Revision(removed);
    }

    /**
     * Support test. The crucial existence guard is in the caller (only TRUE
     * variables reach this code). Kept here as an explicit assertion-style
     * check so the contract cannot be silently bypassed by future callers.
     */
    private static boolean hasSupport(RectDef ri, Placement pi, RectDef rj, Set<Long> dj,
                                      SearchState state, Stats stats) {
        int j = state.instance().indexOf(rj.id());
        if (state.existence(j) == Existence.FALSE) {
            return true;
        }
        if (state.existence(j) == Existence.UNKNOWN) {
            throw new IllegalStateException(
                    "kernel bug: UNKNOWN rectangle '" + rj.id()
                            + "' must never drive strong pruning");
        }
        if (ri.isZeroArea() || rj.isZeroArea()) {
            return true;
        }
        for (long codeJ : dj) {
            stats.incChecks();
            Placement pj = Placement.decode(codeJ);
            if (!Geometry.overlaps(ri, pi, rj, pj)) {
                return true;
            }
        }
        return false;
    }

    private record Revision(List<Placement> removed) {
    }
}
