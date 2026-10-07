package rectkernel.cli;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import rectkernel.error.ErrorCategory;
import rectkernel.error.KernelException;
import rectkernel.model.Domain;
import rectkernel.model.Problem;
import rectkernel.model.RectSpec;

/**
 * Line-based problem file format ('#' starts a comment):
 *   run <runId>                                  (optional)
 *   grid <W> <H>                                 (required, before any rect)
 *   limits <maxNodes> <maxTimeMillis>            (optional)
 *   rect <id> <w> <h> mandatory|optional         (domain defaults to whole grid)
 *   rect <id> <w> <h> mandatory|optional at <x> <y>
 *   rect <id> <w> <h> mandatory|optional domain <xmin> <xmax> <ymin> <ymax>
 * Every malformed line is an INPUT_ERROR carrying the file and line number.
 */
public final class ProblemParser {

    public record Parsed(Problem problem, String runId, Long maxNodes, Long maxTimeMs) {
    }

    private ProblemParser() {
    }

    public static Parsed parse(Path file, String cliRunId) {
        List<String> lines;
        try {
            lines = Files.readAllLines(file);
        } catch (IOException e) {
            throw new KernelException(ErrorCategory.INPUT_ERROR, cliRunId,
                    "cannot read problem file " + file + ": " + e.getMessage());
        }
        String runId = cliRunId;
        Long gridW = null;
        Long gridH = null;
        Long maxNodes = null;
        Long maxTime = null;
        List<RectSpec> rects = new ArrayList<>();
        int lineNo = 0;
        for (String raw : lines) {
            lineNo++;
            String line = raw;
            int hash = line.indexOf('#');
            if (hash >= 0) {
                line = line.substring(0, hash);
            }
            line = line.trim();
            if (line.isEmpty()) {
                continue;
            }
            String[] t = line.split("\\s+");
            String where = file.getFileName() + ":" + lineNo;
            switch (t[0]) {
                case "run" -> {
                    expect(t, 2, where);
                    runId = t[1];
                }
                case "grid" -> {
                    expect(t, 3, where);
                    gridW = parseLong(t[1], where, runId);
                    gridH = parseLong(t[2], where, runId);
                }
                case "limits" -> {
                    expect(t, 3, where);
                    maxNodes = parseLong(t[1], where, runId);
                    maxTime = parseLong(t[2], where, runId);
                }
                case "rect" -> {
                    if (gridW == null) {
                        throw bad(where, runId, "'grid' must be declared before any 'rect'");
                    }
                    rects.add(parseRect(t, gridW, gridH, where, runId));
                }
                default -> throw bad(where, runId, "unknown keyword '" + t[0] + "'");
            }
        }
        if (gridW == null) {
            throw new KernelException(ErrorCategory.INPUT_ERROR, runId,
                    "missing 'grid W H' declaration in " + file);
        }
        Problem p = Problem.of(gridW, gridH, rects, runId);
        return new Parsed(p, runId, maxNodes, maxTime);
    }

    private static RectSpec parseRect(String[] t, long gw, long gh, String where, String runId) {
        if (t.length < 5) {
            throw bad(where, runId,
                    "rect needs: rect <id> <w> <h> <mandatory|optional> [at x y | domain xmin xmax ymin ymax]");
        }
        String id = t[1];
        long w = parseLong(t[2], where, runId);
        long h = parseLong(t[3], where, runId);
        boolean optional = switch (t[4]) {
            case "mandatory" -> false;
            case "optional" -> true;
            default -> throw bad(where, runId, "presence must be 'mandatory' or 'optional', got '" + t[4] + "'");
        };
        Domain d;
        if (t.length == 5) {
            d = new Domain(0, gw - w, 0, gh - h);
        } else if (t.length == 8 && t[5].equals("at")) {
            d = Domain.singleton(parseLong(t[6], where, runId), parseLong(t[7], where, runId));
        } else if (t.length == 10 && t[5].equals("domain")) {
            d = new Domain(parseLong(t[6], where, runId), parseLong(t[7], where, runId),
                    parseLong(t[8], where, runId), parseLong(t[9], where, runId));
        } else {
            throw bad(where, runId, "expected 'at <x> <y>' or 'domain <xmin> <xmax> <ymin> <ymax>'");
        }
        return new RectSpec(id, w, h, optional, d);
    }

    private static void expect(String[] t, int n, String where) {
        if (t.length != n) {
            throw new KernelException(ErrorCategory.INPUT_ERROR, "parse",
                    where + ": expected " + (n - 1) + " argument(s) for '" + t[0] + "', got " + (t.length - 1));
        }
    }

    private static long parseLong(String token, String where, String runId) {
        final long v;
        try {
            v = Long.parseLong(token);
        } catch (NumberFormatException e) {
            throw bad(where, runId, "expected integer, got '" + token + "'");
        }
        if (Math.abs(v) > Problem.MAX_COORD) {
            throw bad(where, runId, "value " + v + " exceeds supported coordinate bound " + Problem.MAX_COORD);
        }
        return v;
    }

    private static KernelException bad(String where, String runId, String msg) {
        return new KernelException(ErrorCategory.INPUT_ERROR, runId, where + ": " + msg);
    }
}
