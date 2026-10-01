package com.example.nonoverlap.model;

/**
 * Existence state of a rectangle.
 *
 * <ul>
 *   <li>{@link #TRUE}    - the rectangle must exist; its position variable is constrained.</li>
 *   <li>{@link #FALSE}   - the rectangle must not exist; it participates in no constraint.</li>
 *   <li>{@link #UNKNOWN} - existence is an open decision variable; it must never be treated
 *                          as necessarily present during propagation.</li>
 * </ul>
 */
public enum Existence {
    TRUE,
    FALSE,
    UNKNOWN
}
