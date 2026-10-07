package rectkernel.evidence;

import java.util.ArrayList;
import java.util.List;
import rectkernel.error.ErrorCategory;
import rectkernel.error.KernelException;
import rectkernel.model.Domain;
import rectkernel.model.Problem;
import rectkernel.model.RectSpec;

/**
 * Independent reference implementation: enumerates every presence set and
 * every layout by direct pairwise overlap checks (see {@link Overlap}).
 * Shares no logic with the propagation kernel or the solver, so agreement
 * between the two is meaningful evidence. Only usable on small grids; a cap
 * turns oversized requests into RESOURCE_EXHAUSTED instead of a hang.
 */
public final class BruteForceEnumerator {

    public record Assignment(String id, boolean present, long x, long y) {
    }

    private final Problem problem;
    private final long cap;
    private final String runId;
    private final List<List<Assignment>> layouts = new ArrayList<>();

    private BruteForceEnumerator(Problem problem, long cap, String runId) {
        this.problem = problem;
        this.cap = cap;
        this.runId = runId;
    }

    public static List<List<Assignment>> enumerate(Problem problem, long cap, String runId) {
        BruteForceEnumerator e = new BruteForceEnumerator(problem, cap, runId);
        e.place(0, new ArrayList<>());
        return e.layouts;
    }

    /** Upper bound on enumerated combinations, used by callers to skip huge cases. */
    public static long combinationBound(Problem problem) {
        long bound = 1;
        for (RectSpec r : problem.rects()) {
            long choices = r.initialDomain().size() + (r.optional() ? 1 : 0);
            if (choices <= 0 || bound > Long.MAX_VALUE / choices) {
                return Long.MAX_VALUE;
            }
            bound *= choices;
        }
        return bound;
    }

    private void place(int idx, List<Assignment> current) {
        if (idx == problem.rects().size()) {
            if (layouts.size() >= cap) {
                throw new KernelException(ErrorCategory.RESOURCE_EXHAUSTED, runId,
                        "enumeration cap " + cap + " exceeded");
            }
            layouts.add(List.copyOf(current));
            return;
        }
        RectSpec r = problem.rects().get(idx);
        if (r.optional()) {
            current.add(new Assignment(r.id(), false, 0, 0));
            place(idx + 1, current);
            current.remove(current.size() - 1);
        }
        Domain d = r.initialDomain();
        for (long x = d.xmin(); x <= d.xmax(); x++) {
            for (long y = d.ymin(); y <= d.ymax(); y++) {
                if (fits(current, r, x, y)) {
                    current.add(new Assignment(r.id(), true, x, y));
                    place(idx + 1, current);
                    current.remove(current.size() - 1);
                }
            }
        }
    }

    private boolean fits(List<Assignment> placed, RectSpec r, long x, long y) {
        for (Assignment a : placed) {
            if (!a.present()) {
                continue;
            }
            RectSpec other = specOf(a.id());
            if (Overlap.overlaps(x, y, r.w(), r.h(), a.x(), a.y(), other.w(), other.h())) {
                return false;
            }
        }
        return true;
    }

    private RectSpec specOf(String id) {
        for (RectSpec r : problem.rects()) {
            if (r.id().equals(id)) {
                return r;
            }
        }
        throw new IllegalStateException("unknown rect " + id);
    }

    /** Same canonical form as the solver's Solution.canonical(). */
    public static String canonical(List<Assignment> layout) {
        StringBuilder sb = new StringBuilder();
        for (Assignment a : layout) {
            if (!sb.isEmpty()) {
                sb.append(',');
            }
            sb.append(a.id()).append(a.present() ? "=P(" + a.x() + "," + a.y() + ")" : "=A");
        }
        return sb.toString();
    }
}
