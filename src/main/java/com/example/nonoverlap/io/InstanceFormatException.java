package com.example.nonoverlap.io;

/** Line-annotated parse error carrying 1-based line numbers for replay. */
public class InstanceFormatException extends IllegalArgumentException {

    private final int line;

    public InstanceFormatException(int line, String message) {
        super("line " + line + ": " + message);
        this.line = line;
    }

    public int line() {
        return line;
    }
}
