package com.example.coloring.diag;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.coloring.model.Graphs;
import com.example.coloring.search.ColoringSolver;
import java.io.ByteArrayOutputStream;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;

class DiagnosticsTest {

    @Test
    void everyEventCarriesRequestAndNodeIdentityAndState() {
        CollectingDiagnostics diag = new CollectingDiagnostics();
        new ColoringSolver(diag).solve(Graphs.cycle(5), BigInteger.valueOf(500), "req-xyz");

        List<DiagnosticEvent> events = diag.events();
        assertFalse(events.isEmpty());
        for (DiagnosticEvent event : events) {
            assertEquals("req-xyz", event.requestId());
            assertTrue(event.nodeId() >= 0);
            assertTrue(event.detail().containsKey("uncolored"));
        }
        long firstNode = events.get(0).nodeId();
        assertTrue(events.stream().allMatch(e -> e.nodeId() >= firstNode));
    }

    @Test
    void printedOutputIsMaskedAndStillExplainsAcceptReject() {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        PrintingSink sink = new PrintingSink(new java.io.PrintStream(buffer, true, StandardCharsets.UTF_8));
        new ColoringSolver(sink).solve(Graphs.cycle(5), BigInteger.valueOf(500), "req-mask");

        String output = buffer.toString(StandardCharsets.UTF_8);
        assertTrue(output.contains("req-mask"));
        assertTrue(output.contains("CLIQUE_LOWER_BOUND"));
        assertTrue(output.contains("k="));
        assertTrue(output.contains("budgetRemaining="));
        // Raw vertex ids must never be printed; only salted hashes like v#....
        for (String line : output.split("\\R")) {
            if (line.contains("vertex=v#")) {
                assertFalse(line.matches(".*vertex=(?!v#)-?\\d+.*"));
            }
        }
        assertFalse(output.contains("vertex=0") && output.contains("v#"),
                "raw and masked vertex ids mixed");
    }

    @Test
    void maskedVertexIdsAreStableWithinRequestButDifferAcrossRequests() {
        ByteArrayOutputStream b1 = new ByteArrayOutputStream();
        ByteArrayOutputStream b2 = new ByteArrayOutputStream();
        // C5 at k=2 always emits a symmetry skip at the first decision node, so a
        // masked vertex id is deterministically present in both runs.
        new ColoringSolver(new PrintingSink(new java.io.PrintStream(b1, true, StandardCharsets.UTF_8)))
                .solve(Graphs.cycle(5), BigInteger.valueOf(500), "req-a");
        new ColoringSolver(new PrintingSink(new java.io.PrintStream(b2, true, StandardCharsets.UTF_8)))
                .solve(Graphs.cycle(5), BigInteger.valueOf(500), "req-b");
        String out1 = b1.toString(StandardCharsets.UTF_8);
        String out2 = b2.toString(StandardCharsets.UTF_8);
        String mask1 = firstMask(out1);
        String mask2 = firstMask(out2);
        assertTrue(mask1.startsWith("v#"), () -> "no masked vertex in:\n" + out1);
        assertFalse(mask1.equals(mask2), "per-request salt must change the mask");
    }

    private static String firstMask(String output) {
        for (String token : output.split("\\s+")) {
            if (token.startsWith("vertex=v#")) {
                return token.substring("vertex=".length());
            }
        }
        return "";
    }
}
