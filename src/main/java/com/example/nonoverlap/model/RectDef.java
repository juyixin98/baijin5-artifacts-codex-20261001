package com.example.nonoverlap.model;

import java.util.Objects;

/**
 * Static declaration of a rectangle: identity, integer size and initial existence state.
 * Width or height may be zero; zero-area rectangles never overlap anything
 * (see {@link Geometry#overlaps}).
 */
public record RectDef(String id, int width, int height, Existence existence) {

    public RectDef {
        Objects.requireNonNull(id, "id");
        Objects.requireNonNull(existence, "existence");
        if (id.isBlank()) {
            throw new IllegalArgumentException("rectangle id must not be blank");
        }
        if (width < 0 || height < 0) {
            throw new IllegalArgumentException(
                    "rectangle '" + id + "' has negative size " + width + "x" + height);
        }
    }

    public boolean isZeroArea() {
        return width == 0 || height == 0;
    }
}
