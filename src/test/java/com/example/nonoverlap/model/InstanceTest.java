package com.example.nonoverlap.model;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class InstanceTest {

    @Test
    void fullDomainIsIntegerAnchorSetIncludingTheFarEdge() {
        Instance in = new Instance("d", 3, 2,
                List.of(new RectDef("A", 2, 1, Existence.TRUE)));
        List<Placement> d = in.fullDomain(0);
        // x in 0..(3-2)=1, y in 0..(2-1)=1
        assertEquals(4, d.size());
        assertTrue(d.contains(new Placement(0, 0)));
        assertTrue(d.contains(new Placement(1, 1)));
        assertEquals("(0,0),(1,0),(0,1),(1,1)",
                d.stream().map(Placement::toString).collect(Collectors.joining(",")));
    }

    @Test
    void zeroWidthZeroHeightStillHasAnchorChoices() {
        Instance in = new Instance("z", 3, 3,
                List.of(new RectDef("p", 0, 0, Existence.UNKNOWN)));
        assertEquals(16, in.fullDomain(0).size());
        Instance line = new Instance("l", 3, 1,
                List.of(new RectDef("l", 1, 0, Existence.UNKNOWN)));
        assertEquals(6, line.fullDomain(0).size());
    }

    @Test
    void malformedStaticDataIsRejected() {
        assertThrows(IllegalArgumentException.class, () ->
                new Instance("x", 0, 2, List.of(new RectDef("A", 1, 1, Existence.TRUE))));
        assertThrows(IllegalArgumentException.class, () ->
                new Instance("x", 2, 2, List.of(
                        new RectDef("A", 1, 1, Existence.TRUE),
                        new RectDef("A", 1, 1, Existence.TRUE))));
        assertThrows(IllegalArgumentException.class, () ->
                new RectDef("A", -1, 1, Existence.TRUE));
        assertThrows(IllegalArgumentException.class, () ->
                new Instance("x", 2, 2,
                        List.of(new RectDef("A", 3, 1, Existence.TRUE))));
    }

    @Test
    void placementPackingRoundTrips() {
        Placement p = new Placement(-1, 5);
        assertEquals(p, Placement.decode(p.code()));
        Placement q = new Placement(1_000_000, -2_000_000);
        assertEquals(q, Placement.decode(q.code()));
    }
}
