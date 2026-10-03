package com.example.coloring.io;

import com.example.coloring.model.GraphFormatException;
import com.example.coloring.model.Graph;
import com.example.coloring.model.SimpleGraph;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.Reader;
import java.io.StringReader;
import java.io.UncheckedIOException;
import java.io.Writer;
import java.util.List;

/**
 * Tiny human-readable text format for local synthetic graph fixtures.
 *
 * <pre>
 * # comment
 * n 6
 * e 0 1
 * e 1 2
 * </pre>
 *
 * <p>First non-comment line must be {@code n <non-negative vertex count>}.
 * Subsequent {@code e <u> <v>} lines add undirected edges. Blank lines and
 * comments starting with {@code #} are ignored.
 */
public final class GraphText {

    private GraphText() {
    }

    public static SimpleGraph read(String text) {
        return read(new StringReader(text));
    }

    public static SimpleGraph read(Reader reader) {
        try (BufferedReader br = new BufferedReader(reader)) {
            Integer n = null;
            SimpleGraph.Builder builder = null;
            int lineNo = 0;
            String raw;
            while ((raw = br.readLine()) != null) {
                lineNo++;
                String line = stripComment(raw).trim();
                if (line.isEmpty()) {
                    continue;
                }
                String[] parts = line.split("\\s+");
                switch (parts[0]) {
                    case "n" -> {
                        if (n != null) {
                            throw new GraphFormatException("duplicate vertex-count declaration", lineNo);
                        }
                        if (parts.length != 2) {
                            throw new GraphFormatException("expected 'n <count>'", lineNo);
                        }
                        n = parseVertexCount(parts[1], lineNo);
                        builder = new SimpleGraph.Builder(n);
                    }
                    case "e" -> {
                        if (builder == null) {
                            throw new GraphFormatException("edge declared before 'n'", lineNo);
                        }
                        if (parts.length != 3) {
                            throw new GraphFormatException("expected 'e <u> <v>'", lineNo);
                        }
                        int u = parseEndpoint(parts[1], n, lineNo);
                        int v = parseEndpoint(parts[2], n, lineNo);
                        try {
                            builder.addEdge(u, v);
                        } catch (IllegalArgumentException ex) {
                            throw new GraphFormatException(ex.getMessage(), lineNo);
                        }
                    }
                    default -> throw new GraphFormatException("unknown directive '" + parts[0] + "'", lineNo);
                }
            }
            if (n == null) {
                throw new GraphFormatException("missing 'n <count>' declaration", lineNo);
            }
            return builder.build();
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
    }

    public static void write(Graph graph, Writer writer) {
        try {
            writer.write("# synthetic simple-graph fixture (GraphText v1)\n");
            writer.write("n " + graph.n() + "\n");
            for (int u = 0; u < graph.n(); u++) {
                for (int v : graph.neighbors(u)) {
                    if (u < v) {
                        writer.write("e " + u + " " + v + "\n");
                    }
                }
            }
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
    }

    public static String write(Graph graph) {
        java.io.StringWriter sw = new java.io.StringWriter();
        write(graph, sw);
        return sw.toString();
    }

    private static String stripComment(String line) {
        int hash = line.indexOf('#');
        return hash < 0 ? line : line.substring(0, hash);
    }

    private static int parseVertexCount(String token, int lineNo) {
        try {
            int value = Integer.parseInt(token);
            if (value < 0) {
                throw new GraphFormatException("vertex count must be non-negative", lineNo);
            }
            return value;
        } catch (NumberFormatException ex) {
            throw new GraphFormatException("invalid vertex count '" + token + "'", lineNo);
        }
    }

    private static int parseEndpoint(String token, int n, int lineNo) {
        try {
            int value = Integer.parseInt(token);
            if (value < 0 || value >= n) {
                throw new GraphFormatException(
                        "endpoint " + value + " out of range 0.." + (n - 1), lineNo);
            }
            return value;
        } catch (NumberFormatException ex) {
            throw new GraphFormatException("invalid endpoint '" + token + "'", lineNo);
        }
    }
}
