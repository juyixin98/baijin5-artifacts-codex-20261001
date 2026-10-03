package com.example.coloring.diag;

import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * Human-readable diagnostic sink that never prints raw vertex ids: each vertex is
 * rendered as a salted, per-request hash, so potentially identifying topology
 * labels cannot leak. Request id, node id, accept/reject reason and key state are
 * always present.
 */
public final class PrintingSink implements DiagnosticSink {

    private final PrintStream out;

    public PrintingSink(PrintStream out) {
        this.out = new PrintStream(out, true, StandardCharsets.UTF_8);
    }

    @Override
    public void accept(DiagnosticEvent e) {
        String remaining = e.remaining() == null ? "unlimited" : e.remaining().toString();
        String maskedVertex = e.vertex() < 0 ? "-" : mask(e.requestId(), e.vertex());
        String color = e.color() < 0 ? "-" : Integer.toString(e.color());
        out.printf("[req=%s node=%d] %-24s vertex=%s color=%s k=%d budgetRemaining=%s state=%s%n",
                e.requestId(),
                e.nodeId(),
                e.reason().name(),
                maskedVertex,
                color,
                e.targetK(),
                remaining,
                e.detail());
    }

    private static String mask(String requestId, int vertex) {
        long hash = 0xcbf29ce484222325L;
        for (byte b : requestId.getBytes(StandardCharsets.UTF_8)) {
            hash ^= (b & 0xffL);
            hash *= 0x100000001b3L;
        }
        hash ^= vertex;
        hash *= 0x100000001b3L;
        return "v#" + Long.toHexString(hash);
    }
}
