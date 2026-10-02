package com.example.clique.search;

/** Enumeration strategy for maximal cliques. */
public enum Strategy {
    /** Bron-Kerbosch with Tomita pivot selection on the whole graph. */
    PIVOT,
    /** Bron-Kerbosch with pivot, driven by a degeneracy vertex ordering (Eppstein-Loffler-Strash). */
    DEGENERACY
}
