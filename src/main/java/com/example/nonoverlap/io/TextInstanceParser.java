package com.example.nonoverlap.io;

import com.example.nonoverlap.model.Existence;
import com.example.nonoverlap.model.Instance;
import com.example.nonoverlap.model.Placement;
import com.example.nonoverlap.model.RectDef;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.Reader;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Parses the documented plain-text instance format:
 * <pre>
 * name &lt;name&gt;
 * grid &lt;width&gt; &lt;height&gt;
 * rect &lt;id&gt; &lt;width&gt; &lt;height&gt; &lt;TRUE|FALSE|UNKNOWN&gt;
 * at   &lt;id&gt; &lt;x&gt; &lt;y&gt;          # optional preassignment
 * </pre>
 * Lines starting with '#' and blank lines are ignored.
 */
public final class TextInstanceParser {

    public static final class Parsed {
        public final Instance instance;
        public final Map<String, List<Placement>> preassign;

        Parsed(Instance instance, Map<String, List<Placement>> preassign) {
            this.instance = instance;
            this.preassign = preassign;
        }
    }

    public Parsed parse(Path file) throws IOException {
        return parse(Files.newBufferedReader(file, StandardCharsets.UTF_8), file.getFileName().toString());
    }

    public Parsed parseString(String text, String name) {
        try {
            return parse(new StringReader(text), name);
        } catch (IOException e) {
            throw new IllegalStateException("string reader cannot fail", e);
        }
    }

    public Parsed parse(Reader reader, String defaultName) throws IOException {
        String name = defaultName;
        int gridW = -1;
        int gridH = -1;
        List<RectDef> rects = new ArrayList<>();
        Map<String, List<Placement>> preassign = new LinkedHashMap<>();
        boolean gridSeen = false;

        int lineNo = 0;
        try (BufferedReader br = new BufferedReader(reader)) {
            String raw;
            while ((raw = br.readLine()) != null) {
                lineNo++;
                String line = stripComment(raw).trim();
                if (line.isEmpty()) {
                    continue;
                }
                String[] t = line.split("\\s+");
                switch (t[0]) {
                    case "name" -> {
                        expect(lineNo, t, 2, "name <id>");
                        name = t[1];
                    }
                    case "grid" -> {
                        expect(lineNo, t, 3, "grid <width> <height>");
                        gridW = parseInt(lineNo, t[1], "width");
                        gridH = parseInt(lineNo, t[2], "height");
                        if (gridW <= 0 || gridH <= 0) {
                            throw new InstanceFormatException(lineNo,
                                    "grid size must be positive, got " + gridW + "x" + gridH);
                        }
                        gridSeen = true;
                    }
                    case "rect" -> {
                        expect(lineNo, t, 5, "rect <id> <width> <height> <TRUE|FALSE|UNKNOWN>");
                        int w = parseInt(lineNo, t[2], "width");
                        int h = parseInt(lineNo, t[3], "height");
                        if (w < 0 || h < 0) {
                            throw new InstanceFormatException(lineNo,
                                    "rectangle '" + t[1] + "' has negative size " + w + "x" + h);
                        }
                        Existence ex = parseExistence(lineNo, t[4]);
                        rects.add(new RectDef(t[1], w, h, ex));
                    }
                    case "at" -> {
                        expect(lineNo, t, 4, "at <id> <x> <y>");
                        int x = parseInt(lineNo, t[2], "x");
                        int y = parseInt(lineNo, t[3], "y");
                        preassign.computeIfAbsent(t[1], k -> new ArrayList<>())
                                .add(new Placement(x, y));
                    }
                    default -> throw new InstanceFormatException(lineNo,
                            "unknown directive '" + t[0] + "'");
                }
            }
        }
        if (!gridSeen) {
            throw new InstanceFormatException(lineNo + 1, "missing 'grid <width> <height>'");
        }
        if (rects.isEmpty()) {
            throw new InstanceFormatException(lineNo + 1, "at least one 'rect' line is required");
        }
        Instance instance = new Instance(name, gridW, gridH, rects);
        for (String id : preassign.keySet()) {
            try {
                instance.indexOf(id);
            } catch (IllegalArgumentException e) {
                throw new InstanceFormatException(lineNo, "'at' refers to unknown rect '" + id + "'");
            }
        }
        return new Parsed(instance, preassign);
    }

    private static String stripComment(String line) {
        int hash = line.indexOf('#');
        return hash < 0 ? line : line.substring(0, hash);
    }

    private static void expect(int line, String[] tokens, int n, String grammar) {
        if (tokens.length != n) {
            throw new InstanceFormatException(line,
                    "expected '" + grammar + "', got " + tokens.length + " tokens");
        }
    }

    private static int parseInt(int line, String s, String field) {
        try {
            return Integer.parseInt(s);
        } catch (NumberFormatException e) {
            throw new InstanceFormatException(line, field + " must be an integer, got '" + s + "'");
        }
    }

    private static Existence parseExistence(int line, String s) {
        return switch (s.toUpperCase()) {
            case "TRUE", "T", "REQUIRED", "1" -> Existence.TRUE;
            case "FALSE", "F", "ABSENT", "0" -> Existence.FALSE;
            case "UNKNOWN", "U", "OPTIONAL", "?" -> Existence.UNKNOWN;
            default -> throw new InstanceFormatException(line,
                    "existence must be TRUE|FALSE|UNKNOWN, got '" + s + "'");
        };
    }
}
