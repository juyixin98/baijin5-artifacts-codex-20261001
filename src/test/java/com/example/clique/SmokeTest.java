package com.example.clique;

import com.example.clique.graph.Graph;
import com.example.clique.search.CliqueEnumerator;
import com.example.clique.search.EnumerationLimits;
import com.example.clique.search.Strategy;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** Minimal vertical slice: triangle must yield exactly one maximal clique. */
class SmokeTest {

    @Test
    void triangleYieldsSingleMaximalClique() {
        Graph triangle = Graph.builder(3).addEdge(0, 1).addEdge(1, 2).addEdge(0, 2).build();
        CliqueEnumerator enumerator = new CliqueEnumerator();
        for (Strategy strategy : Strategy.values()) {
            List<BigInteger> cliques = enumerator.enumerateMaximal(
                    triangle, strategy, EnumerationLimits.unlimited());
            assertEquals(Set.of(BigInteger.valueOf(0b111)), Set.copyOf(cliques),
                    "strategy " + strategy);
        }
    }
}
