package rectkernel.search;

import java.util.ArrayList;
import java.util.List;
import rectkernel.state.Presence;
import rectkernel.state.RectState;
import rectkernel.state.SearchState;

/** A complete decided state: every rectangle is PRESENT at a fixed position or ABSENT. */
public record Solution(List<Placement> placements) {

    public static Solution of(SearchState s) {
        List<Placement> ps = new ArrayList<>();
        for (RectState r : s.rects()) {
            if (r.presence() == Presence.PRESENT) {
                if (!r.domain().isSingleton()) {
                    throw new IllegalStateException(
                            "present rect " + r.spec().id() + " has non-singleton domain at leaf");
                }
                ps.add(new Placement(r.spec().id(), true, r.domain().xmin(), r.domain().ymin()));
            } else {
                ps.add(new Placement(r.spec().id(), false, 0, 0));
            }
        }
        return new Solution(ps);
    }

    /** Stable textual form, comparable across solver and independent enumerator. */
    public String canonical() {
        StringBuilder sb = new StringBuilder();
        for (Placement p : placements) {
            if (!sb.isEmpty()) {
                sb.append(',');
            }
            sb.append(p.id()).append(p.present() ? "=P(" + p.x() + "," + p.y() + ")" : "=A");
        }
        return sb.toString();
    }
}
