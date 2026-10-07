package rectkernel.search;

import java.util.ArrayList;
import java.util.List;
import rectkernel.error.ErrorCategory;
import rectkernel.error.KernelException;
import rectkernel.evidence.RunLog;
import rectkernel.model.Domain;
import rectkernel.model.Problem;
import rectkernel.propagate.Propagator;
import rectkernel.state.Presence;
import rectkernel.state.RectState;
import rectkernel.state.SearchState;

/**
 * Search scheduling: chronological backtracking over presence markers and
 * positions, with propagation to fixpoint at every node.
 *
 * Branching order is deterministic: first decide UNDECIDED presence markers
 * (smallest domain first, PRESENT before ABSENT), then positions of PRESENT
 * rectangles (smallest domain first, coordinates ascending). Node, time and
 * solution caps turn an incomplete search into status LIMIT
 * (RESOURCE_EXHAUSTED), never into a silent wrong answer.
 */
public final class Solver {

    private static final class LimitExceeded extends RuntimeException {
        LimitExceeded(String msg) {
            super(msg);
        }
    }

    private static final class CapReached extends RuntimeException {
    }

    private final SolveOptions options;
    private final RunLog log;
    private final String runId;
    private final long solutionCap;

    private long nodes;
    private long backtracks;
    private long deadlineNanos;
    private List<Solution> solutions;

    public Solver(SolveOptions options, RunLog log, String runId) {
        this.options = options;
        this.log = log;
        this.runId = runId;
        this.solutionCap = options.findAll() ? options.maxSolutions() : 1;
    }

    public SolveResult solve(Problem problem) {
        nodes = 0;
        backtracks = 0;
        solutions = new ArrayList<>();
        deadlineNanos = System.nanoTime() + options.maxTimeMillis() * 1_000_000L;
        log.event(runId, "start", "problem: " + problem + " options: " + options);

        SearchState root = SearchState.initial(problem);
        Propagator propagator = new Propagator(log, runId);
        try {
            propagator.propagateToFixpoint(root);
        } catch (KernelException conflict) {
            log.event(runId, "unsat", "root propagation conflict: " + conflict.getMessage());
            return finish(SolveStatus.UNSAT);
        }
        try {
            search(root, 0, propagator);
        } catch (CapReached cap) {
            log.event(runId, "cap", "solution cap " + solutionCap + " reached");
            return finish(SolveStatus.SAT);
        } catch (LimitExceeded limit) {
            log.event(runId, "limit", limit.getMessage() + " (nodes=" + nodes + ")");
            return finish(SolveStatus.LIMIT);
        }
        return finish(solutions.isEmpty() ? SolveStatus.UNSAT : SolveStatus.SAT);
    }

    private SolveResult finish(SolveStatus status) {
        log.event(runId, "finish", "status=" + status + " nodes=" + nodes
                + " backtracks=" + backtracks + " solutions=" + solutions.size());
        return new SolveResult(status, List.copyOf(solutions), nodes, backtracks, runId);
    }

    private void search(SearchState s, int depth, Propagator propagator) {
        nodes++;
        if (nodes > options.maxNodes()) {
            throw new LimitExceeded("node limit " + options.maxNodes() + " exceeded");
        }
        if (System.nanoTime() > deadlineNanos) {
            throw new LimitExceeded("time limit " + options.maxTimeMillis() + "ms exceeded");
        }
        if (solutions.size() >= solutionCap) {
            throw new CapReached();
        }

        RectState undecided = pickUndecided(s);
        if (undecided != null) {
            String id = undecided.spec().id();
            branchPresence(s.copy(), id, Presence.PRESENT, depth, propagator);
            branchPresence(s.copy(), id, Presence.ABSENT, depth, propagator);
            return;
        }
        RectState open = pickOpen(s);
        if (open == null) {
            Solution sol = Solution.of(s);
            solutions.add(sol);
            log.event(runId, "solution", "#" + solutions.size() + " " + sol.canonical());
            return;
        }
        String id = open.spec().id();
        Domain d = open.domain();
        for (long x = d.xmin(); x <= d.xmax(); x++) {
            for (long y = d.ymin(); y <= d.ymax(); y++) {
                SearchState child = s.copy();
                child.byId(id).setDomain(Domain.singleton(x, y));
                log.event(runId, "branch",
                        "depth=" + depth + " rect " + id + " try position (" + x + "," + y + ")");
                descend(child, depth, propagator);
            }
        }
    }

    private void branchPresence(SearchState child, String id, Presence p, int depth,
                                Propagator propagator) {
        child.byId(id).setPresence(p);
        log.event(runId, "branch", "depth=" + depth + " rect " + id + " try " + p);
        descend(child, depth, propagator);
    }

    private void descend(SearchState child, int depth, Propagator propagator) {
        try {
            propagator.propagateToFixpoint(child);
        } catch (KernelException conflict) {
            if (conflict.category() != ErrorCategory.STATE_CONFLICT) {
                throw conflict;
            }
            backtracks++;
            log.event(runId, "backtrack", "depth=" + depth + " reason=" + conflict.getMessage());
            return;
        }
        search(child, depth + 1, propagator);
    }

    private RectState pickUndecided(SearchState s) {
        RectState best = null;
        for (RectState r : s.rects()) {
            if (r.presence() != Presence.UNDECIDED) {
                continue;
            }
            if (best == null || r.domain().size() < best.domain().size()) {
                best = r;
            }
        }
        return best;
    }

    private RectState pickOpen(SearchState s) {
        RectState best = null;
        for (RectState r : s.rects()) {
            if (r.presence() != Presence.PRESENT || r.domain().isSingleton()) {
                continue;
            }
            if (best == null || r.domain().size() < best.domain().size()) {
                best = r;
            }
        }
        return best;
    }
}
