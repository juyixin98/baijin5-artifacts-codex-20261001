package com.example.coloring.demo;

import com.example.coloring.diag.DiagnosticReport;
import com.example.coloring.diag.RequestContext;
import com.example.coloring.diag.SearchEvent;
import com.example.coloring.graph.Graph;
import com.example.coloring.graph.Graphs;
import com.example.coloring.solver.ChromaticResult;
import com.example.coloring.solver.ChromaticSolver;
import com.example.coloring.solver.SolverConfig;

import java.util.Arrays;

/**
 * Small runnable demonstration. Prints one proved-optimal case and one
 * budget-stopped case, showing that the latter reports only proven bounds.
 */
public final class ColoringDemo {

    private ColoringDemo() {
    }

    public static void main(String[] args) {
        ChromaticSolver solver = new ChromaticSolver();

        Graph c5 = Graphs.cycle(5);
        runCase("C5 (odd cycle)", c5, solver, false);

        Graph hard = Graph.builder(7)
                .edge(0, 1).edge(0, 5).edge(0, 6)
                .edge(1, 4).edge(1, 6)
                .edge(2, 3).edge(2, 4).edge(2, 5)
                .edge(3, 4).edge(3, 5)
                .build();
        runCase("hard7 (full budget)", hard, solver, false);

        System.out.println();
        System.out.println("== Budget-stopped run (nodeBudget=1) ==");
        DiagnosticReport report = new DiagnosticReport();
        ChromaticResult limited = new ChromaticSolver(SolverConfig.limited(1, 60_000))
                .solve(hard, RequestContext.of("demo-budget", false), report);
        System.out.println(limited.summary());
        System.out.println("coloring proper? " + limited.coloring().isProper(hard));
        report.events().stream()
                .filter(e -> e.kind() == com.example.coloring.diag.DecisionKind.UNDETERMINED_BUDGET)
                .findFirst()
                .ifPresent(e -> System.out.println("stop event: "
                        + e.render(false, hard.labels())));
    }

    private static void runCase(String name, Graph graph, ChromaticSolver solver, boolean mask) {
        DiagnosticReport report = new DiagnosticReport();
        ChromaticResult result = solver.solve(graph, RequestContext.create(mask), report);
        System.out.println("== " + name + " " + graph + " ==");
        System.out.println(result.summary());
        System.out.println("colors=" + Arrays.toString(result.coloring().colors())
                + " proper=" + result.coloring().isProper(graph)
                + " cliqueWitness=" + result.cliqueWitness().vertices());
        long rejected = report.events().stream().filter(e -> e.kind() == com.example.coloring.diag.DecisionKind.REJECT).count();
        System.out.println("events=" + report.events().size() + " rejectedBranches=" + rejected);
        System.out.println();
    }
}
