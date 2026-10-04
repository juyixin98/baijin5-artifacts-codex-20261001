package com.local.mwis.cli;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.local.mwis.service.MwisService;
import com.local.mwis.service.ServiceStatus;
import com.local.mwis.service.SolveReport;
import com.local.mwis.service.SolveRequest;

import java.nio.file.Path;

/**
 * CLI entry point.
 *
 * <pre>
 *   mwis-cli solve &lt;request.json&gt;     run one request, print the JSON report
 *   mwis-cli generate &lt;dir&gt;           write the bundled example fixtures into dir
 * </pre>
 *
 * Exit codes: 0 success (OK_*), 2 rejected request, 3 budget exceeded,
 * 4 cross-check mismatch, 1 any other failure.
 */
public final class Main {

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("usage: mwis-cli solve <request.json> | generate <dir>");
            System.exit(64);
        }
        switch (args[0]) {
            case "solve" -> System.exit(runSolve(Path.of(args[1])));
            case "generate" -> {
                FixtureGenerator.generateAll(Path.of(args[1]));
                System.out.println("fixtures written to " + args[1]);
                System.exit(0);
            }
            default -> {
                System.err.println("unknown command: " + args[0]);
                System.exit(64);
            }
        }
    }

    private static int runSolve(Path requestFile) throws Exception {
        SolveRequest request = new JsonRequestParser().parse(requestFile);
        SolveReport report = new MwisService().solve(request);
        ObjectMapper mapper = new ObjectMapper().enable(SerializationFeature.INDENT_OUTPUT);
        // human-readable log on stderr, machine-readable report on stdout
        for (var entry : report.log()) {
            System.err.println(entry);
        }
        System.out.println(mapper.writeValueAsString(report));
        return switch (report.status()) {
            case OK_PROVEN, OK_BOUND_INCONCLUSIVE, CROSSCHECK_SKIPPED -> 0;
            case REJECTED -> 2;
            case BUDGET_EXCEEDED, BAG_TOO_WIDE -> 3;
            case CROSSCHECK_MISMATCH -> 4;
            case INTERNAL_ERROR -> 1;
        };
    }

    private Main() {
    }
}
