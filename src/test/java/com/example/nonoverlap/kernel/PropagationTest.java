package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.Trace;
import com.example.nonoverlap.model.Existence;
import com.example.nonoverlap.model.Instance;
import com.example.nonoverlap.model.Placement;
import com.example.nonoverlap.model.RectDef;
import com.example.nonoverlap.support.RecordingTrace;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PropagationTest {

    private SearchState state(Instance in, Map<String, List<Placement>> dom) {
        return new SearchState(in, dom);
    }

    private Instance twoCells() {
        return new Instance("two", 2, 1, List.of(
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE)));
    }

    @Test
    void touchingAnchorsAreSupportedAndKept() {
        SearchState s = state(twoCells(), Map.of());
        Conflict c = Propagation.propagate(s, new Stats(), Budget.unlimited(),
                true, "r", Trace.noop());
        assertNull(c, "boundary contact keeps every anchor supported");
        assertEquals(2, s.domainSize(0));
        assertEquals(2, s.domainSize(1));
    }

    @Test
    void overlappingAnchorsArePrunedOnBothArcsButOneSupportedAnchorRemains() {
        // force B to the left cell: A may only sit on the right cell
        SearchState s = state(twoCells(), Map.of("B", List.of(new Placement(0, 0))));
        RecordingTrace rec = new RecordingTrace();
        Conflict c = Propagation.propagate(s, new Stats(), Budget.unlimited(),
                true, "r", rec);
        assertNull(c);
        assertEquals(Set.of(new Placement(1, 0).code()), s.domain(0));
        assertTrue(rec.contains("revise", "pruned"));
        assertTrue(rec.contains("propagate", "fixpoint"));
    }

    @Test
    void fourDirectionExclusionWipesTheDomain() {
        // B forced to center (2,2); four required blockers occupy exactly the
        // four separation anchors (left,right,below,above); every B candidate
        // beyond those four is removed from the domain up front.
        Instance in = new Instance("fd", 5, 5, List.of(
                new RectDef("L", 1, 1, Existence.TRUE),
                new RectDef("R", 1, 1, Existence.TRUE),
                new RectDef("D", 1, 1, Existence.TRUE),
                new RectDef("U", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE)));
        SearchState s = state(in, Map.of(
                "L", List.of(new Placement(1, 2)),
                "R", List.of(new Placement(3, 2)),
                "D", List.of(new Placement(2, 1)),
                "U", List.of(new Placement(2, 3)),
                "B", List.of(
                        new Placement(1, 2),
                        new Placement(3, 2),
                        new Placement(2, 1),
                        new Placement(2, 3))));
        RecordingTrace rec = new RecordingTrace();
        Conflict c = Propagation.propagate(s, new Stats(), Budget.unlimited(),
                true, "r", rec);
        assertNotNull(c, "all four separated directions excluded -> wipeout");
        assertEquals(4, c.emptyVar());
        assertTrue(c.atRoot());
        assertTrue(rec.contains("conflict", "domain-wipeout"));
    }

    @Test
    void unknownRectangleNeverStronglyPrunes() {
        // this is the headline boundary condition: with B undecided, A's domain
        // must survive untouched even though every pair of present placements clashes.
        Instance in = new Instance("u", 1, 1, List.of(
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.UNKNOWN)));
        SearchState s = state(in, Map.of());
        Conflict c = Propagation.propagate(s, new Stats(), Budget.unlimited(),
                true, "r", Trace.noop());
        assertNull(c);
        assertEquals(1, s.domainSize(0));
        assertEquals(1, s.domainSize(1), "UNKNOWN domain is never revised");
        // the trace must not record any prune event for this state
        RecordingTrace rec = new RecordingTrace();
        Propagation.propagate(state(in, Map.of()), new Stats(), Budget.unlimited(),
                true, "r", rec);
        assertFalse(rec.contains("revise", "pruned"));
    }

    @Test
    void forcedRequiredDomainIsEmptyAtRootIsAStateConflict() {
        Instance in = new Instance("e", 1, 1, List.of(
                new RectDef("A", 1, 1, Existence.TRUE)));
        SearchState s = new SearchState(in, Map.of("A", List.of()));
        Conflict c = Propagation.propagate(s, new Stats(), Budget.unlimited(),
                true, "r", Trace.noop());
        assertNotNull(c);
        assertTrue(c.reason().contains("A"));
    }

    @Test
    void zeroAreaRectanglePlacedAtSameAnchorIsAlwaysSupported() {
        Instance in = new Instance("z", 1, 1, List.of(
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("z", 0, 0, Existence.TRUE)));
        SearchState s = state(in, Map.of());
        Conflict c = Propagation.propagate(s, new Stats(), Budget.unlimited(),
                true, "r", Trace.noop());
        assertNull(c);
        assertEquals(1, s.domainSize(0));
        assertEquals(4, s.domainSize(1));
    }

    @Test
    void budgetExhaustionStopsPropagationBeforeFixpoint() {
        Instance in = new Instance("big", 6, 6, List.of(
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE),
                new RectDef("C", 1, 1, Existence.TRUE)));
        SearchState s = state(in, Map.of());
        Conflict c = Propagation.propagate(s, new Stats(), new Budget(1, 3),
                true, "r", Trace.noop());
        assertNull(c, "exhaustion returns null (no conflict), caller checks budget");
    }
}
