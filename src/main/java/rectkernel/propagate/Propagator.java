package rectkernel.propagate;

import rectkernel.error.ErrorCategory;
import rectkernel.error.KernelException;
import rectkernel.evidence.RunLog;
import rectkernel.model.Domain;
import rectkernel.state.Presence;
import rectkernel.state.RectState;
import rectkernel.state.SearchState;

/**
 * Non-overlap propagation kernel.
 *
 * Anchors are PRESENT rectangles with a singleton position domain. An anchor
 * prunes every other non-ABSENT rectangle by the four-direction rule: the
 * target must be placeable strictly left of, right of, below or above the
 * anchor. If no direction remains, the target inevitably overlaps the anchor:
 *  - UNDECIDED target  -> forced ABSENT (optional wipe-out is not a failure);
 *  - PRESENT target    -> STATE_CONFLICT.
 * If exactly one direction remains, the target's bounds are tightened into it.
 *
 * UNDECIDED rectangles are never anchors: an undecided-presence rectangle must
 * not prune others as if it were mandatory. Zero-area rectangles are inert on
 * both sides (they overlap nothing, so they neither prune nor get pruned).
 */
public final class Propagator {

    private final RunLog log;
    private final String runId;

    public Propagator(RunLog log, String runId) {
        this.log = log;
        this.runId = runId;
    }

    public void propagateToFixpoint(SearchState s) {
        boolean changed = true;
        while (changed) {
            changed = false;
            for (RectState anchor : s.rects()) {
                if (anchor.presence() != Presence.PRESENT || !anchor.domain().isSingleton()) {
                    continue;
                }
                for (RectState target : s.rects()) {
                    if (target == anchor || target.presence() == Presence.ABSENT) {
                        continue;
                    }
                    if (prunePair(anchor, target)) {
                        changed = true;
                    }
                }
            }
        }
    }

    /** @return true if the target state changed. */
    private boolean prunePair(RectState anchor, RectState target) {
        long aw = anchor.spec().w();
        long ah = anchor.spec().h();
        long tw = target.spec().w();
        long th = target.spec().h();
        if (aw == 0 || ah == 0 || tw == 0 || th == 0) {
            return false; // zero-area rectangles overlap nothing: inert on both sides
        }
        long ax = anchor.domain().xmin();
        long ay = anchor.domain().ymin();
        Domain d = target.domain();

        boolean canLeft = d.xmin() + tw <= ax;
        boolean canRight = d.xmax() >= ax + aw;
        boolean canBelow = d.ymin() + th <= ay;
        boolean canAbove = d.ymax() >= ay + ah;
        int ways = (canLeft ? 1 : 0) + (canRight ? 1 : 0) + (canBelow ? 1 : 0) + (canAbove ? 1 : 0);

        if (ways == 0) {
            return wipeOut(anchor, target, "all four separation directions excluded by anchor "
                    + anchor.spec().id() + " at (" + ax + "," + ay + ") size " + aw + "x" + ah);
        }
        if (ways > 1) {
            return false;
        }

        Domain nd;
        String dir;
        if (canLeft) {
            nd = d.withXmax(Math.min(d.xmax(), ax - tw));
            dir = "left";
        } else if (canRight) {
            nd = d.withXmin(Math.max(d.xmin(), ax + aw));
            dir = "right";
        } else if (canBelow) {
            nd = d.withYmax(Math.min(d.ymax(), ay - th));
            dir = "below";
        } else {
            nd = d.withYmin(Math.max(d.ymin(), ay + ah));
            dir = "above";
        }

        if (nd.isEmpty()) {
            return wipeOut(anchor, target, "domain emptied when forced " + dir
                    + " of anchor " + anchor.spec().id());
        }
        if (nd.equals(d)) {
            return false;
        }
        target.setDomain(nd);
        log.event(runId, "prune", "rect " + target.spec().id() + " domain " + d + " -> " + nd
                + " (forced " + dir + " of anchor " + anchor.spec().id() + ")");
        return true;
    }

    private boolean wipeOut(RectState anchor, RectState target, String why) {
        if (target.presence() == Presence.UNDECIDED) {
            target.setPresence(Presence.ABSENT);
            log.event(runId, "force-absent", "rect " + target.spec().id() + " forced ABSENT: "
                    + why + " (optional rectangle wipe-out is not a failure)");
            return true;
        }
        String msg = "rect " + target.spec().id() + " (PRESENT) inevitably overlaps anchor "
                + anchor.spec().id() + ": " + why;
        log.event(runId, "conflict", msg);
        throw new KernelException(ErrorCategory.STATE_CONFLICT, runId, msg);
    }
}
