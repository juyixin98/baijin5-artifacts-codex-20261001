package com.example.coloring.search;

/**
 * Status of a chromatic-number query.
 *
 * <ul>
 *   <li>{@link #OPTIMAL}: lower bound equals upper bound; the value is proven exact
 *       and the witness coloring is attached.</li>
 *   <li>{@link #STOPPED}: the node budget ran out before exactness was reached.
 *       Only proven bounds are reported: {@code lowerBound <= chi <= upperBound}.</li>
 * </ul>
 */
public enum ColoringStatus {
    OPTIMAL,
    STOPPED
}
