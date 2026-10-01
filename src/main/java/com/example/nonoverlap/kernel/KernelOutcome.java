package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.SearchStats;
import com.example.nonoverlap.api.Solution;

import java.util.List;

/**
 * Raw kernel outcome before service-level mapping:
 * <ul>
 *   <li>{@link Found}     - at least one solution (single or enumerated list);</li>
 *   <li>{@link Infeasible}- proof of impossibility; {@code atRoot} marks a state
 *                           conflict on the submitted initial state;</li>
 *   <li>{@link Limited}   - budget exhausted before SAT/UNSAT was proved.</li>
 * </ul>
 */
public sealed interface KernelOutcome {

    SearchStats stats();

    record Found(Solution solution, List<Solution> solutions, SearchStats stats)
            implements KernelOutcome {
    }

    record Infeasible(String reason, boolean atRoot, SearchStats stats)
            implements KernelOutcome {
    }

    record Limited(String reason, SearchStats stats) implements KernelOutcome {
    }
}
