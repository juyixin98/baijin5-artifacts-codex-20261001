package com.example.nonoverlap.kernel;

/**
 * A propagation wipeout. {@code atRoot} distinguishes an input state conflict
 * (root initial state already inconsistent) from ordinary search dead ends.
 */
public record Conflict(int emptyVar, String reason, boolean atRoot) {
}
