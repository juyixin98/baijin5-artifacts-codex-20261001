package rectkernel.model;

/**
 * Integer interval domain for a rectangle's lower-left position:
 * x in [xmin, xmax], y in [ymin, ymax], all bounds inclusive integers.
 */
public record Domain(long xmin, long xmax, long ymin, long ymax) {

    public boolean isEmpty() {
        return xmin > xmax || ymin > ymax;
    }

    public boolean isSingleton() {
        return !isEmpty() && xmin == xmax && ymin == ymax;
    }

    /** Saturated position count; only used for branching heuristics and guards. */
    public long size() {
        if (isEmpty()) {
            return 0;
        }
        long w = xmax - xmin + 1;
        long h = ymax - ymin + 1;
        if (w > Long.MAX_VALUE / h) {
            return Long.MAX_VALUE;
        }
        return w * h;
    }

    public Domain withXmin(long v) {
        return new Domain(v, xmax, ymin, ymax);
    }

    public Domain withXmax(long v) {
        return new Domain(xmin, v, ymin, ymax);
    }

    public Domain withYmin(long v) {
        return new Domain(xmin, xmax, v, ymax);
    }

    public Domain withYmax(long v) {
        return new Domain(xmin, xmax, ymin, v);
    }

    public static Domain singleton(long x, long y) {
        return new Domain(x, x, y, y);
    }

    @Override
    public String toString() {
        return "x[" + xmin + "," + xmax + "] y[" + ymin + "," + ymax + "]";
    }
}
