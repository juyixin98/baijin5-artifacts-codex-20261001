package com.local.mwis.core;

import com.local.mwis.graph.Edge;
import com.local.mwis.graph.TreeDecomposition;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Deque;
import java.util.List;
import java.util.SortedSet;
import java.util.TreeSet;

/**
 * Converts a valid {@link TreeDecomposition} into a nice one (LEAF / INTRODUCE /
 * FORGET / JOIN nodes, empty root bag). The conversion is the standard textbook one:
 * every original bag becomes a small chain of forget/introduce nodes, and branching
 * is expressed with binary JOIN nodes whose bag equals the parent's bag.
 */
public final class NiceDecompositionBuilder {

    private final List<NiceNode> nodes = new ArrayList<>();

    public NiceDecomposition build(TreeDecomposition td) {
        nodes.clear();
        List<List<Integer>> treeAdj = treeAdjacency(td);
        int[] parent = new int[td.nodeCount()];
        Arrays.fill(parent, -2);
        // iterative DFS from the root to fix a processing order
        List<Integer> order = new ArrayList<>();
        Deque<Integer> stack = new ArrayDeque<>();
        stack.push(td.root());
        parent[td.root()] = -1;
        while (!stack.isEmpty()) {
            int cur = stack.pop();
            order.add(cur);
            for (int nxt : treeAdj.get(cur)) {
                if (nxt != parent[cur]) {
                    parent[nxt] = cur;
                    stack.push(nxt);
                }
            }
        }
        int topOfRoot = buildNode(td, td.root(), parent, treeAdj);
        // forget everything remaining so the root bag is empty
        int top = topOfRoot;
        int[] rootBag = nodes.get(top).bag;
        for (int i = rootBag.length - 1; i >= 0; i--) {
            int v = rootBag[i];
            int[] smaller = new int[i];
            System.arraycopy(rootBag, 0, smaller, 0, i);
            int pos = indexOf(rootBag, v);
            top = add(NiceNode.forget(nextId(), smaller, top, v, pos));
            rootBag = smaller;
        }
        return new NiceDecomposition(nodes, top);
    }

    /** Builds the nice subtree for tdNode; returns the top node whose bag equals bag(tdNode). */
    private int buildNode(TreeDecomposition td, int tdNode, int[] parent, List<List<Integer>> treeAdj) {
        int[] myBag = sorted(td.bag(tdNode));
        List<Integer> alignedChildren = new ArrayList<>();
        for (int childTd : treeAdj.get(tdNode)) {
            if (childTd == parent[tdNode]) {
                continue;
            }
            int childTop = buildNode(td, childTd, parent, treeAdj);
            alignedChildren.add(align(childTop, myBag));
        }
        if (alignedChildren.isEmpty()) {
            // leaf in the original tree: start from an empty leaf and introduce up
            int top = add(NiceNode.leaf(nextId()));
            return introduceChain(top, new int[0], myBag);
        }
        int top = alignedChildren.get(0);
        for (int i = 1; i < alignedChildren.size(); i++) {
            top = add(NiceNode.join(nextId(), myBag, top, alignedChildren.get(i)));
        }
        return top;
    }

    /** Rewrites childTop's bag into targetBag via forgets (shrink) then introduces (grow). */
    private int align(int childTop, int[] targetBag) {
        int top = childTop;
        int[] cur = nodes.get(top).bag;
        // forget vertices not needed anymore
        for (int v : cur) {
            if (indexOf(targetBag, v) < 0) {
                int pos = indexOf(cur, v);
                int[] smaller = removeAt(cur, pos);
                top = add(NiceNode.forget(nextId(), smaller, top, v, pos));
                cur = smaller;
            }
        }
        // introduce missing vertices
        return introduceChain(top, cur, targetBag);
    }

    private int introduceChain(int top, int[] fromBag, int[] toBag) {
        int[] cur = fromBag;
        for (int v : toBag) {
            if (indexOf(cur, v) >= 0) {
                continue;
            }
            int[] grown = insertSorted(cur, v);
            int pos = indexOf(grown, v);
            top = add(NiceNode.introduce(nextId(), grown, top, v, pos));
            cur = grown;
        }
        return top;
    }

    private int add(NiceNode node) {
        nodes.add(node);
        return node.id;
    }

    private int nextId() {
        return nodes.size();
    }

    private static List<List<Integer>> treeAdjacency(TreeDecomposition td) {
        List<List<Integer>> adj = new ArrayList<>(td.nodeCount());
        for (int i = 0; i < td.nodeCount(); i++) {
            adj.add(new ArrayList<>());
        }
        for (Edge e : td.treeEdges()) {
            adj.get(e.u()).add(e.v());
            adj.get(e.v()).add(e.u());
        }
        return adj;
    }

    private static int[] sorted(List<Integer> bag) {
        SortedSet<Integer> s = new TreeSet<>(bag);
        int[] out = new int[s.size()];
        int i = 0;
        for (int v : s) {
            out[i++] = v;
        }
        return out;
    }

    static int indexOf(int[] bag, int v) {
        return Arrays.binarySearch(bag, v) >= 0 ? Arrays.binarySearch(bag, v) : -1;
    }

    static int[] removeAt(int[] bag, int pos) {
        int[] out = new int[bag.length - 1];
        System.arraycopy(bag, 0, out, 0, pos);
        System.arraycopy(bag, pos + 1, out, pos, bag.length - pos - 1);
        return out;
    }

    static int[] insertSorted(int[] bag, int v) {
        int[] out = new int[bag.length + 1];
        int pos = 0;
        while (pos < bag.length && bag[pos] < v) {
            out[pos] = bag[pos];
            pos++;
        }
        out[pos] = v;
        System.arraycopy(bag, pos, out, pos + 1, bag.length - pos);
        return out;
    }
}
