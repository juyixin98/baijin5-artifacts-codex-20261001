package rectkernel.state;

import java.util.ArrayList;
import java.util.List;
import rectkernel.model.Problem;

/** Full mutable search state; deep-copied on branching (problems are small). */
public final class SearchState {

    private final List<RectState> rects;

    private SearchState(List<RectState> rects) {
        this.rects = rects;
    }

    public static SearchState initial(Problem p) {
        List<RectState> rs = new ArrayList<>();
        for (var spec : p.rects()) {
            rs.add(new RectState(spec));
        }
        return new SearchState(rs);
    }

    public SearchState copy() {
        List<RectState> rs = new ArrayList<>(rects.size());
        for (RectState r : rects) {
            rs.add(r.copy());
        }
        return new SearchState(rs);
    }

    public List<RectState> rects() {
        return rects;
    }

    public RectState byId(String id) {
        for (RectState r : rects) {
            if (r.spec().id().equals(id)) {
                return r;
            }
        }
        throw new IllegalArgumentException("unknown rect " + id);
    }
}
