package com.local.mwis.cli;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.local.mwis.graph.Edge;
import com.local.mwis.graph.Graph;
import com.local.mwis.graph.TreeDecomposition;
import com.local.mwis.service.SolveRequest;

import java.io.IOException;
import java.math.BigInteger;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Parses the JSON request format into a {@link SolveRequest}. */
public final class JsonRequestParser {

    private final ObjectMapper mapper = new ObjectMapper();

    public SolveRequest parse(Path file) throws IOException {
        JsonNode root = mapper.readTree(file.toFile());
        String requestId = required(root, "requestId").asText();

        JsonNode graphNode = required(root, "graph");
        int vertexCount = graphNode.required("vertexCount").asInt();
        List<Edge> edges = new ArrayList<>();
        for (JsonNode e : graphNode.required("edges")) {
            edges.add(Edge.of(e.get(0).asInt(), e.get(1).asInt()));
        }
        Graph graph = Graph.of(vertexCount, edges);

        JsonNode weightsNode = required(root, "weights");
        BigInteger[] weights = new BigInteger[weightsNode.size()];
        for (int i = 0; i < weights.length; i++) {
            weights[i] = new BigInteger(weightsNode.get(i).asText());
        }

        JsonNode tdNode = required(root, "decomposition");
        List<List<Integer>> bags = new ArrayList<>();
        for (JsonNode bag : tdNode.required("bags")) {
            List<Integer> b = new ArrayList<>();
            bag.forEach(v -> b.add(v.asInt()));
            bags.add(b);
        }
        List<Edge> treeEdges = new ArrayList<>();
        for (JsonNode e : tdNode.required("treeEdges")) {
            treeEdges.add(Edge.of(e.get(0).asInt(), e.get(1).asInt()));
        }
        int tdRoot = tdNode.required("root").asInt();
        TreeDecomposition td = TreeDecomposition.of(bags, treeEdges, tdRoot);

        Long budget = null;
        boolean crossCheck = false;
        JsonNode options = root.get("options");
        if (options != null) {
            if (options.hasNonNull("tableEntryBudget")) {
                budget = options.get("tableEntryBudget").asLong();
            }
            if (options.hasNonNull("runExhaustiveCrossCheck")) {
                crossCheck = options.get("runExhaustiveCrossCheck").asBoolean();
            }
        }
        return new SolveRequest(requestId, graph, weights, td, budget, crossCheck);
    }

    private static JsonNode required(JsonNode node, String field) {
        JsonNode child = node.get(field);
        if (child == null) {
            throw new IllegalArgumentException("missing required field: " + field);
        }
        return child;
    }
}
