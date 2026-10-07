package rectkernel.model;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import rectkernel.error.ErrorCategory;
import rectkernel.error.KernelException;

/**
 * Validated constraint model: a bounding grid plus rectangle specs.
 * All validation failures are INPUT_ERROR with a precise reason.
 */
public final class Problem {

    /** Coordinates and sizes are integers bounded by this magnitude. */
    public static final long MAX_COORD = 1_000_000_000L;

    private final long gridW;
    private final long gridH;
    private final List<RectSpec> rects;

    private Problem(long gridW, long gridH, List<RectSpec> rects) {
        this.gridW = gridW;
        this.gridH = gridH;
        this.rects = List.copyOf(rects);
    }

    public static Problem of(long gridW, long gridH, List<RectSpec> rects, String runId) {
        if (gridW < 0 || gridH < 0) {
            throw input(runId, "grid dimensions must be >= 0, got " + gridW + "x" + gridH);
        }
        if (gridW > MAX_COORD || gridH > MAX_COORD) {
            throw input(runId, "grid dimensions exceed supported bound " + MAX_COORD);
        }
        Set<String> ids = new HashSet<>();
        for (RectSpec r : rects) {
            if (r.id() == null || r.id().isBlank()) {
                throw input(runId, "rectangle id must be non-empty");
            }
            if (!ids.add(r.id())) {
                throw input(runId, "duplicate rectangle id '" + r.id() + "'");
            }
            if (r.w() < 0 || r.h() < 0) {
                throw input(runId, "rect " + r.id() + ": negative size w=" + r.w() + " h=" + r.h());
            }
            if (r.w() > gridW || r.h() > gridH) {
                throw input(runId, "rect " + r.id() + ": size " + r.w() + "x" + r.h()
                        + " exceeds grid " + gridW + "x" + gridH);
            }
            Domain d = r.initialDomain();
            if (d == null || d.isEmpty()) {
                throw input(runId, "rect " + r.id() + ": empty position domain");
            }
            if (d.xmin() < 0 || d.ymin() < 0) {
                throw input(runId, "rect " + r.id() + ": domain starts outside grid: " + d);
            }
            if (d.xmax() + r.w() > gridW || d.ymax() + r.h() > gridH) {
                throw input(runId, "rect " + r.id() + ": domain " + d + " with size "
                        + r.w() + "x" + r.h() + " escapes grid " + gridW + "x" + gridH);
            }
        }
        return new Problem(gridW, gridH, rects);
    }

    private static KernelException input(String runId, String msg) {
        return new KernelException(ErrorCategory.INPUT_ERROR, runId, msg);
    }

    public long gridW() {
        return gridW;
    }

    public long gridH() {
        return gridH;
    }

    public List<RectSpec> rects() {
        return rects;
    }

    @Override
    public String toString() {
        StringBuilder sb = new StringBuilder("grid " + gridW + " " + gridH);
        for (RectSpec r : rects) {
            sb.append(" | ").append(r);
        }
        return sb.toString();
    }
}
