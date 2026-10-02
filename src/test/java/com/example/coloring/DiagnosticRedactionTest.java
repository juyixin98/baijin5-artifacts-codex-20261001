package com.example.coloring;

import com.example.coloring.diag.DiagnosticReport;
import com.example.coloring.diag.RequestContext;
import com.example.coloring.diag.SearchEvent;
import com.example.coloring.graph.Graph;
import com.example.coloring.solver.ChromaticSolver;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DiagnosticRedactionTest {

    private static Graph sensitiveGraph() {
        return Graph.builder(3)
                .label(0, "customer-9001234-SSN")
                .label(1, "customer-9001235-SSN")
                .label(2, "customer-9001236-SSN")
                .edge(0, 1).edge(1, 2)
                .build();
    }

    @Test
    void maskedRenderHidesSensitiveLabels() {
        Graph graph = sensitiveGraph();
        DiagnosticReport report = new DiagnosticReport();
        new ChromaticSolver().solve(graph, RequestContext.of("req-secret", true), report);

        assertTrue(report.events().size() > 0);
        for (SearchEvent event : report.events()) {
            String rendered = event.render(true, graph.labels());
            assertFalse(rendered.contains("customer-"), "rendered log leaks a raw label");
            assertFalse(rendered.contains("SSN"), "rendered log leaks sensitive suffix");
            assertFalse(rendered.contains("9001234"), "rendered log leaks an identifier");
            assertTrue(rendered.contains("h#"), "masked identities must use fingerprints");
            assertTrue(rendered.contains("req-secret"), "correlation id stays visible");
        }
    }

    @Test
    void unmaskedRenderShowsPlaintextForLocalDevelopment() {
        Graph graph = sensitiveGraph();
        DiagnosticReport report = new DiagnosticReport();
        new ChromaticSolver().solve(graph, RequestContext.of("req-open", false), report);
        SearchEvent first = report.events().get(0);
        String rendered = first.render(false, graph.labels());
        assertTrue(rendered.contains("req-open"));
    }

    @Test
    void maskingIsStableAcrossRenders() {
        Graph graph = sensitiveGraph();
        DiagnosticReport report = new DiagnosticReport();
        new ChromaticSolver().solve(graph, RequestContext.of("req-stable", true), report);
        SearchEvent event = report.events().get(0);
        List<String> labels = graph.labels();
        String a = event.render(true, labels);
        String b = event.render(true, labels);
        assertTrue(a.equals(b), "fingerprints must be deterministic");
    }
}
