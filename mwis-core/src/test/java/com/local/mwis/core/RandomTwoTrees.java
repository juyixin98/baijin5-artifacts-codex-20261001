package com.local.mwis.core;

import com.local.mwis.graph.Edge;
import com.local.mwis.graph.Graph;
import com.local.mwis.graph.TreeDecomposition;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

/**
 * Seeded random partial 2-trees together with their natural width-2 decomposition.
 * Test-local data generation; nothing here is used by production code.
 */
final class RandomTwoTrees {

    record Instance(Graph graph, TreeDecomposition decomposition) {
    }

    static Instance generate(int n, long seed) {
        Random rng = new Random(seed);
        List<Edge> edges = new ArrayList<>();
        List<List<Integer>> bags = new ArrayList<>();
        List<Edge> treeEdges = new ArrayList<>();
        edges.add(Edge.of(0, 1));
        bags.add(List.of(0, 1));
        for (int v = 2; v < n; v++) {
            int host = rng.nextInt(bags.size());
            List<Integer> bag = bags.get(host);
            int u = bag.get(rng.nextInt(bag.size()));
            int w;
            do {
                w = bag.get(rng.nextInt(bag.size()));
            } while (w == u);
            edges.add(Edge.of(u, v));
            edges.add(Edge.of(w, v));
            List<Integer> tri = new ArrayList<>(List.of(u, w, v));
            tri.sort(Integer::compareTo);
            bags.add(tri);
            treeEdges.add(Edge.of(host, bags.size() - 1));
        }
        return new Instance(Graph.of(n, edges),
                TreeDecomposition.of(bags, treeEdges, rng.nextInt(bags.size())));
    }

    private RandomTwoTrees() {
    }
}
