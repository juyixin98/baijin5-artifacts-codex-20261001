package rectkernel.cli;

import java.io.PrintStream;
import java.nio.file.Path;
import java.util.UUID;
import rectkernel.error.ErrorCategory;
import rectkernel.error.KernelException;
import rectkernel.evidence.RunLog;
import rectkernel.search.Solution;
import rectkernel.search.SolveOptions;
import rectkernel.search.SolveResult;
import rectkernel.search.Solver;

/**
 * Service entry point. Exit codes double as the error contract:
 *   0  SAT
 *   10 UNSAT              (STATE_CONFLICT)
 *   11 INPUT_ERROR
 *   12 LIMIT              (RESOURCE_EXHAUSTED)
 *   13 COMPUTATION_FAILED
 */
public final class Main {

    public static final int EXIT_SAT = 0;
    public static final int EXIT_UNSAT = 10;
    public static final int EXIT_INPUT = 11;
    public static final int EXIT_LIMIT = 12;
    public static final int EXIT_FAILED = 13;

    private Main() {
    }

    public static void main(String[] args) {
        System.exit(run(args, System.out, System.err));
    }

    public static int run(String[] args, PrintStream out, PrintStream err) {
        String runId = "cli-" + UUID.randomUUID().toString().substring(0, 8);
        try {
            if (args.length < 2 || !args[0].equals("solve")) {
                err.println("usage: solve <problem-file> [--all] [--max-nodes N] [--max-time-ms N] [--log-dir DIR]");
                return EXIT_INPUT;
            }
            Path file = Path.of(args[1]);
            boolean all = false;
            Long maxNodes = null;
            Long maxTime = null;
            Path logDir = Path.of("target", "run-logs");
            for (int i = 2; i < args.length; i++) {
                String a = args[i];
                try {
                    switch (a) {
                        case "--all" -> all = true;
                        case "--max-nodes" -> maxNodes = Long.parseLong(args[++i]);
                        case "--max-time-ms" -> maxTime = Long.parseLong(args[++i]);
                        case "--log-dir" -> logDir = Path.of(args[++i]);
                        default -> {
                            err.println("run=" + runId + " category=INPUT_ERROR :: unknown option " + a);
                            return EXIT_INPUT;
                        }
                    }
                } catch (IndexOutOfBoundsException | NumberFormatException ex) {
                    err.println("run=" + runId + " category=INPUT_ERROR :: bad value for option " + a);
                    return EXIT_INPUT;
                }
            }
            ProblemParser.Parsed parsed = ProblemParser.parse(file, runId);
            runId = parsed.runId();
            long nodes = maxNodes != null ? maxNodes
                    : (parsed.maxNodes() != null ? parsed.maxNodes() : 1_000_000L);
            long time = maxTime != null ? maxTime
                    : (parsed.maxTimeMs() != null ? parsed.maxTimeMs() : 10_000L);
            SolveOptions opts = new SolveOptions(nodes, time, all, all ? 100_000 : 1);
            Path logFile = logDir.resolve("run-" + runId + ".log");
            SolveResult result;
            try (RunLog log = RunLog.toFile(logFile)) {
                result = new Solver(opts, log, runId).solve(parsed.problem());
            }
            out.println("run=" + runId);
            out.println("status=" + result.status());
            out.println("category=" + (result.category() == null ? "NONE" : result.category()));
            out.println("nodes=" + result.nodes() + " backtracks=" + result.backtracks());
            out.println("solutions=" + result.solutions().size());
            int shown = 0;
            for (Solution s : result.solutions()) {
                if (++shown > 20) {
                    out.println("... (" + (result.solutions().size() - 20) + " more)");
                    break;
                }
                out.println("solution " + s.canonical());
            }
            out.println("log=" + logFile);
            return switch (result.status()) {
                case SAT -> EXIT_SAT;
                case UNSAT -> EXIT_UNSAT;
                case LIMIT -> EXIT_LIMIT;
            };
        } catch (KernelException e) {
            err.println("run=" + runId + " category=" + e.category() + " :: " + e.getMessage());
            return switch (e.category()) {
                case INPUT_ERROR -> EXIT_INPUT;
                case STATE_CONFLICT -> EXIT_UNSAT;
                case RESOURCE_EXHAUSTED -> EXIT_LIMIT;
                case COMPUTATION_FAILED -> EXIT_FAILED;
            };
        } catch (Throwable t) {
            err.println("run=" + runId + " category=" + ErrorCategory.COMPUTATION_FAILED + " :: " + t);
            return EXIT_FAILED;
        }
    }
}
