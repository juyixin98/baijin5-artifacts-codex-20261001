package com.local.mwis.service;

import com.local.mwis.graph.Edge;
import com.local.mwis.graph.Graph;
import com.local.mwis.graph.TreeDecomposition;

import java.math.BigInteger;
import java.util.List;

/**
 * Immutable solve request. {@code requestId} is caller-supplied identity echoed into
 * every log line and the report, so concurrent runs stay attributable.
 */
public record SolveRequest(
        String requestId,
        Graph graph,
        BigInteger[] weights,
        TreeDecomposition decomposition,
        Long tableEntryBudget,
        boolean runExhaustiveCrossCheck) {

    public SolveRequest {
        if (requestId == null || requestId.isBlank()) {
            throw new IllegalArgumentException("requestId must be non-empty");
        }
    }

    public static SolveRequest of(String requestId, int vertexCount, List<Edge> edges,
                                  BigInteger[] weights, List<List<Integer>> bags,
                                  List<Edge> treeEdges, int root,
                                  Long tableEntryBudget, boolean crossCheck) {
        return new SolveRequest(requestId, Graph.of(vertexCount, edges), weights,
                TreeDecomposition.of(bags, treeEdges, root), tableEntryBudget, crossCheck);
    }
}
