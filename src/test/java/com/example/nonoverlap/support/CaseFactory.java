package com.example.nonoverlap.support;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

/** Deterministic synthetic cases: fixed curated fixtures plus seeded random grids. */
public final class CaseFactory {

    private CaseFactory() {
    }

    /** Small curated cases with manually derived expected outcomes. */
    public static List<GenCase> curated() {
        return List.of(
                new GenCase("touch", 2, 1, List.of(
                        new GenCase.GenRect("A", 1, 1, "TRUE"),
                        new GenCase.GenRect("B", 1, 1, "TRUE"))),
                new GenCase("unknown-safe", 1, 1, List.of(
                        new GenCase.GenRect("A", 1, 1, "TRUE"),
                        new GenCase.GenRect("B", 1, 1, "UNKNOWN"))),
                new GenCase("four-required-unsat", 1, 1, List.of(
                        new GenCase.GenRect("A", 1, 1, "TRUE"),
                        new GenCase.GenRect("B", 1, 1, "TRUE"))),
                new GenCase("zero-width-line", 2, 1, List.of(
                        new GenCase.GenRect("A", 1, 1, "TRUE"),
                        new GenCase.GenRect("B", 1, 1, "TRUE"),
                        new GenCase.GenRect("z", 1, 0, "UNKNOWN"))),
                new GenCase("zero-point", 3, 1, List.of(
                        new GenCase.GenRect("A", 1, 1, "TRUE"),
                        new GenCase.GenRect("z", 0, 0, "UNKNOWN"))),
                new GenCase("three-two-cells", 2, 1, List.of(
                        new GenCase.GenRect("A", 1, 1, "TRUE"),
                        new GenCase.GenRect("B", 1, 1, "TRUE"),
                        new GenCase.GenRect("C", 1, 1, "UNKNOWN"))),
                new GenCase("three-two-cells-unsat", 2, 1, List.of(
                        new GenCase.GenRect("A", 1, 1, "TRUE"),
                        new GenCase.GenRect("B", 1, 1, "TRUE"),
                        new GenCase.GenRect("C", 1, 1, "TRUE"))),
                new GenCase("forced-conflict", 2, 2, List.of(
                        new GenCase.GenRect("A", 2, 2, "TRUE"),
                        new GenCase.GenRect("B", 1, 1, "UNKNOWN")))
        );
    }

    /**
     * Seeded random small cases. Sizes/placements are clamped so every rectangle
     * fits; existence is a fixed mix. No production code is used here.
     */
    public static List<GenCase> seeded(int count, long baseSeed) {
        List<GenCase> out = new ArrayList<>();
        for (int s = 0; s < count; s++) {
            Random rnd = new Random(baseSeed + s * 1_000_003L);
            int gw = 1 + rnd.nextInt(4);
            int gh = 1 + rnd.nextInt(4);
            int n = 1 + rnd.nextInt(4);
            List<GenCase.GenRect> rects = new ArrayList<>();
            for (int i = 0; i < n; i++) {
                int w = 1 + rnd.nextInt(gw);
                int h = 1 + rnd.nextInt(gh);
                String ex = switch (rnd.nextInt(3)) {
                    case 0 -> "TRUE";
                    case 1 -> "UNKNOWN";
                    default -> i == 0 ? "TRUE" : "FALSE";
                };
                rects.add(new GenCase.GenRect("r" + i, w, h, ex));
            }
            out.add(new GenCase("seed-" + s + "-" + (baseSeed + s), gw, gh, rects));
        }
        return out;
    }
}
