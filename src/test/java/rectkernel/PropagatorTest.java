package rectkernel;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;
import rectkernel.error.ErrorCategory;
import rectkernel.error.KernelException;
import rectkernel.evidence.RunLog;
import rectkernel.model.Domain;
import rectkernel.model.Problem;
import rectkernel.model.RectSpec;
import rectkernel.propagate.Propagator;
import rectkernel.state.Presence;
import rectkernel.state.SearchState;

/** Propagation-kernel contract tests; every expectation is hand-computed. */
class PropagatorTest {

    private static RectSpec m(String id, long w, long h, Domain d) {
        return new RectSpec(id, w, h, false, d);
    }

    private static RectSpec o(String id, long w, long h, Domain d) {
        return new RectSpec(id, w, h, true, d);
    }

    @Test
    void fourDirectionsExcludedMandatoryIsStateConflict() {
        // I fixed at (1,1); J (2x2) can start at x,y in [0,1]: cannot go left,
        // right, below or above I -> inevitable overlap.
        RunLog log = RunLog.inMemory();
        Problem p = Problem.of(3, 3, List.of(
                m("I", 1, 1, Domain.singleton(1, 1)),
                m("J", 2, 2, new Domain(0, 1, 0, 1))), "T-four-dir-m");
        SearchState s = SearchState.initial(p);
        KernelException e = assertThrows(KernelException.class,
                () -> new Propagator(log, "T-four-dir-m").propagateToFixpoint(s));
        assertEquals(ErrorCategory.STATE_CONFLICT, e.category());
        assertEquals("T-four-dir-m", e.runId());
        assertTrue(e.getMessage().contains("J") && e.getMessage().contains("I"), e.getMessage());
        assertTrue(log.events().stream().anyMatch(
                ev -> ev.kind().equals("conflict") && ev.runId().equals("T-four-dir-m")),
                "conflict must be logged with the run id");
    }

    @Test
    void fourDirectionsExcludedOptionalIsForcedAbsentNotError() {
        // Same geometry, but J is optional: wipe-out forces ABSENT, no failure.
        RunLog log = RunLog.inMemory();
        Problem p = Problem.of(3, 3, List.of(
                m("I", 1, 1, Domain.singleton(1, 1)),
                o("J", 2, 2, new Domain(0, 1, 0, 1))), "T-four-dir-o");
        SearchState s = SearchState.initial(p);
        assertDoesNotThrow(() -> new Propagator(log, "T-four-dir-o").propagateToFixpoint(s));
        assertEquals(Presence.ABSENT, s.byId("J").presence());
        assertTrue(log.events().stream().anyMatch(
                ev -> ev.kind().equals("force-absent") && ev.detail().contains("J")),
                "forced absence must be logged with a reason");
    }

    @Test
    void singleOpenDirectionForcesExactBounds() {
        // I (3x3) at (1,1) occupies [1,4)x[1,4). J (2x2), x in [0,3], y in [3,4]:
        // left/right/below all impossible, only "above" remains -> ymin := 4.
        RunLog log = RunLog.inMemory();
        Problem p = Problem.of(5, 6, List.of(
                m("I", 3, 3, Domain.singleton(1, 1)),
                m("J", 2, 2, new Domain(0, 3, 3, 4))), "T-force-above");
        SearchState s = SearchState.initial(p);
        new Propagator(log, "T-force-above").propagateToFixpoint(s);
        assertEquals(new Domain(0, 3, 4, 4), s.byId("J").domain());
        assertTrue(log.events().stream().anyMatch(
                ev -> ev.kind().equals("prune") && ev.detail().contains("above")));
    }

    @Test
    void undecidedPresenceNeverPrunesOthers() {
        // O (undecided) sits exactly on mandatory J's only cell. O must not
        // prune J; instead mandatory J absorbs O (O forced ABSENT, no error).
        RunLog log = RunLog.inMemory();
        Problem p = Problem.of(1, 1, List.of(
                o("O", 1, 1, Domain.singleton(0, 0)),
                m("J", 1, 1, Domain.singleton(0, 0))), "T-undecided");
        SearchState s = SearchState.initial(p);
        assertDoesNotThrow(() -> new Propagator(log, "T-undecided").propagateToFixpoint(s));
        assertEquals(Presence.ABSENT, s.byId("O").presence(), "mandatory J absorbs optional O");
        assertEquals(Presence.PRESENT, s.byId("J").presence());
        assertEquals(Domain.singleton(0, 0), s.byId("J").domain(),
                "J domain must stay untouched by undecided O");
    }

    @Test
    void edgeAdjacentDomainIsNotPruned() {
        // J may start exactly at the anchor's right edge (x=2): touching is legal.
        RunLog log = RunLog.inMemory();
        Problem p = Problem.of(6, 2, List.of(
                m("I", 2, 2, Domain.singleton(0, 0)),
                m("J", 2, 2, new Domain(2, 4, 0, 0))), "T-edge");
        SearchState s = SearchState.initial(p);
        new Propagator(log, "T-edge").propagateToFixpoint(s);
        assertEquals(new Domain(2, 4, 0, 0), s.byId("J").domain(),
                "touching the anchor edge must stay legal");
    }

    @Test
    void zeroAreaRectanglesAreInert() {
        // Z (0x1) and P (0x0) sit inside anchor I's cell: no conflict, no pruning.
        RunLog log = RunLog.inMemory();
        Problem p = Problem.of(1, 1, List.of(
                m("I", 1, 1, Domain.singleton(0, 0)),
                m("Z", 0, 1, new Domain(0, 1, 0, 0)),
                m("P", 0, 0, Domain.singleton(0, 0))), "T-zero");
        SearchState s = SearchState.initial(p);
        assertDoesNotThrow(() -> new Propagator(log, "T-zero").propagateToFixpoint(s));
        assertEquals(new Domain(0, 1, 0, 0), s.byId("Z").domain());
        assertEquals(Presence.PRESENT, s.byId("Z").presence());
        assertEquals(Presence.PRESENT, s.byId("P").presence());
    }
}
