package com.local.mwis.core;

import com.local.mwis.graph.Graph;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Exact weighted maximum independent set solver via dynamic programming on a nice
 * tree decomposition. Weights are arbitrary {@link BigInteger}s; negative weights are
 * allowed and the empty set (weight 0) is always a feasible fallback.
 *
 * <p>Table keys are bitmasks over the node's bag (bit i = bag[i] selected). Bags are
 * therefore limited to {@link #MAX_BAG_SIZE} vertices. The join rule subtracts the
 * weight of shared selected vertices once, so vertex weights are not double counted
 * when two child tables are merged.
 */
public final class MwisSolver {

    /** Bags must fit into a long bitmask with one spare sign bit. */
    public static final int MAX_BAG_SIZE = 62;

    private final Graph graph;
    private final BigInteger[] weights;
    private final NiceDecomposition nice;
    private final SolverOptions options;

    private final Map<Integer, Map<Long, BigInteger>> tables = new HashMap<>();
    private long tableEntries;
    private long maxTableSize;
    private int nodesProcessed;
    private int leafNodes;
    private int introduceNodes;
    private int forgetNodes;
    private int joinNodes;

    public MwisSolver(Graph graph, BigInteger[] weights, NiceDecomposition nice, SolverOptions options) {
        if (weights.length != graph.vertexCount()) {
            throw new IllegalArgumentException(
                    "weights length " + weights.length + " != vertexCount " + graph.vertexCount());
        }
        this.graph = graph;
        this.weights = weights.clone();
        this.nice = nice;
        this.options = options;
    }

    public SolveResult solve() {
        if (nice.maxBagSize() > MAX_BAG_SIZE) {
            throw new BagTooWideException(nice.maxBagSize(), MAX_BAG_SIZE);
        }
        // post-order traversal: children have smaller ids by construction, but compute
        // an explicit order so the solver does not rely on builder internals
        List<Integer> order = postOrder();
        for (int id : order) {
            computeNode(nice.node(id));
        }
        Map<Long, BigInteger> rootTable = tables.get(nice.root());
        BigInteger optimum = rootTable.getOrDefault(0L, BigInteger.ZERO);
        List<Integer> solution = backtrack(optimum);
        return new SolveResult(optimum, solution, stats());
    }

    private List<Integer> postOrder() {
        List<Integer> order = new ArrayList<>();
        boolean[] seen = new boolean[nice.size()];
        List<Integer> stack = new ArrayList<>();
        stack.add(nice.root());
        while (!stack.isEmpty()) {
            int id = stack.remove(stack.size() - 1);
            if (seen[id]) {
                continue;
            }
            seen[id] = true;
            order.add(id);
            for (int c : nice.node(id).children) {
                stack.add(c);
            }
        }
        // reverse so children precede parents
        List<Integer> reversed = new ArrayList<>(order.size());
        for (int i = order.size() - 1; i >= 0; i--) {
            reversed.add(order.get(i));
        }
        return reversed;
    }

    private void computeNode(NiceNode node) {
        switch (node.kind) {
            case LEAF -> {
                Map<Long, BigInteger> table = newTable(node);
                put(node, table, 0L, BigInteger.ZERO);
                leafNodes++;
            }
            case INTRODUCE -> {
                Map<Long, BigInteger> child = tables.get(node.children[0]);
                Map<Long, BigInteger> table = newTable(node);
                long bagNeighborMask = neighborMaskInBag(node);
                long vBit = 1L << node.vertexPosition;
                BigInteger wv = weights[node.vertex];
                for (Map.Entry<Long, BigInteger> e : child.entrySet()) {
                    // child bits at positions >= p shift up by one: the introduced
                    // vertex takes position p in this node's bag
                    long mask = insertZeroBit(e.getKey(), node.vertexPosition);
                    put(node, table, mask, e.getValue());
                    if ((mask & bagNeighborMask) == 0) {
                        long withV = mask | vBit;
                        BigInteger candidate = e.getValue().add(wv);
                        putMax(node, table, withV, candidate);
                    }
                }
                introduceNodes++;
            }
            case FORGET -> {
                Map<Long, BigInteger> child = tables.get(node.children[0]);
                Map<Long, BigInteger> table = newTable(node);
                int p = node.vertexPosition;
                for (Map.Entry<Long, BigInteger> e : child.entrySet()) {
                    long parentMask = removeBit(e.getKey(), p);
                    putMax(node, table, parentMask, e.getValue());
                }
                forgetNodes++;
            }
            case JOIN -> {
                Map<Long, BigInteger> left = tables.get(node.children[0]);
                Map<Long, BigInteger> right = tables.get(node.children[1]);
                Map<Long, BigInteger> table = newTable(node);
                Map<Long, BigInteger> smaller = left.size() <= right.size() ? left : right;
                Map<Long, BigInteger> larger = smaller == left ? right : left;
                for (Map.Entry<Long, BigInteger> e : smaller.entrySet()) {
                    BigInteger other = larger.get(e.getKey());
                    if (other == null) {
                        continue; // infeasible on one side
                    }
                    // merge rule: shared selected vertices were counted on both sides,
                    // subtract their weight once to avoid double counting
                    BigInteger merged = e.getValue().add(other).subtract(selectedWeight(node.bag, e.getKey()));
                    put(node, table, e.getKey(), merged);
                }
                joinNodes++;
            }
        }
        nodesProcessed++;
        maxTableSize = Math.max(maxTableSize, tables.get(node.id).size());
    }

    private Map<Long, BigInteger> newTable(NiceNode node) {
        Map<Long, BigInteger> table = new HashMap<>();
        tables.put(node.id, table);
        return table;
    }

    private void put(NiceNode node, Map<Long, BigInteger> table, long mask, BigInteger value) {
        table.put(mask, value);
        tableEntries++;
        if (tableEntries > options.tableEntryBudget()) {
            throw new BudgetExceededException(options.tableEntryBudget(), tableEntries, node.id);
        }
    }

    private void putMax(NiceNode node, Map<Long, BigInteger> table, long mask, BigInteger value) {
        BigInteger existing = table.get(mask);
        if (existing == null) {
            put(node, table, mask, value);
        } else if (value.compareTo(existing) > 0) {
            table.put(mask, value);
        }
    }

    /** Bitmask of bag positions adjacent to the introduced vertex. */
    private long neighborMaskInBag(NiceNode introduceNode) {
        long mask = 0;
        int v = introduceNode.vertex;
        int[] bag = introduceNode.bag;
        for (int i = 0; i < bag.length; i++) {
            if (bag[i] != v && graph.adjacent(v, bag[i])) {
                mask |= 1L << i;
            }
        }
        return mask;
    }

    private BigInteger selectedWeight(int[] bag, long mask) {
        BigInteger sum = BigInteger.ZERO;
        for (int i = 0; i < bag.length; i++) {
            if ((mask & (1L << i)) != 0) {
                sum = sum.add(weights[bag[i]]);
            }
        }
        return sum;
    }

    /**
     * Reconstructs one optimal independent set by walking the tables top-down.
     * A vertex's selection decision is read at its INTRODUCE node; forget nodes only
     * pick the child mask consistent with the target value. A set is used because a
     * JOIN node's shared vertices are introduced once per child subtree.
     */
    private List<Integer> backtrack(BigInteger rootValue) {
        java.util.Set<Integer> selected = new java.util.TreeSet<>();
        backtrackNode(nice.root(), 0L, rootValue, selected);
        List<Integer> result = new ArrayList<>(selected);
        // internal consistency: the reconstructed set must reproduce the optimum
        BigInteger check = BigInteger.ZERO;
        for (int v : result) {
            check = check.add(weights[v]);
        }
        if (!check.equals(rootValue)) {
            throw new IllegalStateException(
                    "backtracking inconsistency: set weight " + check + " != table value " + rootValue);
        }
        return result;
    }

    private void backtrackNode(int nodeId, long mask, BigInteger target, java.util.Set<Integer> selected) {
        NiceNode node = nice.node(nodeId);
        switch (node.kind) {
            case LEAF -> {
                // empty bag, nothing to decide
            }
            case INTRODUCE -> {
                long vBit = 1L << node.vertexPosition;
                long childMask = removeBit(mask, node.vertexPosition);
                if ((mask & vBit) != 0) {
                    selected.add(node.vertex);
                    backtrackNode(node.children[0], childMask, target.subtract(weights[node.vertex]), selected);
                } else {
                    backtrackNode(node.children[0], childMask, target, selected);
                }
            }
            case FORGET -> {
                Map<Long, BigInteger> childTable = tables.get(node.children[0]);
                long withoutV = insertZeroBit(mask, node.vertexPosition);
                BigInteger valueWithout = childTable.get(withoutV);
                if (target.equals(valueWithout)) {
                    backtrackNode(node.children[0], withoutV, target, selected);
                    return;
                }
                long withV = withoutV | (1L << node.vertexPosition);
                BigInteger valueWith = childTable.get(withV);
                if (valueWith == null || !target.equals(valueWith)) {
                    throw new IllegalStateException(
                            "backtracking dead end at forget node " + nodeId);
                }
                backtrackNode(node.children[0], withV, target, selected);
            }
            case JOIN -> {
                // both children share this node's bag and mask; each explains its own table value
                BigInteger leftValue = tables.get(node.children[0]).get(mask);
                BigInteger rightValue = tables.get(node.children[1]).get(mask);
                if (leftValue == null || rightValue == null) {
                    throw new IllegalStateException("backtracking dead end at join node " + nodeId);
                }
                backtrackNode(node.children[0], mask, leftValue, selected);
                backtrackNode(node.children[1], mask, rightValue, selected);
            }
        }
    }

    private SolverStats stats() {
        return new SolverStats(nodesProcessed, leafNodes, introduceNodes, forgetNodes,
                joinNodes, tableEntries, maxTableSize, options.tableEntryBudget());
    }

    static long removeBit(long mask, int p) {
        long low = mask & ((1L << p) - 1);
        long high = mask >>> (p + 1);
        return low | (high << p);
    }

    static long insertZeroBit(long mask, int p) {
        long low = mask & ((1L << p) - 1);
        long high = mask & ~((1L << p) - 1);
        return low | (high << 1);
    }
}
