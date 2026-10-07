package rectkernel.model;

/**
 * Immutable rectangle declaration: id, integer size (w, h >= 0; zero means a
 * degenerate, never-conflicting rectangle), presence kind, and the initial
 * position domain for its lower-left corner.
 */
public record RectSpec(String id, long w, long h, boolean optional, Domain initialDomain) {

    @Override
    public String toString() {
        return id + "(w=" + w + ",h=" + h + (optional ? ",optional" : ",mandatory") + "," + initialDomain + ")";
    }
}
