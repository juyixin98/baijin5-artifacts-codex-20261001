package com.example.nonoverlap.model;

/**
 * Integer anchor (bottom-left corner) of a rectangle on the grid.
 *
 * <p>The whole domain stays in the integer domain. Placements are packed into a
 * single {@code long} so propagation domains can be compact sets of longs.
 */
public record Placement(int x, int y) {

    /** Packs this anchor into one 64 bit value. */
    public long code() {
        return ((long) x << 32) | (y & 0xffffffffL);
    }

    /** Unpacks an anchor previously produced by {@link #code()}. */
    public static Placement decode(long code) {
        return new Placement((int) (code >>> 32), (int) code);
    }

    @Override
    public String toString() {
        return "(" + x + "," + y + ")";
    }
}
