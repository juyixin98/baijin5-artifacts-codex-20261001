package com.example.coloring.demo;

import com.example.coloring.certificate.CertificateVerdict;
import com.example.coloring.certificate.CertificateVerifier;
import com.example.coloring.diag.PrintingSink;
import com.example.coloring.io.GraphText;
import com.example.coloring.model.Graph;
import com.example.coloring.model.SimpleGraph;
import com.example.coloring.model.Graphs;
import com.example.coloring.search.ColoringResult;
import com.example.coloring.search.ColoringSolver;
import com.example.coloring.search.ColoringStatus;
import java.io.PrintStream;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

/**
 * Command line entry point for the chromatic-number solver.
 *
 * <pre>
 *   java -jar vertex-coloring-1.0.0.jar [graph-spec] [budget] [--diag]
 * </pre>
 *
 * where {@code graph-spec} is either {@code family:size} (one of empty, complete,
 * cycle, path, wheel) or {@code file:path.graph}; budget is a non-negative
 * BigInteger node budget (default unlimited).
 */
public final class ColoringDemo {

    private ColoringDemo() {
    }

    public static void main(String[] args) throws Exception {
        PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
        String spec = args.length >= 1 ? args[0] : "cycle:5";
        BigInteger budget = args.length >= 2 ? new BigInteger(args[1])
                : ColoringSolver.UNLIMITED_BUDGET;
        boolean diagnostics = Arrays.asList(args).contains("--diag");

        SimpleGraph graph = resolve(spec);
        String requestId = "req-" + spec.replace(':', '_');
        ColoringSolver solver = new ColoringSolver(diagnostics
                ? new PrintingSink(System.err)
                : event -> { });
        ColoringResult result = solver.solve(graph, budget, requestId);

        out.println("graph=" + spec + " n=" + graph.n() + " edges=" + graph.edgeCount());
        out.println("requestId=" + requestId);
        out.println("status=" + result.status());
        out.println("provenLowerBound=" + result.lowerBound()
                + " (clique witness size " + result.cliqueWitness().length
                + " " + Arrays.toString(result.cliqueWitness())
                + (result.lowerBound() > result.cliqueWitness().length
                        ? ", raised further by infeasibility proof)"
                        : ")"));
        out.println("feasibleUpperBound=" + result.upperBound()
                + " coloring=" + Arrays.toString(result.coloring()));
        if (result.status() == ColoringStatus.OPTIMAL) {
            out.println("chromatic=" + result.chromatic().orElseThrow());
        } else {
            out.println("chromatic=UNDECIDED (budget " + result.budget()
                    + " exhausted after " + result.nodesUsed() + " nodes; "
                    + result.lowerBound() + " <= chi <= " + result.upperBound() + ")");
        }
        out.println("nodesUsed=" + result.nodesUsed());
        out.println("pruningCounts=" + result.pruningCounts());

        CertificateVerdict verdict = CertificateVerifier.verify(graph, result);
        out.println("certificate=" + (verdict.accepted() ? "ACCEPTED" : "REJECTED " + verdict.errors()));
        if (!verdict.accepted()) {
            System.exit(2);
        }
    }

    private static SimpleGraph resolve(String spec) throws Exception {
        String[] parts = spec.split(":", 2);
        if (parts.length != 2) {
            throw new IllegalArgumentException(
                    "expected family:size or file:path, got '" + spec + "'");
        }
        String kind = parts[0];
        if ("file".equals(kind)) {
            return GraphText.read(Files.readString(Path.of(parts[1]), StandardCharsets.UTF_8));
        }
        int size = Integer.parseInt(parts[1]);
        return switch (kind) {
            case "empty" -> Graphs.empty(size);
            case "complete" -> Graphs.complete(size);
            case "cycle" -> Graphs.cycle(size);
            case "path" -> Graphs.path(size);
            case "wheel" -> Graphs.wheel(size);
            default -> throw new IllegalArgumentException("unknown family " + kind);
        };
    }
}
