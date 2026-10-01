package com.example.nonoverlap.api;

import com.example.nonoverlap.model.Existence;

/**
 * A rectangle in a solved assignment. {@code x}/{@code y} are only meaningful
 * when {@code resolvedExistence} is TRUE; FALSE rectangles carry (-1,-1).
 */
public record PlacedRect(String id, int x, int y, int width, int height,
                         Existence resolvedExistence) {

    public boolean present() {
        return resolvedExistence == Existence.TRUE;
    }
}
