package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.PlacedRect;
import com.example.nonoverlap.api.Solution;
import com.example.nonoverlap.model.Existence;
import com.example.nonoverlap.model.Instance;
import com.example.nonoverlap.model.Placement;
import com.example.nonoverlap.model.RectDef;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Mutable solving state: one existence flag and one placement domain per rectangle.
 *
 * <p>A shallow copy copies the existence array and replaces each touched domain
 * with a fresh set, so branch nodes are independent and backtracking is a simple
 * reference swap.
 */
public final class SearchState {

    private final Instance instance;
    private final Existence[] exists;
    private final Set<Long>[] domains;
    private final boolean externallyForced;

    @SuppressWarnings("unchecked")
    public SearchState(Instance instance, Map<String, ? extends Iterable<Placement>> initialDomains) {
        this.externallyForced = initialDomains != null && initialDomains.values().stream()
                .anyMatch(it -> it.iterator().hasNext());
        this.instance = instance;
        int n = instance.size();
        this.exists = new Existence[n];
        this.domains = new Set[n];
        Map<String, Iterable<Placement>> supplied = initialDomains == null
                ? Map.of() : new HashMap<>((Map<String, ? extends Iterable<Placement>>) initialDomains);
        for (int i = 0; i < n; i++) {
            RectDef r = instance.rect(i);
            exists[i] = r.existence();
            Iterable<Placement> values = supplied.get(r.id());
            Set<Long> d = new LinkedHashSet<>();
            if (values != null) {
                int maxX = instance.gridWidth() - r.width();
                int maxY = instance.gridHeight() - r.height();
                for (Placement p : values) {
                    if (p.x() < 0 || p.y() < 0 || p.x() > maxX || p.y() > maxY) {
                        throw new IllegalArgumentException(
                                "preassignment of '" + r.id() + "' " + p
                                        + " is outside the grid");
                    }
                    d.add(p.code());
                }
            } else {
                for (Placement p : instance.fullDomain(i)) {
                    d.add(p.code());
                }
            }
            domains[i] = d;
        }
    }

    private SearchState(Instance instance, Existence[] exists, Set<Long>[] domains,
                        boolean externallyForced) {
        this.instance = instance;
        this.exists = exists;
        this.domains = domains;
        this.externallyForced = externallyForced;
    }

    public Instance instance() {
        return instance;
    }

    public int size() {
        return exists.length;
    }

    public Existence existence(int i) {
        return exists[i];
    }

    public Set<Long> domain(int i) {
        return domains[i];
    }

    public int domainSize(int i) {
        return domains[i].size();
    }

    /** @return true only when this rectangle is committed to existence. */
    public boolean activeRequired(int i) {
        return exists[i] == Existence.TRUE;
    }

    /**
     * The rule that prevents the silent boundary bug: a rectangle constrains
     * another only while it is necessarily present. UNKNOWN rectangles answer
     * false here, so they never cause strong pruning.
     */
    public boolean externallyForced() {
        return externallyForced;
    }

    public boolean canStronglyPrune(int i) {
        return exists[i] == Existence.TRUE;
    }

    public void setExistence(int i, Existence e) {
        exists[i] = e;
    }

    public void restrictTo(int i, long code) {
        Set<Long> d = new LinkedHashSet<>(1);
        d.add(code);
        domains[i] = d;
    }

    /** Independent copy for a DFS branch. */
    public SearchState copy() {
        Existence[] e = exists.clone();
        @SuppressWarnings("unchecked")
        Set<Long>[] d = new Set[domains.length];
        for (int i = 0; i < d.length; i++) {
            d[i] = new LinkedHashSet<>(domains[i]);
        }
        return new SearchState(instance, e, d, externallyForced);
    }

    /** @return next undecided rectangle index, or -1 if none */
    public int selectUndecided() {
        for (int i = 0; i < exists.length; i++) {
            if (exists[i] == Existence.UNKNOWN) {
                return i;
            }
        }
        return -1;
    }

    /** @return required TRUE rectangle with &gt;1 placements, smallest domain first, or -1 */
    public int selectRequiredVariable() {
        int best = -1;
        int bestSize = Integer.MAX_VALUE;
        for (int i = 0; i < exists.length; i++) {
            int s = domains[i].size();
            if (exists[i] == Existence.TRUE && s > 1 && s < bestSize) {
                best = i;
                bestSize = s;
            }
        }
        return best;
    }

    public boolean allSettled() {
        for (int i = 0; i < exists.length; i++) {
            if (exists[i] != Existence.TRUE && exists[i] != Existence.FALSE) {
                return false;
            }
            if (exists[i] == Existence.TRUE && domains[i].size() != 1) {
                return false;
            }
        }
        return true;
    }

    /** Builds a solution; legal only when {@link #allSettled()}. */
    public Solution toSolution() {
        List<PlacedRect> out = new ArrayList<>(exists.length);
        Map<String, Integer> idx = new HashMap<>();
        for (int i = 0; i < exists.length; i++) {
            RectDef r = instance.rect(i);
            idx.put(r.id(), i);
            if (exists[i] == Existence.TRUE) {
                Placement p = Placement.decode(domains[i].iterator().next());
                out.add(new PlacedRect(r.id(), p.x(), p.y(), r.width(), r.height(), Existence.TRUE));
            } else {
                out.add(new PlacedRect(r.id(), -1, -1, r.width(), r.height(), Existence.FALSE));
            }
        }
        return new Solution(List.copyOf(out), Map.copyOf(idx));
    }
}
