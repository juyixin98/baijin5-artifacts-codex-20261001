package com.example.coloring.bounds;

import java.util.List;

/**
 * Lower-bound witness: a vertex set that is pairwise adjacent.
 *
 * <p>A clique of size {@code omega} proves {@code chi >= omega}, because every
 * clique vertex needs a distinct color. The witness is independently
 * verifiable from the graph alone.</p>
 */
public record CliqueCertificate(List<Integer> vertices, boolean provenMaximum) {

    public int size() {
        return vertices.size();
    }
}
