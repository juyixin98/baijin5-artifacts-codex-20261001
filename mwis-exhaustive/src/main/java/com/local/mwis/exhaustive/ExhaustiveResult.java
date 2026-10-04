package com.local.mwis.exhaustive;

import java.math.BigInteger;
import java.util.List;

/**
 * @param weight         optimum total weight (never negative: the empty set is feasible)
 * @param independentSet one optimal set, sorted
 * @param subsetsTested  number of candidate subsets examined (2^n)
 */
public record ExhaustiveResult(BigInteger weight, List<Integer> independentSet, long subsetsTested) {

    public ExhaustiveResult {
        independentSet = List.copyOf(independentSet);
    }
}
