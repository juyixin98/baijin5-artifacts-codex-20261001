package com.example.coloring.bound;

import com.example.coloring.model.Graph;
import java.util.BitSet;

/**
 * Exact maximum clique of a small graph via the Tomita pivoted
 * Bron-Kerbosch branch-and-bound. The returned clique is a witness for the
 * clique lower bound: any proper coloring needs at least clique.size() colors.
 */
public final class MaxClique {

    private final int n;
    private final Graph graph;
    private final BitSet[] neighbors;

    private BitSet best;

    private MaxClique(Graph graph) {
        this.graph = graph;
        this.n = graph.n();
        this.neighbors = new BitSet[n];
        for (int v = 0; v < n; v++) {
            BitSet set = new BitSet(n);
            for (int u : graph.neighbors(v)) {
                set.set(u);
            }
            neighbors[v] = set;
        }
        this.best = new BitSet(n);
    }

    /** Returns a maximum clique as a sorted array of vertex ids (empty array if n == 0). */
    public static int[] find(Graph graph) {
        MaxClique mc = new MaxClique(graph);
        BitSet all = new BitSet(graph.n());
        all.set(0, graph.n());
        mc.expand(new BitSet(graph.n()), all, new BitSet(graph.n()));
        int[] result = new int[mc.best.cardinality()];
        int idx = 0;
        for (int v = mc.best.nextSetBit(0); v >= 0; v = mc.best.nextSetBit(v + 1)) {
            result[idx++] = v;
        }
        return result;
    }

    private void expand(BitSet current, BitSet candidates, BitSet excluded) {
        if (candidates.isEmpty() && excluded.isEmpty()) {
            if (current.cardinality() > best.cardinality()) {
                best = (BitSet) current.clone();
            }
            return;
        }
        if (current.cardinality() + candidates.cardinality() <= best.cardinality()) {
            return;
        }
        int pivot = choosePivot(candidates, excluded);
        BitSet candidatesWithoutPivotNeighbors = (BitSet) candidates.clone();
        if (pivot >= 0) {
            candidatesWithoutPivotNeighbors.andNot(neighbors[pivot]);
        }
        for (int v = first(candidatesWithoutPivotNeighbors); v >= 0;
                v = next(candidatesWithoutPivotNeighbors, v)) {
            BitSet nextCurrent = (BitSet) current.clone();
            nextCurrent.set(v);
            BitSet nextCandidates = (BitSet) candidates.clone();
            nextCandidates.and(neighbors[v]);
            BitSet nextExcluded = (BitSet) excluded.clone();
            nextExcluded.and(neighbors[v]);
            expand(nextCurrent, nextCandidates, nextExcluded);
            candidates.clear(v);
            excluded.set(v);
            candidatesWithoutPivotNeighbors.clear(v);
        }
    }

    private int choosePivot(BitSet candidates, BitSet excluded) {
        BitSet union = (BitSet) candidates.clone();
        union.or(excluded);
        int pivot = -1;
        int bestDegree = -1;
        for (int v = first(union); v >= 0; v = next(union, v)) {
            BitSet inter = (BitSet) candidates.clone();
            inter.and(neighbors[v]);
            int degree = inter.cardinality();
            if (degree > bestDegree) {
                bestDegree = degree;
                pivot = v;
            }
        }
        return pivot;
    }

    private static int first(BitSet set) {
        return set.isEmpty() ? -1 : set.nextSetBit(0);
    }

    private static int next(BitSet set, int from) {
        return set.nextSetBit(from + 1);
    }
}
