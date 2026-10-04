package com.local.mwis.core;

import java.math.BigInteger;
import java.util.List;

/**
 * Outcome of a successful solve: optimum weight, one witnessing independent set
 * (sorted vertex ids), and run statistics.
 */
public record SolveResult(BigInteger weight, List<Integer> independentSet, SolverStats stats) {

    public SolveResult {
        independentSet = List.copyOf(independentSet);
    }
}
