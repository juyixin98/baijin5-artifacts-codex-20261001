package com.local.mwis.graph;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Immutable tree decomposition contract: a list of bags (vertex sets) plus the
 * tree adjacency between bag ids. Validity is established separately by
 * {@link DecompositionValidator}; this type only stores the shape.
 */
public final class TreeDecomposition {

    private final List<List<Integer>> bags;       // bag id -> sorted distinct vertex ids
    private final List<Edge> treeEdges;           // adjacency between bag ids
    private final int root;                       // designated root bag id

    private TreeDecomposition(List<List<Integer>> bags, List<Edge> treeEdges, int root) {
        List<List<Integer>> copy = new ArrayList<>(bags.size());
        for (List<Integer> bag : bags) {
            copy.add(Collections.unmodifiableList(new ArrayList<>(bag)));
        }
        this.bags = Collections.unmodifiableList(copy);
        this.treeEdges = Collections.unmodifiableList(new ArrayList<>(treeEdges));
        this.root = root;
    }

    public static TreeDecomposition of(List<List<Integer>> bags, List<Edge> treeEdges, int root) {
        return new TreeDecomposition(bags, treeEdges, root);
    }

    public int nodeCount() {
        return bags.size();
    }

    public List<Integer> bag(int nodeId) {
        return bags.get(nodeId);
    }

    public List<List<Integer>> bags() {
        return bags;
    }

    public List<Edge> treeEdges() {
        return treeEdges;
    }

    public int root() {
        return root;
    }

    /** Widest bag size minus one, the width of this decomposition. */
    public int width() {
        int max = 0;
        for (List<Integer> bag : bags) {
            max = Math.max(max, bag.size());
        }
        return max - 1;
    }
}
