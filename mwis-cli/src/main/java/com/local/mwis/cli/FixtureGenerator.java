package com.local.mwis.cli;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;

/**
 * Deterministic synthetic fixture writer. All data is generated locally with fixed
 * seeds - no external systems involved. Random instances are partial 2-trees, which
 * come with a naturally valid width-2 decomposition: every new vertex is attached to
 * both endpoints of an edge inside an existing bag, producing one triangle bag.
 */
public final class FixtureGenerator {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    public static void generateAll(Path dir) {
        try {
            Files.createDirectories(dir);
            write(dir.resolve("request-path.json"), pathFixture());
            write(dir.resolve("request-join.json"), joinFixture());
            write(dir.resolve("request-negative.json"), negativeFixture());
            write(dir.resolve("request-random-2tree.json"), randomTwoTreeFixture("fixture-seed-1", 16, 20261004L));
            write(dir.resolve("request-broken-uncovered-edge.json"), brokenUncoveredEdgeFixture());
            write(dir.resolve("request-broken-disconnected.json"), brokenDisconnectedFixture());
            write(dir.resolve("request-tight-budget.json"), tightBudgetFixture());
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** Path 0-1-2-3, weights 3,2,5,1; hand-computed optimum 8 = {0,2}. */
    static ObjectNode pathFixture() {
        ObjectNode req = base("demo-path-4");
        graph(req, 4, new int[][]{{0, 1}, {1, 2}, {2, 3}});
        weights(req, new long[]{3, 2, 5, 1});
        decomposition(req, new int[][]{{0, 1}, {1, 2}, {2, 3}}, new int[][]{{0, 1}, {1, 2}}, 0);
        options(req, null, true);
        return req;
    }

    /** Path of 5 rooted mid-way so the nice decomposition contains a JOIN. */
    static ObjectNode joinFixture() {
        ObjectNode req = base("demo-join-p5");
        graph(req, 5, new int[][]{{0, 1}, {1, 2}, {2, 3}, {3, 4}});
        weights(req, new long[]{1, 1, 1, 1, 1});
        decomposition(req, new int[][]{{0, 1}, {1, 2}, {2, 3}, {3, 4}},
                new int[][]{{0, 1}, {1, 2}, {2, 3}}, 1);
        options(req, null, true);
        return req;
    }

    /** All-negative weights: the empty set (weight 0) must win. */
    static ObjectNode negativeFixture() {
        ObjectNode req = base("demo-negative");
        graph(req, 3, new int[][]{{0, 1}, {1, 2}});
        weights(req, new long[]{-5, -1, -7});
        decomposition(req, new int[][]{{0, 1}, {1, 2}}, new int[][]{{0, 1}}, 0);
        options(req, null, true);
        return req;
    }

    /** Seeded random partial 2-tree with a valid width-2 decomposition. */
    static ObjectNode randomTwoTreeFixture(String requestId, int n, long seed) {
        Random rng = new Random(seed);
        List<int[]> edges = new ArrayList<>();
        List<int[]> bags = new ArrayList<>();
        List<int[]> treeEdges = new ArrayList<>();
        edges.add(new int[]{0, 1});
        bags.add(new int[]{0, 1});
        for (int v = 2; v < n; v++) {
            int hostBag = rng.nextInt(bags.size());
            int[] host = bags.get(hostBag);
            int u = host[rng.nextInt(host.length)];
            int w;
            do {
                w = host[rng.nextInt(host.length)];
            } while (w == u);
            edges.add(new int[]{Math.min(u, v), Math.max(u, v)});
            edges.add(new int[]{Math.min(w, v), Math.max(w, v)});
            int[] tri = {Math.min(Math.min(u, w), v), 0, Math.max(Math.max(u, w), v)};
            tri[1] = u + w + v - tri[0] - tri[2];
            bags.add(tri);
            treeEdges.add(new int[]{hostBag, bags.size() - 1});
        }
        long[] w = new long[n];
        for (int i = 0; i < n; i++) {
            w[i] = rng.nextInt(30) - 9; // [-9, 20], includes negatives
        }
        ObjectNode req = base(requestId);
        graph(req, n, edges.toArray(new int[0][]));
        weights(req, w);
        decomposition(req, bags.toArray(new int[0][]), treeEdges.toArray(new int[0][]), 0);
        options(req, null, true);
        return req;
    }

    /** Triangle graph, but no bag contains edge (1,2): must be rejected. */
    static ObjectNode brokenUncoveredEdgeFixture() {
        ObjectNode req = base("demo-broken-uncovered-edge");
        graph(req, 3, new int[][]{{0, 1}, {1, 2}, {0, 2}});
        weights(req, new long[]{1, 2, 3});
        decomposition(req, new int[][]{{0, 1}, {1, 2}}, new int[][]{{0, 1}}, 0);
        options(req, null, false);
        return req;
    }

    /** Vertex 2 appears in bags 0 and 2 but not in bag 1 between them. */
    static ObjectNode brokenDisconnectedFixture() {
        ObjectNode req = base("demo-broken-disconnected");
        graph(req, 4, new int[][]{{0, 2}, {2, 3}});
        weights(req, new long[]{1, 1, 1, 1});
        decomposition(req, new int[][]{{0, 2}, {1}, {2, 3}},
                new int[][]{{0, 1}, {1, 2}}, 0);
        options(req, null, false);
        return req;
    }

    /** Deliberately tiny budget on a width-2 instance: solver must stop. */
    static ObjectNode tightBudgetFixture() {
        ObjectNode req = randomTwoTreeFixture("demo-tight-budget", 14, 777L);
        options(req, 3L, false);
        return req;
    }

    // ---- JSON assembly helpers ----

    private static ObjectNode base(String requestId) {
        ObjectNode req = MAPPER.createObjectNode();
        req.put("requestId", requestId);
        return req;
    }

    private static void graph(ObjectNode req, int vertexCount, int[][] edges) {
        ObjectNode g = req.putObject("graph");
        g.put("vertexCount", vertexCount);
        ArrayNode es = g.putArray("edges");
        for (int[] e : edges) {
            es.addArray().add(e[0]).add(e[1]);
        }
    }

    private static void weights(ObjectNode req, long[] weights) {
        ArrayNode ws = req.putArray("weights");
        for (long w : weights) {
            ws.add(BigInteger.valueOf(w).toString());
        }
    }

    private static void decomposition(ObjectNode req, int[][] bags, int[][] treeEdges, int root) {
        ObjectNode td = req.putObject("decomposition");
        ArrayNode bs = td.putArray("bags");
        for (int[] bag : bags) {
            ArrayNode b = bs.addArray();
            for (int v : bag) {
                b.add(v);
            }
        }
        ArrayNode tes = td.putArray("treeEdges");
        for (int[] e : treeEdges) {
            tes.addArray().add(e[0]).add(e[1]);
        }
        td.put("root", root);
    }

    private static void options(ObjectNode req, Long budget, boolean crossCheck) {
        ObjectNode o = req.putObject("options");
        if (budget != null) {
            o.put("tableEntryBudget", budget);
        }
        o.put("runExhaustiveCrossCheck", crossCheck);
    }

    private static void write(Path file, ObjectNode node) throws IOException {
        MAPPER.writerWithDefaultPrettyPrinter().writeValue(file.toFile(), node);
    }

    private FixtureGenerator() {
    }
}
