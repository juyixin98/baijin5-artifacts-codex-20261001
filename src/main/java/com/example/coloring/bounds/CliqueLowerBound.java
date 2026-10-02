package com.example.coloring.bounds;

import com.example.coloring.graph.Graph;

import java.util.ArrayList;
import java.util.BitSet;
import java.util.List;

/**
 * Clique lower bound with two modes:
 *
 * <ul>
 *   <li>{@link #maximumClique(Graph)}: pivot Bron-Kerbosch with pivoting; the
 *       returned clique is certified to be a maximum clique, so the lower bound
 *       is exact as a clique bound (it still equals chi only when the graph is
 *       perfect - the solver is what closes that gap).</li>
 *   <li>{@link #greedyClique(Graph)}: deterministic maximal clique used as a
 *       cheap, always-valid lower bound; {@code provenMaximum} is false.</li>
 * </ul>
 */
public final class CliqueLowerBound {

    private CliqueLowerBound() {
    }

    public static CliqueCertificate maximumClique(Graph graph) {
        int n = graph.order();
        List<Integer> best = new ArrayList<>();
        if (n == 0) {
            return new CliqueCertificate(List.of(), true);
        }
        bronKerbosch(new BitSet(), fullSet(n), fullSet(n), graph, best);
        return new CliqueCertificate(List.copyOf(best), true);
    }

    public static CliqueCertificate greedyClique(Graph graph) {
        int n = graph.order();
        List<Integer> clique = new ArrayList<>();
        BitSet allowed = fullSet(n);
        while (!allowed.isEmpty()) {
            int pick = -1;
            int bestDegree = -1;
            for (int v = allowed.nextSetBit(0); v >= 0; v = allowed.nextSetBit(v + 1)) {
                int degree = countIntersection(graph.neighborSet(v), allowed);
                if (degree > bestDegree || (degree == bestDegree && (pick == -1 || v < pick))) {
                    bestDegree = degree;
                    pick = v;
                }
            }
            clique.add(pick);
            allowed.and(graph.neighborSet(pick));
        }
        return new CliqueCertificate(List.copyOf(clique), false);
    }

    private static void bronKerbosch(BitSet r, BitSet p, BitSet x, Graph graph, List<Integer> best) {
        if (p.isEmpty() && x.isEmpty()) {
            if (r.cardinality() > best.size()) {
                best.clear();
                for (int v = r.nextSetBit(0); v >= 0; v = r.nextSetBit(v + 1)) {
                    best.add(v);
                }
            }
            return;
        }
        int pivot = choosePivot(p, x, graph);
        BitSet candidates = (BitSet) p.clone();
        if (pivot >= 0) {
            candidates.andNot(graph.neighborSet(pivot));
        }
        for (int v = candidates.nextSetBit(0); v >= 0; v = candidates.nextSetBit(v + 1)) {
            BitSet nv = graph.neighborSet(v);
            BitSet r2 = (BitSet) r.clone();
            r2.set(v);
            BitSet p2 = (BitSet) p.clone();
            p2.and(nv);
            BitSet x2 = (BitSet) x.clone();
            x2.and(nv);
            bronKerbosch(r2, p2, x2, graph, best);
            p.clear(v);
            x.set(v);
        }
    }

    private static int choosePivot(BitSet p, BitSet x, Graph graph) {
        int pivot = -1;
        int bestCount = -1;
        BitSet union = (BitSet) p.clone();
        union.or(x);
        for (int u = union.nextSetBit(0); u >= 0; u = union.nextSetBit(u + 1)) {
            int count = countIntersection(graph.neighborSet(u), p);
            if (count > bestCount) {
                bestCount = count;
                pivot = u;
            }
        }
        return pivot;
    }

    private static int countIntersection(BitSet a, BitSet b) {
        BitSet copy = (BitSet) a.clone();
        copy.and(b);
        return copy.cardinality();
    }

    private static BitSet fullSet(int n) {
        BitSet set = new BitSet(n);
        set.set(0, n);
        return set;
    }
}
