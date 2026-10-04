package com.local.mwis.core;

import java.util.Arrays;

/**
 * One node of a nice tree decomposition. Bags are sorted vertex arrays; bit i of a
 * table mask refers to bag[i]. Exactly one of the four kinds applies:
 * LEAF (empty bag), INTRODUCE (one child, one extra vertex), FORGET (one child, one
 * fewer vertex), JOIN (two children, identical bags).
 */
public final class NiceNode {

    public enum Kind { LEAF, INTRODUCE, FORGET, JOIN }

    public final int id;
    public final Kind kind;
    public final int[] bag;
    public final int[] children;
    /** Introduced or forgotten vertex; -1 for LEAF/JOIN. */
    public final int vertex;
    /**
     * Bit position of {@link #vertex}: for INTRODUCE it is the position in this node's
     * bag, for FORGET the position in the child's bag. -1 for LEAF/JOIN.
     */
    public final int vertexPosition;

    private NiceNode(int id, Kind kind, int[] bag, int[] children, int vertex, int vertexPosition) {
        this.id = id;
        this.kind = kind;
        this.bag = bag;
        this.children = children;
        this.vertex = vertex;
        this.vertexPosition = vertexPosition;
    }

    public static NiceNode leaf(int id) {
        return new NiceNode(id, Kind.LEAF, new int[0], new int[0], -1, -1);
    }

    public static NiceNode introduce(int id, int[] bag, int child, int vertex, int vertexPosition) {
        return new NiceNode(id, Kind.INTRODUCE, bag, new int[]{child}, vertex, vertexPosition);
    }

    public static NiceNode forget(int id, int[] bag, int child, int vertex, int vertexPosition) {
        return new NiceNode(id, Kind.FORGET, bag, new int[]{child}, vertex, vertexPosition);
    }

    public static NiceNode join(int id, int[] bag, int left, int right) {
        return new NiceNode(id, Kind.JOIN, bag, new int[]{left, right}, -1, -1);
    }

    @Override
    public String toString() {
        return "NiceNode#" + id + " " + kind + " bag=" + Arrays.toString(bag)
                + (vertex >= 0 ? " v=" + vertex : "");
    }
}
