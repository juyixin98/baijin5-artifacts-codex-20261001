package com.local.mwis.graph;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Verifies that a {@link TreeDecomposition} is a valid decomposition of a {@link Graph}:
 *
 * <ol>
 *   <li>structural sanity: node ids in range, root in range, no duplicate vertices in a bag;</li>
 *   <li>the bag adjacency forms a tree (connected and acyclic);</li>
 *   <li>every graph edge is contained in at least one bag (edge coverage);</li>
 *   <li>for every vertex, the bags containing it form a connected subtree (running intersection).</li>
 * </ol>
 *
 * All problems are collected and returned; nothing is thrown for invalid input.
 */
public final class DecompositionValidator {

    public ValidationResult validate(Graph graph, TreeDecomposition td) {
        List<ValidationFailure> failures = new ArrayList<>();
        int nodes = td.nodeCount();

        if (nodes == 0) {
            failures.add(new ValidationFailure(
                    FailureCategory.EMPTY_DECOMPOSITION,
                    "decomposition has no bags",
                    "graph vertices=" + graph.vertexCount()));
            return ValidationResult.of(failures);
        }

        validateStructure(graph, td, failures);
        boolean treeShapeOk = validateTreeShape(td, failures);
        if (treeShapeOk) {
            // Coverage and connectivity are only meaningful on a well-formed tree.
            validateEdgeCoverage(graph, td, failures);
            validateVertexConnectivity(graph, td, failures);
        }
        return failures.isEmpty() ? ValidationResult.ok() : ValidationResult.of(failures);
    }

    private void validateStructure(Graph graph, TreeDecomposition td, List<ValidationFailure> failures) {
        int nodes = td.nodeCount();
        if (td.root() < 0 || td.root() >= nodes) {
            failures.add(new ValidationFailure(
                    FailureCategory.DECOMPOSITION_STRUCTURE_INVALID,
                    "root id out of range",
                    "root=" + td.root() + " nodeCount=" + nodes));
        }
        Set<Edge> seenTreeEdges = new HashSet<>();
        for (Edge e : td.treeEdges()) {
            if (e.u() >= nodes || e.v() >= nodes) {
                failures.add(new ValidationFailure(
                        FailureCategory.DECOMPOSITION_STRUCTURE_INVALID,
                        "tree edge references unknown bag id",
                        e + " nodeCount=" + nodes));
            }
            if (!seenTreeEdges.add(e)) {
                failures.add(new ValidationFailure(
                        FailureCategory.DECOMPOSITION_STRUCTURE_INVALID,
                        "duplicate tree edge",
                        e.toString()));
            }
        }
        for (int b = 0; b < nodes; b++) {
            Set<Integer> seen = new HashSet<>();
            for (int v : td.bag(b)) {
                if (v < 0 || v >= graph.vertexCount()) {
                    failures.add(new ValidationFailure(
                            FailureCategory.BAG_VERTEX_OUT_OF_RANGE,
                            "bag references vertex outside the graph",
                            "bag=" + b + " vertex=" + v + " vertexCount=" + graph.vertexCount()));
                }
                if (!seen.add(v)) {
                    failures.add(new ValidationFailure(
                            FailureCategory.DUPLICATE_VERTEX_IN_BAG,
                            "vertex appears twice in one bag",
                            "bag=" + b + " vertex=" + v));
                }
            }
        }
    }

