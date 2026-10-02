package com.example.coloring.graph;


import java.io.BufferedReader;
import java.io.IOException;
import java.io.Reader;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Loader for the local, human-readable {@code .gfc} fixture format.
 *
 * <pre>
 * # comment
 * graph: name
 * order: n
 * chi: expected chromatic number
 * clique: expected maximum clique size
 * colorings-at-1,2,3: c1 c2 c3   (optional: number of proper colorings P_G(k))
 * edges:
 * 0 1
 * 1 2
 * graph: next ...
 * </pre>
 *
 * <p>All fixtures are hand-authored/synthetic; no external data source is
 * read.</p>
 */
public final class GraphFixture {

    public final String name;
    public final Graph graph;
    public final int expectedChi;
    public final int expectedClique;
    public final Map<Integer, java.math.BigInteger> expectedColorings;

    private GraphFixture(String name, Graph graph, int expectedChi, int expectedClique,
                         Map<Integer, java.math.BigInteger> expectedColorings) {
        this.name = name;
        this.graph = graph;
        this.expectedChi = expectedChi;
        this.expectedClique = expectedClique;
        this.expectedColorings = Map.copyOf(expectedColorings);
    }

    public static List<GraphFixture> loadAll(Reader reader) {
        List<GraphFixture> fixtures = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(reader)) {
            String name = null;
            int order = -1;
            int chi = -1;
            int clique = -1;
            List<int[]> edges = new ArrayList<>();
            Map<Integer, java.math.BigInteger> colorings = new HashMap<>();
            boolean inEdges = false;

            String raw;
            while ((raw = br.readLine()) != null) {
                String line = raw.trim();
                if (line.isEmpty() || line.startsWith("#")) {
                    continue;
                }
                if (line.startsWith("graph:")) {
                    if (name != null) {
                        fixtures.add(build(name, order, chi, clique, edges, colorings));
                    }
                    name = line.substring("graph:".length()).trim();
                    order = -1;
                    chi = -1;
                    clique = -1;
                    edges = new ArrayList<>();
                    colorings = new HashMap<>();
                    inEdges = false;
                } else if (line.startsWith("order:")) {
                    order = Integer.parseInt(after(line));
                    inEdges = false;
                } else if (line.startsWith("chi:")) {
                    chi = Integer.parseInt(after(line));
                    inEdges = false;
                } else if (line.startsWith("clique:")) {
                    clique = Integer.parseInt(after(line));
                    inEdges = false;
                } else if (line.startsWith("colorings-at")) {
                    parseColorings(line, colorings);
                    inEdges = false;
                } else if (line.startsWith("edges:")) {
                    inEdges = true;
                } else if (inEdges) {
                    String[] parts = line.split("\\s+");
                    edges.add(new int[]{Integer.parseInt(parts[0]), Integer.parseInt(parts[1])});
                } else {
                    throw new IllegalArgumentException("unrecognized fixture line: " + line);
                }
            }
            if (name != null) {
                fixtures.add(build(name, order, chi, clique, edges, colorings));
            }
        } catch (IOException e) {
            throw new IllegalArgumentException("failed reading fixture", e);
        }
        return List.copyOf(fixtures);
    }

    private static String after(String line) {
        return line.substring(line.indexOf(':') + 1).trim();
    }

    private static void parseColorings(String line, Map<Integer, java.math.BigInteger> target) {
        int open = line.indexOf('(');
        int close = line.indexOf(')');
        String[] keys = line.substring(open + 1, close).split(",");
        String[] values = after(line).split("\\s+");
        if (keys.length != values.length) {
            throw new IllegalArgumentException("colorings-at key/value count mismatch: " + line);
        }
        for (int i = 0; i < keys.length; i++) {
            target.put(Integer.parseInt(keys[i].trim()),
                    new java.math.BigInteger(values[i].trim()));
        }
    }

    private static GraphFixture build(String name, int order, int chi, int clique,
                                      List<int[]> edges,
                                      Map<Integer, java.math.BigInteger> colorings) {
        if (order < 0 || chi < 0 || clique < 0) {
            throw new IllegalArgumentException("fixture " + name + " is missing order/chi/clique");
        }
        Graph.Builder builder = Graph.builder(order);
        for (int[] edge : edges) {
            builder.edge(edge[0], edge[1]);
        }
        return new GraphFixture(name, builder.build(), chi, clique, colorings);
    }

    @Override
    public String toString() {
        return name + graph;
    }
}
