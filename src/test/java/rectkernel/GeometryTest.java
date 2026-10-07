package rectkernel;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;
import rectkernel.evidence.Overlap;

/** Reference-geometry contract: edge touching legal, zero area inert. */
class GeometryTest {

    @Test
    void edgeTouchingIsLegalInAllFourDirections() {
        // A occupies [0,2) x [0,2); B touches each side and the corner.
        assertFalse(Overlap.overlaps(0, 0, 2, 2, 2, 0, 2, 2), "right touch");
        assertFalse(Overlap.overlaps(0, 0, 2, 2, -2, 0, 2, 2), "left touch");
        assertFalse(Overlap.overlaps(0, 0, 2, 2, 0, 2, 2, 2), "above touch");
        assertFalse(Overlap.overlaps(0, 0, 2, 2, 0, -2, 2, 2), "below touch");
        assertFalse(Overlap.overlaps(0, 0, 2, 2, 2, 2, 2, 2), "corner touch");
    }

    @Test
    void positiveAreaIntersectionIsDetected() {
        assertTrue(Overlap.overlaps(0, 0, 2, 2, 1, 1, 2, 2));
        assertTrue(Overlap.overlaps(-3, -3, 2, 2, -2, -2, 2, 2), "negative coordinates");
        assertTrue(Overlap.overlaps(0, 0, 4, 4, 1, 1, 1, 1), "containment");
    }

    @Test
    void zeroAreaRectanglesNeverOverlap() {
        assertFalse(Overlap.overlaps(0, 0, 0, 5, 0, 0, 1, 1), "zero width");
        assertFalse(Overlap.overlaps(0, 0, 5, 0, 0, 0, 1, 1), "zero height");
        assertFalse(Overlap.overlaps(3, 3, 0, 0, 3, 3, 4, 4), "zero point inside other");
        assertFalse(Overlap.overlaps(0, 0, 0, 0, 0, 0, 0, 0), "two zero points");
        // Regression: a degenerate rectangle strictly inside another's span
        // must still be inert (caught by the enumerator cross-check).
        assertFalse(Overlap.overlaps(0, 0, 3, 3, 0, 1, 2, 0), "zero-height line strictly inside");
        assertFalse(Overlap.overlaps(0, 0, 3, 3, 1, 0, 0, 2), "zero-width line strictly inside");
        assertFalse(Overlap.overlaps(0, 1, 2, 0, 0, 0, 3, 3), "same with arguments swapped");
    }
}