    /** A forest on k nodes with k-1 edges is a tree; we check connected + edge count. */
    private boolean validateTreeShape(TreeDecomposition td, List<ValidationFailure> failures) {
        int nodes = td.nodeCount();
        List<Edge> edges = td.treeEdges();
        // Only edges with in-range endpoints participate; broken ids were already reported.
        List<List<Integer>> adj = new ArrayList<>(nodes);
        for (int i = 0; i < nodes; i++) {
            adj.add(new ArrayList<>());
        }
        int usableEdges = 0;
        for (Edge e : edges) {
            if (e.u() < nodes && e.v() < nodes) {
                adj.get(e.u()).add(e.v());
                adj.get(e.v()).add(e.u());
                usableEdges++;
            }
        }
        int reached = 0;
        boolean[] seen = new boolean[nodes];
        Deque<Integer> stack = new ArrayDeque<>();
        stack.push(0);
        seen[0] = true;
        while (!stack.isEmpty()) {
            int cur = stack.pop();
            reached++;
            for (int nxt : adj.get(cur)) {
                if (!seen[nxt]) {
                    seen[nxt] = true;
                    stack.push(nxt);
                }
            }
        }
        boolean ok = true;
        if (reached != nodes) {
            failures.add(new ValidationFailure(
                    FailureCategory.DECOMPOSITION_NOT_A_TREE,
                    "bag adjacency is disconnected",
                    "reachable=" + reached + " of " + nodes));
            ok = false;
        }
        if (usableEdges != nodes - 1) {
            failures.add(new ValidationFailure(
                    FailureCategory.DECOMPOSITION_NOT_A_TREE,
                    "bag adjacency is not a tree (edge count must be nodeCount-1)",
                    "edges=" + usableEdges + " nodes=" + nodes));
            ok = false;
        }
        return ok;
    }

    private void validateEdgeCoverage(Graph graph, TreeDecomposition td, List<ValidationFailure> failures) {
        // vertex -> bags containing it
        Map<Integer, Set<Integer>> occurrences = occurrences(td);
        for (Edge e : graph.edges()) {
            Set<Integer> bagsU = occurrences.getOrDefault(e.u(), Set.of());
            boolean covered = false;
            for (int bagId : bagsU) {
                if (occurrences.getOrDefault(e.v(), Set.of()).contains(bagId)) {
                    covered = true;
                    break;
                }
            }
            if (!covered) {
                failures.add(new ValidationFailure(
                        FailureCategory.EDGE_NOT_COVERED,
                        "graph edge is not contained in any bag",
                        "edge=" + e));
            }
        }
    }

    /**
     * Running-intersection property: for each vertex v, the subtree induced by the bags
     * containing v must be connected. On a tree this holds iff
     * (#tree edges inside the occurrence set) == (#occurrences - 1).
     */
    private void validateVertexConnectivity(Graph graph, TreeDecomposition td, List<ValidationFailure> failures) {
        Map<Integer, Set<Integer>> occ = occurrences(td);
        for (int v = 0; v < graph.vertexCount(); v++) {
            Set<Integer> bags = occ.getOrDefault(v, Set.of());
            if (bags.isEmpty()) {
                // Isolated vertices need no bag; covered vertices do. Only flag vertices
                // that appear in an edge but never in a bag - edge coverage already reports
                // that, so here we only test connectivity of non-empty occurrence sets.
                continue;
            }
            if (bags.size() == 1) {
                continue;
            }
            int internalEdges = 0;
            for (Edge e : td.treeEdges()) {
                if (e.u() < td.nodeCount() && e.v() < td.nodeCount()
                        && bags.contains(e.u()) && bags.contains(e.v())) {
                    internalEdges++;
                }
            }
            if (internalEdges != bags.size() - 1) {
                failures.add(new ValidationFailure(
                        FailureCategory.VERTEX_OCCURRENCES_DISCONNECTED,
                        "bags containing vertex do not form a connected subtree",
                        "vertex=" + v + " occurrences=" + bags.size()
                                + " internalTreeEdges=" + internalEdges));
            }
        }
    }

    private Map<Integer, Set<Integer>> occurrences(TreeDecomposition td) {
        Map<Integer, Set<Integer>> occ = new HashMap<>();
        for (int b = 0; b < td.nodeCount(); b++) {
            for (int v : td.bag(b)) {
                occ.computeIfAbsent(v, k -> new HashSet<>()).add(b);
            }
        }
        return occ;
    }
}
