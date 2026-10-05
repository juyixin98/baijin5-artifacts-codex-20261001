package clique.cli;

import clique.bound.BoundsCertificate;
import clique.config.CliqueConfig;
import clique.error.CliqueException;
import clique.error.ErrorCategory;
import clique.graph.Graph;
import clique.graph.GraphBuilder;
import clique.reference.BruteForceMaximalCliques;
import clique.run.RunLog;
import clique.search.BronKerbosch;
import clique.search.CancellationToken;
import clique.search.ResourceExhaustedException;
import clique.search.ResumeState;
import clique.search.SearchResult;

import java.io.IOException;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * 命令行入口。退出码：0 成功；2 输入错误；3 资源耗尽（已打印续扫状态）；1 其他失败。
 */
public final class Main {
    private Main() {
    }

    public static void main(String[] args) {
        try {
            System.exit(run(args));
        } catch (java.io.IOException e) {
            System.err.println(ErrorCategory.INPUT_ERROR + ": " + e.getMessage());
            System.exit(2);
        } catch (ResourceExhaustedException e) {
            SearchResult p = e.partial();
            System.out.println("RESOURCE_EXHAUSTED: " + e.getMessage());
            System.out.println("partial cliques committed: " + p.cliques().size());
            System.out.println("resume state: " + p.resumeState().encode());
            System.out.println("resume with: --resume '" + p.resumeState().encode() + "' (same graph)");
            System.exit(3);
        } catch (CliqueException e) {
            System.err.println(e.category() + ": " + e.getMessage());
            System.exit(e.category() == ErrorCategory.INPUT_ERROR ? 2 : 1);
        }
    }

