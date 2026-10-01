package com.example.nonoverlap.cli;

import com.example.nonoverlap.api.PlacedRect;
import com.example.nonoverlap.api.Service;
import com.example.nonoverlap.api.SolveResult;
import com.example.nonoverlap.api.Status;
import com.example.nonoverlap.io.GridText;
import com.example.nonoverlap.io.InstanceFormatException;
import com.example.nonoverlap.io.TextInstanceParser;
import com.example.nonoverlap.model.Placement;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Runnable service entry.
 *
 * <pre>
 * Main &lt;file.txt&gt; [--all &lt;cap&gt;] [--max-nodes N] [--max-checks N] [--log-dir DIR]
 * </pre>
 *
 * Exit codes: 0 SAT, 10 UNSAT, 20 INVALID_INPUT, 30 STATE_CONFLICT,
 * 40 RESOURCE_EXHAUSTED, 50 COMPUTATION_FAILED.
 */
public final class Main {

    public static void main(String[] args) {
        System.exit(run(args));
    }

    static int run(String[] args) {
        if (args.length == 0 || args[0].startsWith("-")) {
            System.err.println("usage: Main <instance.txt> [--all <cap>] "
                    + "[--max-nodes N] [--max-checks N] [--log-dir DIR]");
            return 20;
        }
        Path file = Path.of(args[0]);
        int maxSolutions = 1;
        int maxNodes = Integer.MAX_VALUE;
        long maxChecks = 0;
        Path logDir = Path.of("logs");
        try {
            for (int i = 1; i < args.length; i++) {
                switch (args[i]) {
                    case "--all" -> {
                        i++;
                        maxSolutions = Integer.parseInt(requireValue(args, i));
                    }
                    case "--max-nodes" -> {
                        i++;
                        maxNodes = Integer.parseInt(requireValue(args, i));
                    }
                    case "--max-checks" -> {
                        i++;
                        maxChecks = Long.parseLong(requireValue(args, i));
                    }
                    case "--log-dir" -> {
                        i++;
                        logDir = Path.of(requireValue(args, i));
                    }
                    default -> throw new IllegalArgumentException("unknown option: " + args[i]);
                }
            }
            if (!Files.exists(file)) {
                throw new IllegalArgumentException("instance file not found: " + file);
            }
            TextInstanceParser.Parsed parsed = new TextInstanceParser().parse(file);
            Map<String, java.util.List<Placement>> pre = parsed.preassign;
            SolveResult result = new Service().solve(parsed.instance, pre,
                    maxSolutions, maxNodes, maxChecks, logDir);
            printResult(result, parsed);
            return exitCode(result.status());
        } catch (InstanceFormatException e) {
            System.err.println("INVALID_INPUT: " + e.getMessage());
            return 20;
        } catch (IllegalArgumentException e) {
            System.err.println("INVALID_INPUT: " + e.getMessage());
            return 20;
        } catch (Exception e) {
            System.err.println("COMPUTATION_FAILED: " + e.getClass().getSimpleName()
                    + ": " + e.getMessage());
            return 50;
        }
    }

    private static Map<String, Iterable<Placement>> mergeDomains(
            Map<String, java.util.List<Placement>> raw) {
        Map<String, Iterable<Placement>> out = new LinkedHashMap<>();
        raw.forEach(out::put);
        return out;
    }

    private static String requireValue(String[] args, int i) {
        if (i >= args.length) {
            throw new IllegalArgumentException("option " + args[i - 1] + " requires a value");
        }
        return args[i];
    }

    private static int exitCode(Status status) {
        return switch (status) {
            case SAT -> 0;
            case UNSAT -> 10;
            case INVALID_INPUT -> 20;
            case STATE_CONFLICT -> 30;
            case RESOURCE_EXHAUSTED -> 40;
            case COMPUTATION_FAILED -> 50;
        };
    }

    private static void printResult(SolveResult r, TextInstanceParser.Parsed parsed) {
        System.out.println("runId=" + r.runId());
        System.out.println("status=" + r.status());
        if (r.failureKind() != null) {
            System.out.println("failureKind=" + r.failureKind());
            System.out.println("reason=" + r.reason());
        }
        System.out.println("stats=" + r.stats());
        if (r.status() == Status.SAT) {
            if (r.solutions() != null && r.solutions().size() > 1) {
                int n = 0;
                for (var sol : r.solutions()) {
                    System.out.println("--- solution " + (++n) + " ---");
                    System.out.print(GridText.render(parsed.instance, sol));
                }
                System.out.println("solutionCount=" + r.solutions().size());
            } else if (r.solution() != null) {
                System.out.print(GridText.render(parsed.instance, r.solution()));
                for (PlacedRect p : r.solution().placements()) {
                    System.out.println(p.id() + " " + p.resolvedExistence()
                            + (p.present() ? " @ (" + p.x() + "," + p.y() + ")" : ""));
                }
            }
        }
    }

    private Main() {
    }
}
