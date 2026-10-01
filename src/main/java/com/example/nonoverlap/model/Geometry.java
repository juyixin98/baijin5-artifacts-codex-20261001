package com.example.nonoverlap.model;

/**
 * Pure integer geometry. Rectangles occupy half-open intervals
 * {@code [x, x+width)} x {@code [y, y+height)}, so boundary contact
 * ({@code x2 == x1 + w1} and friends) is legal overlap-free packing.
 *
 * <p>Zero-area rectangles (width 0 or height 0) occupy no points and therefore
 * never overlap any rectangle, even at coincident anchors.
 */
public final class Geometry {

    private Geometry() {
    }

    /**
     * Strict area intersection of two axis-aligned rectangles.
     *
     * @return true only when the intersection has both a positive width and a
     *         positive height in the integer half-open convention
     */
    public static boolean overlaps(int ax, int ay, int aw, int ah,
                                   int bx, int by, int bw, int bh) {
        if (aw <= 0 || ah <= 0 || bw <= 0 || bh <= 0) {
            return false;
        }
        return ax < bx + bw
                && bx < ax + aw
                && ay < by + bh
                && by < ay + ah;
    }

    /** Convenience overload for placed definitions. */
    public static boolean overlaps(RectDef a, Placement pa, RectDef b, Placement pb) {
        return overlaps(pa.x(), pa.y(), a.width(), a.height(),
                pb.x(), pb.y(), b.width(), b.height());
    }
}
