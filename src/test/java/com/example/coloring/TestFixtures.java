package com.example.coloring;

import com.example.coloring.io.GraphText;
import com.example.coloring.model.SimpleGraph;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;

/** Loads the versioned synthetic graph fixtures shipped under test resources. */
public final class TestFixtures {

    public record NamedGraph(String name, SimpleGraph graph, int chromatic, int cliqueNumber) {
    }

    private TestFixtures() {
    }

    public static SimpleGraph load(String resourceName) {
        String path = "/fixtures/" + resourceName;
        try (InputStream in = TestFixtures.class.getResourceAsStream(path)) {
            if (in == null) {
                throw new IllegalStateException("missing fixture " + path);
            }
            return GraphText.read(new String(in.readAllBytes(), StandardCharsets.UTF_8));
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
    }

    /** Every fixture with an independently hand-verified expected chromatic number. */
    public static java.util.List<NamedGraph> all() {
        return java.util.List.of(
                new NamedGraph("c4", load("c4.graph"), 2, 2),
                new NamedGraph("c5", load("c5.graph"), 3, 2),
                new NamedGraph("c9", load("c9.graph"), 3, 2),
                new NamedGraph("k6", load("k6.graph"), 6, 6),
                new NamedGraph("k3-plus-k2", load("k3-plus-k2.graph"), 3, 3),
                new NamedGraph("c5-plus-k4", load("c5-plus-k4.graph"), 4, 4),
                new NamedGraph("w5", load("w5.graph"), 3, 3),
                new NamedGraph("w6", load("w6.graph"), 4, 3),
                new NamedGraph("petersen", load("petersen.graph"), 3, 2),
                new NamedGraph("grotzsch", load("grotzsch.graph"), 4, 2));
    }
}
