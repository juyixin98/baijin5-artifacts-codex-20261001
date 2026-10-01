package com.example.nonoverlap.model;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * A validated constraint instance: a finite W x H integer grid and a list of
 * rectangles, each with integer size and initial existence state.
 *
 * <p>This object only checks static input well-formedness (duplicate ids,
 * negative sizes, sizes outside the grid). It contains no solving state.
 */
public final class Instance {

    private final String name;
    private final int gridWidth;
    private final int gridHeight;
    private final List<RectDef> rects;
    private final Map<String, Integer> indexById;

    public Instance(String name, int gridWidth, int gridHeight, List<RectDef> rects) {
        if (gridWidth <= 0 || gridHeight <= 0) {
            throw new IllegalArgumentException(
                    "grid size must be positive, got " + gridWidth + "x" + gridHeight);
        }
        this.name = name == null ? "instance" : name;
        this.gridWidth = gridWidth;
        this.gridHeight = gridHeight;
        List<RectDef> copy = List.copyOf(rects);
        Map<String, Integer> idx = new HashMap<>();
        for (int i = 0; i < copy.size(); i++) {
            RectDef r = copy.get(i);
            if (idx.put(r.id(), i) != null) {
                throw new IllegalArgumentException("duplicate rectangle id: " + r.id());
            }
            if (r.width() > gridWidth || r.height() > gridHeight) {
                throw new IllegalArgumentException(
                        "rectangle '" + r.id() + "' size " + r.width() + "x" + r.height()
                                + " does not fit grid " + gridWidth + "x" + gridHeight);
            }
        }
        this.rects = copy;
        this.indexById = Collections.unmodifiableMap(idx);
    }

    public String name() {
        return name;
    }

    public int gridWidth() {
        return gridWidth;
    }

    public int gridHeight() {
        return gridHeight;
    }

    public List<RectDef> rects() {
        return rects;
    }

    public int size() {
        return rects.size();
    }

    public RectDef rect(int i) {
        return rects.get(i);
    }

    public int indexOf(String id) {
        Integer i = indexById.get(id);
        if (i == null) {
            throw new IllegalArgumentException("unknown rectangle id: " + id);
        }
        return i;
    }

    /**
     * Full legal anchor domain of rectangle {@code i}.
     *
     * <p>Positive size: integer anchors satisfy 0 &le; x &le; W-w and
     * 0 &le; y &le; H-h (the far-edge contact is included). Zero size on an
     * axis contributes the extra boundary vertex, i.e. 0 &le; x &le; W, so a
     * degenerate rectangle may also anchor on the far grid edge. This is the
     * natural integer extension {@code (W+1)} and is checked explicitly in
     * tests. Zero-area rectangles never overlap any rectangle regardless of
     * anchor (see {@link Geometry}).
     */
    public List<Placement> fullDomain(int i) {
        RectDef r = rects.get(i);
        int maxX = gridWidth - r.width();
        int maxY = gridHeight - r.height();
        if (r.width() == 0) {
            maxX = gridWidth;
        }
        if (r.height() == 0) {
            maxY = gridHeight;
        }
        List<Placement> out = new ArrayList<>((maxX + 1) * (maxY + 1));
        for (int y = 0; y <= maxY; y++) {
            for (int x = 0; x <= maxX; x++) {
                out.add(new Placement(x, y));
            }
        }
        return out;
    }

    public Map<String, List<Placement>> defaultDomains() {
        Map<String, List<Placement>> out = new HashMap<>();
        for (int i = 0; i < rects.size(); i++) {
            out.put(rects.get(i).id(), fullDomain(i));
        }
        return out;
    }
}
