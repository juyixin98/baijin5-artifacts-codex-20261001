package com.local.mwis.bounds;

import java.math.BigInteger;

/**
 * Compares a solved value with the best available upper bound.
 *
 * @param status    PROVEN_OPTIMAL when bound equals value, GAP otherwise
 * @param value     solver's value
 * @param upperBound best computed upper bound
 * @param gap       upperBound - value (zero when proven)
 */
public record OptimalityAssessment(Status status, BigInteger value, BigInteger upperBound, BigInteger gap) {

    public enum Status { PROVEN_OPTIMAL, GAP }

    public static OptimalityAssessment of(BigInteger value, BigInteger upperBound) {
        BigInteger gap = upperBound.subtract(value);
        return new OptimalityAssessment(
                gap.signum() == 0 ? Status.PROVEN_OPTIMAL : Status.GAP,
                value, upperBound, gap);
    }
}
