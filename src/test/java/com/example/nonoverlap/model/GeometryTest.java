package com.example.nonoverlap.model;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GeometryTest {

    @Test
    void boundaryContactIsLegalInAllFourDirections() {
        // right edge of A meets left edge of B
        assertFalse(Geometry.overlaps(0, 0, 2, 2, 2, 0, 2, 2), "touch on x must be disjoint");
        // B fully left of A
        assertFalse(Geometry.overlaps(2, 0, 2, 2, 0, 0, 2, 2), "left separation");
        // B above A (touch y)
        assertFalse(Geometry.overlaps(0, 0, 2, 2, 0, 2, 2, 2), "touch on y must be disjoint");
        // B below A
        assertFalse(Geometry.overlaps(0, 2, 2, 2, 0, 0, 2, 2), "below separation");
    }

    @Test
    void oneUnitInteriorOverlapIsDetected() {
        assertTrue(Geometry.overlaps(0, 0, 2, 2, 1, 1, 2, 2), "1x1 interior overlap");
        // a one-cell shift on x but same y still shares a 1x2 strip
        assertTrue(Geometry.overlaps(0, 0, 2, 2, 1, 0, 2, 2));
    }

    @Test
    void zeroAreaRectanglesNeverOverlapEvenAtCoincidentAnchor() {
        assertFalse(Geometry.overlaps(0, 0, 0, 1, 0, 0, 5, 5), "zero-width left");
        assertFalse(Geometry.overlaps(0, 0, 5, 5, 0, 0, 0, 5), "zero-width right");
        assertFalse(Geometry.overlaps(0, 0, 1, 0, 0, 0, 5, 5), "zero-height");
        assertFalse(Geometry.overlaps(0, 0, 0, 0, 0, 0, 0, 0), "two zero-area");
    }

    @Test
    void onlyNegativeCoordinatesAreNeverProducedButIntegerArithmeticStaysExact() {
        // half-open: overlap iff x < bx+bw AND bx < x+aw (strict on both axes)
        assertFalse(Geometry.overlaps(1, 1, 1, 1, 2, 1, 1, 1));
        assertTrue(Geometry.overlaps(1, 1, 2, 1, 2, 1, 2, 1));
    }
}