    private static int run(String[] args) throws IOException {
        String mode = null;
        int n = 40;
        double p = 0.5;
        long seed = 7;
        Path file = null;
        Path configPath = null;
        String resume = null;
        boolean bruteCheck = false;
        int print = 10;
        Long maxSteps = null;

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--demo" -> mode = "demo";
                case "--random" -> mode = "random";
                case "--file" -> {
                    mode = "file";
                    file = Path.of(requireValue(args, ++i, "--file"));
                }
                case "--n" -> n = Integer.parseInt(requireValue(args, ++i, "--n"));
                case "--p" -> p = Double.parseDouble(requireValue(args, ++i, "--p"));
                case "--seed" -> seed = Long.parseLong(requireValue(args, ++i, "--seed"));
                case "--config" -> configPath = Path.of(requireValue(args, ++i, "--config"));
                case "--max-steps" -> maxSteps = Long.parseLong(requireValue(args, ++i, "--max-steps"));
                case "--resume" -> resume = requireValue(args, ++i, "--resume");
                case "--brute-check" -> bruteCheck = true;
                case "--print" -> print = Integer.parseInt(requireValue(args, ++i, "--print"));
                default -> throw new CliqueException(ErrorCategory.INPUT_ERROR, "unknown option: " + args[i]);
            }
        }
        if (mode == null) {
            System.out.println(usage());
            return 0;
        }

        CliqueConfig config = configPath != null ? CliqueConfig.load(configPath) : CliqueConfig.defaults();
        if (maxSteps != null) {
            config = new CliqueConfig(maxSteps, config.pivot(), config.verbosity(), config.bruteForceMaxN());
        }

        Graph graph = switch (mode) {
            case "demo" -> demoGraph();
            case "random" -> randomGraph(n, p, seed);
            case "file" -> readGraph(file);
            default -> throw new CliqueException(ErrorCategory.INPUT_ERROR, "unreachable mode");
        };

        RunLog log = RunLog.create();
        BronKerbosch bk = new BronKerbosch(graph, config, log);
        System.out.println("runId: " + bk.runId());
        System.out.println("graph: n=" + graph.n() + " m=" + graph.edgeCount()
                + " degeneracy=" + bk.degeneracyOrder().degeneracy());

        long t0 = System.nanoTime();
        SearchResult result = resume != null
                ? bk.resume(ResumeState.parse(resume), CancellationToken.none())
                : bk.enumerate(CancellationToken.none());
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;

        System.out.println("completed: " + result.completed() + " steps=" + result.steps()
                + " elapsedMs=" + elapsedMs);
        System.out.println("maximal cliques: " + result.cliques().size());
        if (!result.completed()) {
            System.out.println("INCOMPLETE (cancelled). resume state: " + result.resumeState().encode());
        }

        TreeMap<Integer, Integer> histogram = new TreeMap<>();
        for (BigInteger c : result.cliques()) {
            histogram.merge(c.bitCount(), 1, Integer::sum);
        }
        System.out.println("size histogram (size -> count): " + histogram);

        if (print > 0) {
            int shown = 0;
            for (BigInteger c : new TreeSet<>(result.cliques())) {
                if (shown++ >= print) {
                    System.out.println("... (" + (result.cliques().size() - print) + " more)");
                    break;
                }
                System.out.println("  clique " + bitsetToVertices(c));
            }
        }

        if (result.completed()) {
            BoundsCertificate bounds = BoundsCertificate.assess(graph, result.cliques());
            System.out.println("bounds: omega in [" + bounds.lower() + ", " + bounds.upper() + "]"
                    + (bounds.lower() == bounds.upper() ? " (tight, omega=" + bounds.lower() + ")" : "")
                    + " certificate verify=" + bounds.verify(graph));
        }

        if (bruteCheck) {
            List<BigInteger> expected = BruteForceMaximalCliques.maximalCliques(graph, config.bruteForceMaxN());
            boolean ok = new TreeSet<>(expected).equals(result.cliqueSet());
            System.out.println("brute-check vs exhaustive reference: " + (ok ? "PASS" : "FAIL")
                    + " (reference=" + expected.size() + ", actual=" + result.cliques().size() + ")");
            if (!ok) {
                return 1;
            }
        }
        return 0;
    }

    static Graph demoGraph() {
        // 大量重叠团：四个三角形共顶点 0，外加 (1,3) 产生更多重叠
        GraphBuilder b = new GraphBuilder(9);
        int[][] edges = {
                {0, 1}, {0, 2}, {1, 2},
                {0, 3}, {0, 4}, {3, 4},
                {0, 5}, {0, 6}, {5, 6},
                {0, 7}, {0, 8}, {7, 8},
                {1, 3},
        };
        for (int[] e : edges) {
            b.addEdge(e[0], e[1]);
        }
        return b.build();
    }

    static Graph randomGraph(int n, double p, long seed) {
        if (n < 0 || p < 0.0 || p > 1.0) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "bad --n/--p: n=" + n + " p=" + p);
        }
        Random rnd = new Random(seed);
        GraphBuilder b = new GraphBuilder(n);
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                if (rnd.nextDouble() < p) {
                    b.addEdge(u, v);
                }
            }
        }
        return b.build();
    }

    static Graph readGraph(Path file) throws IOException {
        GraphBuilder builder = null;
        int maxV = -1;
        List<int[]> pending = new java.util.ArrayList<>();
        int lineNo = 0;
        for (String line : Files.readAllLines(file)) {
            lineNo++;
            String s = line.trim();
            if (s.isEmpty() || s.startsWith("#")) {
                continue;
            }
            String[] parts = s.split("\\s+");
            try {
                if (parts[0].equals("n") && parts.length == 2 && builder == null) {
                    builder = new GraphBuilder(Integer.parseInt(parts[1]));
                    continue;
                }
                if (parts.length != 2) {
                    throw new CliqueException(ErrorCategory.INPUT_ERROR,
                            file + ":" + lineNo + ": expect 'u v' or 'n <count>', got '" + s + "'");
                }
                int u = Integer.parseInt(parts[0]);
                int v = Integer.parseInt(parts[1]);
                if (builder == null) {
                    pending.add(new int[]{u, v});
                    maxV = Math.max(maxV, Math.max(u, v));
                } else {
                    builder.addEdge(u, v);
                }
            } catch (NumberFormatException e) {
                throw new CliqueException(ErrorCategory.INPUT_ERROR,
                        file + ":" + lineNo + ": not an integer in '" + s + "'");
            }
        }
        if (builder == null) {
            builder = new GraphBuilder(maxV + 1);
            for (int[] e : pending) {
                builder.addEdge(e[0], e[1]);
            }
        }
        return builder.build();
    }

    private static String requireValue(String[] args, int i, String opt) {
        if (i >= args.length) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, opt + " requires a value");
        }
        return args[i];
    }

    private static String bitsetToVertices(BigInteger c) {
        StringBuilder sb = new StringBuilder("{");
        for (int v : clique.graph.Bits.setBits(c)) {
            if (sb.length() > 1) {
                sb.append(',');
            }
            sb.append(v);
        }
        return sb.append('}').toString();
    }

    private static String usage() {
        return String.join(System.lineSeparator(),
                "usage: java -jar bk-pivot-clique.jar <mode> [options]",
                "  --demo                    built-in overlapping-cliques demo graph",
                "  --random [--n N] [--p P] [--seed S]",
                "  --file PATH               edge list ('u v' per line, optional 'n <count>' header, # comments)",
                "options:",
                "  --config PATH             properties file overriding bundled defaults",
                "  --max-steps K             recursion step budget (RESOURCE_EXHAUSTED beyond it)",
                "  --resume STATE            resume from a printed resume state (same graph)",
                "  --brute-check             cross-check against exhaustive reference (small n)",
                "  --print K                 print up to K cliques (default 10)");
    }
}
