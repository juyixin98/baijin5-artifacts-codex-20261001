package rectkernel.evidence;

/**
 * Independent half-open-interval geometry, shared by the reference enumerator
 * and the tests. This is the single source of truth for "do two placed
 * rectangles conflict"; the propagation kernel never calls it, so the
 * cross-check stays honest.
 *
 * Semantics: a rectangle at (x,y) with size (w,h) occupies [x, x+w) x [y, y+h).
 *  - Edge/corner touching is legal (strict inequalities).
 *  - Zero width or zero height means an empty region, which overlaps nothing.
 */
public final class Overlap {

    private Overlap() {
    }

    /** True iff the two half-open rectangles intersect with positive area. */
    public static boolean overlaps(long x, long y, long w, long h,
                                   long x2, long y2, long w2, long h2) {
        if (w <= 0 || h <= 0 || w2 <= 0 || h2 <= 0) {
            return false; // a degenerate rectangle occupies no area
        }
        return x < x2 + w2 && x2 < x + w && y < y2 + h2 && y2 < y + h;
    }
}
