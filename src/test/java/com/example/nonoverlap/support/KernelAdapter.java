package com.example.nonoverlap.support;

import com.example.nonoverlap.api.PlacedRect;
import com.example.nonoverlap.api.Service;
import com.example.nonoverlap.api.Solution;
import com.example.nonoverlap.api.SolveResult;
import com.example.nonoverlap.model.Existence;
import com.example.nonoverlap.model.Instance;
import com.example.nonoverlap.model.RectDef;
import com.example.nonoverlap.oracle.BruteOracle;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/** Thin bridge turning a {@link GenCase} into production inputs and kernel answers
 *  into the oracle's canonical form. Contains no solving logic. */
public final class KernelAdapter {

    private final Service service = new Service();

    public Instance toInstance(GenCase c) {
        List<RectDef> rects = new ArrayList<>();
        for (GenCase.GenRect r : c.rects()) {
            rects.add(new RectDef(r.id(), r.w(), r.h(), Existence.valueOf(r.existence())));
        }
        return new Instance(c.name(), c.gridW(), c.gridH(), rects);
    }

    public SolveResult enumerate(GenCase c, int cap, Path logDir) {
        return service.solve(toInstance(c), Map.of(), cap, 1_000_000, 0, logDir);
    }

    public SolveResult enumerateInMemory(GenCase c, int cap) {
        return service.solveWith(toInstance(c), Map.of(), cap, 1_000_000, 0,
                com.example.nonoverlap.api.Trace.noop(), "test-" + c.name());
    }

    public TreeSet<BruteOracle.Answer> canonical(SolveResult r) {
        TreeSet<BruteOracle.Answer> out = new TreeSet<>();
        for (Solution s : r.solutions()) {
            List<String> terms = new ArrayList<>();
            for (PlacedRect p : s.placements()) {
                terms.add(p.present() ? p.x() + "," + p.y() : "F");
            }
            out.add(new BruteOracle.Answer(terms));
        }
        return out;
    }
}
