package com.example.clique.search;

import java.math.BigInteger;

/** Receives each maximal clique exactly once, as a vertex bit mask. */
@FunctionalInterface
public interface CliqueSink {
    void accept(BigInteger clique);
}
