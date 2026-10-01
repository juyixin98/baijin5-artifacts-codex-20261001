package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.PlacedRect;
import com.example.nonoverlap.api.Status;
import com.example.nonoverlap.api.Trace;
import com.example.nonoverlap.model.Existence;
import com.example.nonoverlap.model.Instance;
import com.example.nonoverlap.model.Placement;
import com.example.nonoverlap.model.RectDef;
import com.example.nonoverlap.support.RecordingTrace;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SolverTest {

    private Instance instance(String name, int w, int h, RectDef... rects) {
        return new Instance(name, w, h, List.of(rects));
    }

    private KernelOutcome solve(Instance in, int cap, Trace trace) {
        return new Solver(Budget.unlimited(), cap, trace).solve(new SearchState(in, Map.of()));
    }

    @Test
    void enumeratesBothOrderingsOfTwoRequiredCells() {
        Instance in = instance("touch", 2, 1,
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE));
        KernelOutcome out = solve(in, 50, Trace.noop());
        assertInstanceOf(KernelOutcome.Found.class, out);
        var found = (KernelOutcome.Found) out;
        assertEquals(2, found.solutions().size());
        // concrete witnesses, not "call succeeded"
        PlacedRect firstA = found.solutions().get(0).get("A");
        PlacedRect firstB = found.solutions().get(0).get("B");
        assertEquals(0, firstA.x());
        assertEquals(1, firstB.x());
        PlacedRect secondA = found.solutions().get(1).get("A");
        PlacedRect secondB = found.solutions().get(1).get("B");
        assertEquals(1, secondA.x());
        assertEquals(0, secondB.x());
    }

    @Test
    void undecidedRectangleOnlySurvivesAsAbsentAndThatIsTheUniqueAnswer() {
        Instance in = instance("u", 1, 1,
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.UNKNOWN));
        var found = (KernelOutcome.Found) solve(in, 50, Trace.noop());
        assertEquals(1, found.solutions().size());
        assertEquals(Existence.FALSE, found.solutions().get(0).get("B").resolvedExistence());
        assertEquals(Existence.TRUE, found.solutions().get(0).get("A").resolvedExistence());
    }

    @Test
    void backtracksThroughBranchesAndProvesUnsat() {
        Instance in = instance("alldiff", 2, 1,
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE),
                new RectDef("C", 1, 1, Existence.TRUE));
        RecordingTrace rec = new RecordingTrace();
        KernelOutcome out = solve(in, 50, rec);
        assertInstanceOf(KernelOutcome.Infeasible.class, out);
        var inf = (KernelOutcome.Infeasible) out;
        assertEquals(false, inf.atRoot(), "declaration-level impossibility is UNSAT not STATE_CONFLICT");
        assertTrue(inf.stats().backtracks() > 0, "must actually backtrack: " + inf.stats());
        assertTrue(rec.contains("backtrack", "dead-end"));
        assertTrue(rec.contains("result", "unsat") || rec.events().stream()
                .noneMatch(e -> e.phase().equals("result")));
    }

    @Test
    void optionalThirdRectYieldsExactlyTwoPackingsWithItAbsent() {
        Instance in = instance("opt", 2, 1,
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE),
                new RectDef("C", 1, 1, Existence.UNKNOWN));
        var found = (KernelOutcome.Found) solve(in, 50, Trace.noop());
        assertEquals(2, found.solutions().size());
        for (var sol : found.solutions()) {
            assertEquals(Existence.FALSE, sol.get("C").resolvedExistence());
        }
    }

    @Test
    void zeroPointCanShareAnyAnchorButAbsenceIsAlsoEnumerated() {
        Instance in = instance("zp", 3, 1,
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("z", 0, 0, Existence.UNKNOWN));
        var found = (KernelOutcome.Found) solve(in, 100, Trace.noop());
        // A at 0/1/2 (3); for each, z present at any of 4*2=8..16 anchors or absent
        // 3 A-anchors; z present at 4*2=8 boundary-vertex anchors or absent: 3*9=27
        assertEquals(27, found.solutions().size());
    }

    @Test
    void nodeBudgetOneReportsResourceExhausted() {
        Instance in = instance("opt", 2, 1,
                new RectDef("A", 1, 1, Existence.TRUE),
                new RectDef("B", 1, 1, Existence.TRUE),
                new RectDef("C", 1, 1, Existence.UNKNOWN));
        KernelOutcome out = new Solver(new Budget(1, Long.MAX_VALUE), 50, Trace.noop())
                .solve(new SearchState(in, Map.of()));
        assertInstanceOf(KernelOutcome.Limited.class, out);
        assertEquals(1, ((KernelOutcome.Limited) out).stats().nodes());
    }
}
