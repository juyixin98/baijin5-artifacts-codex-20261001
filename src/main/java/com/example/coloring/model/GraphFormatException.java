package com.example.coloring.model;

/** Thrown when graph text data is malformed. Line numbers are 1-based. */
public class GraphFormatException extends RuntimeException {

    private final int line;

    public GraphFormatException(String message, int line) {
        super("line " + line + ": " + message);
        this.line = line;
    }

    public int line() {
        return line;
    }
}
