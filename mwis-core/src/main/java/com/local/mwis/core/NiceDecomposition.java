package com.local.mwis.core;

import java.util.Collections;
import java.util.List;

/** A nice tree decomposition: immutable node list plus the root id (root bag is empty). */
public final class NiceDecomposition {

    private final List<NiceNode> nodes;
    private final int root;

    public NiceDecomposition(List<NiceNode> nodes, int root) {
        this.nodes = Collections.unmodifiableList(nodes);
        this.root = root;
    }

    public List<NiceNode> nodes() {
        return nodes;
    }

    public NiceNode node(int id) {
        return nodes.get(id);
    }

    public int root() {
        return root;
    }

    public int size() {
        return nodes.size();
    }

    public int maxBagSize() {
        int max = 0;
        for (NiceNode n : nodes) {
            max = Math.max(max, n.bag.length);
        }
        return max;
    }
}
