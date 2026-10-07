package rectkernel.search;

import java.util.List;
import rectkernel.error.ErrorCategory;

/** Immutable result of one solver run, with replay metadata. */
public record SolveResult(SolveStatus status, List<Solution> solutions,
                          long nodes, long backtracks, String runId) {

    /** Distinguishable outcome category; null when SAT. */
    public ErrorCategory category() {
        return switch (status) {
            case SAT -> null;
            case UNSAT -> ErrorCategory.STATE_CONFLICT;
            case LIMIT -> ErrorCategory.RESOURCE_EXHAUSTED;
        };
    }
}
