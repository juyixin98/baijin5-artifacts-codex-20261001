package com.example.coloring;

import com.example.coloring.bounds.CliqueCertificate;
import com.example.coloring.bounds.CliqueLowerBound;
import com.example.coloring.bounds.GreedyColorer;
import com.example.coloring.cert.Coloring;
import com.example.coloring.graph.Graph;
import com.example.coloring.graph.Graphs;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class BoundCertificateTest {

    @Test
    void maximumCliqueMatchesKnownValues() {
        assertEquals(0, CliqueLowerBound.maximumClique(Graphs.empty()).size());
        assertEquals(1, CliqueLowerBound.maximumClique(Graphs.edgeless(4)).size());
        assertEquals(5, CliqueLowerBound.maximumClique(Graphs.complete(5)).size());
        assertEquals(2, CliqueLowerBound.maximumClique(Graphs.cycle(5)).size());
        assertEquals(2, CliqueLowerBound.maximumClique(Graphs.petersen()).size());
    }

    @Test
    void cliqueWitnessIsActuallyAClique() {
        Graph graph = Graphs.randomErdosRenyi(8, 1, 2, 42L);
        CliqueCertificate certificate = CliqueLowerBound.maximumClique(graph);
        var vertices = certificate.vertices();
        for (int i = 0; i < vertices.size(); i++) {
            for (int j = i + 1; j < vertices.size(); j++) {
                assertTrue(graph.hasEdge(vertices.get(i), vertices.get(j)),
                        "clique certificate must be pairwise adjacent");
            }
        }
        assertTrue(certificate.provenMaximum());
    }

    @Test
    void greedyColoringIsAlwaysFeasible() {
        Graph[] graphs = {
                Graphs.complete(5), Graphs.cycle(5), Graphs.petersen(),
                Graphs.edgeless(4), Graphs.randomErdosRenyi(8, 1, 3, 7L)
        };
        for (Graph graph : graphs) {
            Coloring coloring = GreedyColorer.color(graph);
            assertTrue(coloring.isProper(graph), "upper bound certificate must be a proper coloring");
        }
    }

    @Test
    void detectsTamperedCertificate() {
        Coloring good = GreedyColorer.color(Graphs.cycle(3));
        assertTrue(good.isProper(Graphs.cycle(3)));
        int[] tampered = good.colors();
        tampered[1] = tampered[0];
        Coloring bad = new Coloring(tampered, good.colorCount());
        assertFalse(bad.isProper(Graphs.cycle(3)));
    }
}
