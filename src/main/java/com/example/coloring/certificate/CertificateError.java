package com.example.coloring.certificate;

/**
 * Concrete failure category for a rejected coloring/bound certificate.
 * Tests assert the exact category, not merely that verification threw.
 */
public enum CertificateError {

    /** A color id is outside the declared number of colors. */
    COLOR_OUT_OF_RANGE,

    /** Some vertex has no assigned color (sentinel value). */
    UNCOLORED_VERTEX,

    /** Two adjacent vertices share a color, so the coloring is not proper. */
    MONOCHROMATIC_EDGE,

    /** Declared/attached color count does not cover the coloring actually used. */
    COLOR_COUNT_MISMATCH,

    /** Attached clique witness contains two non-adjacent vertices. */
    NOT_A_CLIQUE,

    /** Clique witness size disagrees with the asserted lower bound. */
    LOWER_BOUND_WITNESS_MISMATCH,

    /** Reported lower bound exceeds the reported upper bound. */
    BOUNDS_INVERTED,

    /** Result claims OPTIMAL while lower and upper bound differ. */
    NOT_PROVEN_OPTIMAL,

    /** Result claims a chromatic value while marked STOPPED. */
    STOPPED_BUT_VALUE_CLAIMED
}
