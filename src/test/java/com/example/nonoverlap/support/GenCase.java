package com.example.nonoverlap.support;

import java.util.List;

/**
 * Primitive test-side case description. It intentionally uses none of the
 * production kernel classes so the independent oracle and the kernel adapter
 * consume the same neutral data.
 */
public record GenCase(
        String name,
        int gridW,
        int gridH,
        List<GenRect> rects) {

    public record GenRect(String id, int w, int h, String existence) {
    }
}
