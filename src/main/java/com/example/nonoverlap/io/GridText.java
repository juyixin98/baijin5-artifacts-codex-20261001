package com.example.nonoverlap.io;

import com.example.nonoverlap.api.PlacedRect;
import com.example.nonoverlap.api.Solution;
import com.example.nonoverlap.model.Instance;
import com.example.nonoverlap.model.RectDef;

import java.util.Map;

/** Deterministic ASCII rendering used in run logs and CLI output. */
public final class GridText {

    private GridText() {
    }

    /** Renders a solution on the grid. Zero-area rectangles are listed separately. */
    public static String render(Instance instance, Solution solution) {
        int w = instance.gridWidth();
        int h = instance.gridHeight();
        char[][] cells = new char[h][w];
        for (char[] row : cells) {
            java.util.Arrays.fill(row, '.');
        }
        StringBuilder zeroArea = new StringBuilder();
        for (PlacedRect p : solution.placements()) {
            if (!p.present()) {
                continue;
            }
            RectDef r = instance.rect(instance.indexOf(p.id()));
            if (r.isZeroArea()) {
                if (zeroArea.length() > 0) {
                    zeroArea.append(", ");
                }
                zeroArea.append(p.id()).append("@(").append(p.x()).append(',').append(p.y()).append(')');
                continue;
            }
            char mark = Character.toUpperCase(p.id().charAt(0));
            for (int y = p.y(); y < p.y() + p.height(); y++) {
                for (int x = p.x(); x < p.x() + p.width(); x++) {
                    cells[y][x] = mark;
                }
            }
        }
        StringBuilder sb = new StringBuilder();
        sb.append("grid ").append(w).append('x').append(h).append(System.lineSeparator());
        for (int y = h - 1; y >= 0; y--) {
            sb.append(new String(cells[y])).append(System.lineSeparator());
        }
        if (zeroArea.length() > 0) {
            sb.append("zero-area anchors: ").append(zeroArea).append(System.lineSeparator());
        }
        return sb.toString();
    }

    /** Compact instance header line for trace replay. */
    public static String summary(Instance instance, Map<String, Integer> domainSizes) {
        StringBuilder sb = new StringBuilder();
        sb.append("grid=").append(instance.gridWidth()).append('x').append(instance.gridHeight());
        for (RectDef r : instance.rects()) {
            sb.append(" | ").append(r.id())
                    .append(' ').append(r.width()).append('x').append(r.height())
                    .append(' ').append(r.existence());
            if (domainSizes != null && domainSizes.containsKey(r.id())) {
                sb.append(" dom=").append(domainSizes.get(r.id()));
            }
        }
        return sb.toString();
    }
}
