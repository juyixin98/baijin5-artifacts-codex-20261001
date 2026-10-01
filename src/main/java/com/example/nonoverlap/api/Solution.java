package com.example.nonoverlap.api;

import java.util.List;
import java.util.Map;

/** A complete assignment: existence of every rectangle and anchor of every present one. */
public record Solution(List<PlacedRect> placements, Map<String, Integer> indexById) {

    /** @return anchor code for the rectangle with the given id, or -1 if it is absent */
    public long anchorCode(String id) {
        int i = indexById.get(id);
        PlacedRect p = placements.get(i);
        return p.present() ? ((long) p.x() << 32) | (p.y() & 0xffffffffL) : -1L;
    }

    public PlacedRect get(String id) {
        return placements.get(indexById.get(id));
    }
}
