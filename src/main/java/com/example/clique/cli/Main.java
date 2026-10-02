package com.example.clique.cli;

import com.example.clique.bound.CliqueBounds;
import com.example.clique.bound.MaximumClique;
import com.example.clique.cert.CliqueCertificate;
import com.example.clique.error.CliqueException;
import com.example.clique.graph.Graph;
import com.example.clique.graph.GraphBuilder;
import com.example.clique.reference.BruteForceMaximalCliques;
import com.example.clique.runlog.RunLog;
import com.example.clique.search.CliqueEnumerator;
import com.example.clique.search.EnumerationLimits;
import com.example.clique.search.Strategy;

import java.math.BigInteger;
import java.nio.file.Path;
import java.util.List;

/**
 * Example CLI. Usage:
 * <pre>
 *   Main --demo
 *   Main --vertices 6 --edges 0-1,1-2,2-0,3-4 [--strategy pivot|degeneracy]
 *        [--limit K] [--log-dir DIR]
 * </pre>
 * Prints the run id (for log replay), all maximal cliques, the maximum clique
 * size with its degeneracy upper bound, and the certificate verdict.
 */
public final class Main {

    private Main() {
    }

    public static void main(String[] args) {
        int vertices = -1;
        String edges = "";
        Strategy strategy = Strategy.DEGENERACY;
        Long limit = null;
        Path logDir = Path.of("run-logs");
        boolean demo = false;

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--demo" -> demo = true;
                case "--vertices" -> vertices = Integer.parseInt(args[++i]);
                case "--edges" -> edges = args[++i];
                case "--strategy" -> strategy = Strategy.valueOf(args[++i].trim().toUpperCase());
                case "--limit" -> limit = Long.parseLong(args[++i]);
                case "--log-dir" -> logDir = Path.of(args[++i]);
                default -> throw new CliqueException(
                        com.example.clique.error.FailureCategory.INPUT_ERROR,
                        "unknown argument: " + args[i]);
            }
        }

        if (demo) {
            vertices = 8;
            edges = "0-1,0-2,1-2,1-3,2-3,3-4,4-5,4-6,5-6,6-7";
        }
        if (vertices < 0) {
            throw new CliqueException(com.example.clique.error.FailureCategory.INPUT_ERROR,
                    "missing --vertices N (or use --demo)");
        }

        GraphBuilder builder = Graph.builder(vertices);
        if (!edges.isBlank()) {
            for (String token : edges.split(",")) {
                String[] uv = token.trim().split("-");
                builder.addEdge(Integer.parseInt(uv[0].trim()), Integer.parseInt(uv[1].trim()));
            }
        }
        Graph graph = builder.build();

        try (RunLog log = RunLog.create(logDir)) {
            CliqueEnumerator enumerator = new CliqueEnumerator(log);
            EnumerationLimits limits = limit == null
                    ? EnumerationLimits.unlimited()
                    : EnumerationLimits.ofMaxCliques(limit);
            List<BigInteger> cliques = enumerator.enumerateMaximal(graph, strategy, limits);

            CliqueCertificate.verifyMaximalityAndUniqueness(graph, cliques, log.runId());
            String completeness = "skipped (n=" + graph.vertexCount()
                    + " > brute-force guard " + BruteForceMaximalCliques.MAX_VERTICES + ")";
            if (graph.vertexCount() <= BruteForceMaximalCliques.MAX_VERTICES) {
                CliqueCertificate.verifyCompleteness(graph, cliques, log.runId());
                completeness = "OK (brute-force reference)";
            }
            MaximumClique.Result max = MaximumClique.fromMaximalCliques(graph, cliques);
            log.event("certificate", "maximality+uniqueness=OK completeness=" + completeness);

            System.out.println("run=" + log.runId());
            System.out.println("log=" + log.file());
            System.out.println("strategy=" + strategy);
            System.out.println("vertices=" + graph.vertexCount() + " edges=" + graph.edgeCount());
            System.out.println("maximalCliques=" + cliques.size());
            for (int i = 0; i < cliques.size(); i++) {
                System.out.println("clique[" + i + "]=" + CliqueCertificate.format(cliques.get(i))
                        + " size=" + cliques.get(i).bitCount());
            }
            System.out.println("maximumCliqueSize=" + max.size()
                    + " witness=" + (max.witness() == null ? "-" : CliqueCertificate.format(max.witness())));
            System.out.println("degeneracyUpperBound=" + CliqueBounds.maxCliqueSizeUpperBound(graph));
            System.out.println("certificate=maximality+uniqueness OK; completeness " + completeness);
        }
    }
}
