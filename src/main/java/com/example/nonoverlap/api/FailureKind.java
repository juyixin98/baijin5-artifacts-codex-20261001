package com.example.nonoverlap.api;

/** Coarse, machine-readable failure family; {@code null} for SAT/UNSAT. */
public enum FailureKind {
    INPUT,
    STATE,
    RESOURCE,
    COMPUTATION
}
