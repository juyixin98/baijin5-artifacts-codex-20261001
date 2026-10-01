package com.example.nonoverlap.kernel;

import com.example.nonoverlap.api.PlacedRect;
import com.example.nonoverlap.api.Solution;
import com.example.nonoverlap.support.GenCase;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Re-validates a kernel witness from the primitive case definition. */
final class WitnessChecker {

    private WitnessChecker() {
    }

    static void check(GenCase c, Solution s) {
        var rects = c.rects();
        for (int i = 0; i < rects.size(); i++) {
            GenCase.GenRect def = rects.get(i);
            PlacedRect p = s.get(def.id());
            switch (def.existence()) {
                case "TRUE" -> assertTrue(p.present(), c.name() + ": " + def.id() + " must be present");
                case "FALSE" -> assertEquals(false, p.present(), c.name() + ": " + def.id() + " must be absent");
                default -> { }
            }
            if (p.present()) {
                assertTrue(p.x() >= 0 && p.x() <= c.gridW() - def.w(),
                        c.name() + ": x in grid");
                assertTrue(p.y() >= 0 && p.y() <= c.gridH() - def.h(),
                        c.name() + ": y in grid");
            }
        }
        for (int a = 0; a < rects.size(); a++) {
            PlacedRect pa = s.get(rects.get(a).id());
            if (!pa.present() || pa.width() == 0 || pa.height() == 0) {
                continue;
            }
            for (int b = a + 1; b < rects.size(); b++) {
                PlacedRect pb = s.get(rects.get(b).id());
                if (!pb.present() || pb.width() == 0 || pb.height() == 0) {
                    continue;
                }
                boolean disjoint = pa.x() + pa.width() <= pb.x()
                        || pb.x() + pb.width() <= pa.x()
                        || pa.y() + pa.height() <= pb.y()
                        || pb.y() + pb.height() <= pa.y();
                assertTrue(disjoint, c.name() + ": " + pa.id() + " overlaps " + pb.id());
            }
        }
    }
}
