#!/usr/bin/env python3
"""Generates raw-frame HTTP/2 fixtures for the h2svc compatibility tests.

This script is an INDEPENDENT reference: every expected frame, window value
and error code is computed here directly from RFC 7540 / RFC 7541 constants,
not by the Go implementation under test. If the Go code and this script
disagree, the test fails and the divergence is investigated against the RFC.

Output: fixtures/cases/<name>.json — one self-contained case per file with
hex-encoded inbound frames and the expected outbound frames / states.
"""

import json
import os
import struct

# --- RFC 7540 frame types (§6) ---
DATA, HEADERS, PRIORITY, RST_STREAM, SETTINGS, PUSH_PROMISE, PING, GOAWAY, \
    WINDOW_UPDATE, CONTINUATION = range(10)

# --- flags (§6) ---
END_STREAM = 0x1
ACK = 0x1
END_HEADERS = 0x4

# --- error codes (§7) ---
ERR = {
    0x0: "NO_ERROR", 0x1: "PROTOCOL_ERROR", 0x2: "INTERNAL_ERROR",
    0x3: "FLOW_CONTROL_ERROR", 0x4: "SETTINGS_TIMEOUT", 0x5: "STREAM_CLOSED",
    0x6: "FRAME_SIZE_ERROR", 0x7: "REFUSED_STREAM", 0x8: "CANCEL",
    0x9: "COMPRESSION_ERROR", 0xA: "CONNECT_ERROR", 0xB: "ENHANCE_YOUR_CALM",
}
PROTOCOL_ERROR = 0x1
FLOW_CONTROL_ERROR = 0x3
STREAM_CLOSED = 0x5
FRAME_SIZE_ERROR = 0x6
REFUSED_STREAM = 0x7
CANCEL = 0x8

# --- settings ids (§6.5.2) ---
INITIAL_WINDOW_SIZE = 0x4
MAX_FRAME_SIZE = 0x5

# Protocol constants (§4.2, §6.5.2)
DEFAULT_MAX_FRAME = 16384
DEFAULT_WINDOW = 65535

# Fixture configuration shared with the Go test harness (documented in
# README.md). The harness constructs the connection with exactly these values.
FIXTURE_CONFIG = {
    "max_frame_size": DEFAULT_MAX_FRAME,
    "initial_recv_window": DEFAULT_WINDOW,
    "send_queue_capacity": 256,
    "max_pending_body_bytes": 1 << 20,
    "large_body_bytes": 70000,
}
LARGE_BODY = FIXTURE_CONFIG["large_body_bytes"]


def frame(t, flags, sid, payload=b""):
    """RFC 7540 §4.1 frame header + payload."""
    return len(payload).to_bytes(3, "big") + bytes([t, flags]) + \
        (sid & 0x7FFFFFFF).to_bytes(4, "big") + payload


def settings(*pairs):
    return frame(SETTINGS, 0, 0,
                 b"".join(struct.pack(">HI", i, v) for i, v in pairs))


def settings_ack():
    return frame(SETTINGS, ACK, 0)


def window_update(sid, inc):
    return frame(WINDOW_UPDATE, 0, sid, struct.pack(">I", inc))


def rst_stream(sid, code):
    return frame(RST_STREAM, 0, sid, struct.pack(">I", code))


def ping(data=b"12345678"):
    return frame(PING, 0, 0, data)


# --- Minimal HPACK encoder (RFC 7541), independent of the Go hpack package ---
def hpack_indexed(idx):
    assert idx < 127
    return bytes([0x80 | idx])


def hpack_lit_indexed_name(idx, value):
    """Literal without indexing, name from static table (§6.2.2)."""
    assert idx < 15
    return bytes([idx, len(value)]) + value.encode()


def request_block(method, path):
    b = hpack_indexed(2) if method == "GET" else hpack_indexed(3)
    b += hpack_lit_indexed_name(4, path)      # :path (static idx 4)
    b += hpack_indexed(6)                      # :scheme: http
    b += hpack_lit_indexed_name(1, "fixture")  # :authority
    return b


def response_block(status_idx):
    """What a correct server emits: indexed :status + content-type literal.

    content-type is static name index 31; 31 >= 15 so the 4-bit prefix
    integer continues in one extra byte carrying 31-15=16 (§5.1)."""
    return hpack_indexed(status_idx) + bytes([0x0F, 0x10, 0x0A]) + b"text/plain"


RESP_200 = response_block(8)   # :status: 200 -> static index 8
RESP_404 = response_block(13)  # :status: 404 -> static index 13

HELLO_BODY = b"hello h2\n"


def exp(t, flags=None, stream=None, length=None, error=None, payload=None,
        goaway_last=None):
    """One expected outbound frame; only given fields are asserted."""
    d = {"type": t}
    if flags is not None:
        d["flags"] = flags
    if stream is not None:
        d["stream"] = stream
    if length is not None:
        d["length"] = length
    if error is not None:
        d["error_code"] = ERR[error]
    if payload is not None:
        d["payload_hex"] = payload.hex()
    if goaway_last is not None:
        d["goaway_last_stream"] = goaway_last
    return d


def server_settings_payload():
    return struct.pack(">HI", INITIAL_WINDOW_SIZE, DEFAULT_WINDOW) + \
        struct.pack(">HI", MAX_FRAME_SIZE, DEFAULT_MAX_FRAME)


def exchange_steps():
    """Server settings + client settings handshake."""
    return [
        {"action": "send_server_settings",
         "expect": [exp("SETTINGS", flags=0, stream=0, length=12,
                        payload=server_settings_payload())]},
        {"feed_hex": settings().hex(),
         "expect": [exp("SETTINGS", flags=ACK, stream=0, length=0)]},
    ]


def large_response_data_frames():
    """Reference flow-control computation for GET /large on a fresh connection.

    Both windows start at 65535; frames are capped at 16384. Returns the list
    of expected DATA frame lengths and the remaining pending bytes."""
    window = DEFAULT_WINDOW
    pending = LARGE_BODY
    lengths = []
    while pending > 0 and window > 0:
        n = min(pending, window, DEFAULT_MAX_FRAME)
        lengths.append(n)
        pending -= n
        window -= n
    return lengths, pending


def case_window_shrink_negative():
    data_lens, pending_after = large_response_data_frames()
    assert data_lens == [16384, 16384, 16384, 16383] and pending_after == 4465

    # Reference window arithmetic (RFC 7540 §6.9.2): the SETTINGS delta
    # applies to the open stream's send window and may drive it negative.
    stream_window = 0  # after the 4 DATA frames above: 65535 - 65535
    new_initial = 100
    delta = new_initial - DEFAULT_WINDOW          # -65435
    stream_window += delta                        # -65435 -> negative
    assert stream_window == -65435

    expect_headers = exp("HEADERS", flags=END_HEADERS, stream=1,
                         payload=RESP_200)
    steps = exchange_steps()
    steps.append({
        "feed_hex": frame(HEADERS, END_HEADERS | END_STREAM, 1,
                          request_block("GET", "/large")).hex(),
        "expect": [expect_headers] +
                  [exp("DATA", flags=0, stream=1, length=n) for n in data_lens],
        "expect_pending": {"id": 1, "bytes": pending_after},
    })
    steps.append({
        "feed_hex": settings((INITIAL_WINDOW_SIZE, new_initial)).hex(),
        "expect": [exp("SETTINGS", flags=ACK, stream=0, length=0)],
        "expect_send_window": {"id": 1, "value": stream_window},
        "comment": "window went negative; no DATA may be sent now",
    })
    # Connection-level update alone does not help: stream window still <= 0.
    stream_window += 65435  # == 0
    steps.append({
        "feed_hex": (window_update(0, 65435) + window_update(1, 65435)).hex(),
        "expect": [],
        "expect_send_window": {"id": 1, "value": 0},
        "comment": "window exactly 0 still stalls DATA (window must be > 0)",
    })
    steps.append({
        "feed_hex": window_update(1, 500).hex(),
        "expect": [exp("DATA", flags=0, stream=1, length=500)],
        "expect_pending": {"id": 1, "bytes": pending_after - 500},
    })
    steps.append({
        "feed_hex": window_update(1, 65535).hex(),
        "expect": [exp("DATA", flags=0, stream=1, length=pending_after - 500),
                   exp("DATA", flags=END_STREAM, stream=1, length=0)],
        "expect_pending": {"id": 1, "bytes": 0},
        "expect_stream_state": {"id": 1, "state": "closed"},
    })
    return {
        "name": "window_shrink_negative",
        "description": "SETTINGS_INITIAL_WINDOW_SIZE reduction drives the "
                       "stream send window negative; DATA stalls until "
                       "WINDOW_UPDATE makes it positive again",
        "rfc": "RFC 7540 §6.9.2",
        "config": FIXTURE_CONFIG,
        "steps": steps,
    }


def case_rst_then_late_data():
    steps = exchange_steps()
    steps.append({
        "feed_hex": frame(HEADERS, END_HEADERS, 1,
                          request_block("POST", "/echo")).hex(),
        "expect": [],
        "expect_stream_state": {"id": 1, "state": "open"},
    })
    steps.append({
        "feed_hex": rst_stream(1, CANCEL).hex(),
        "expect": [],
        "expect_stream_state": {"id": 1, "state": "closed"},
    })
    steps.append({
        "feed_hex": frame(DATA, 0, 1, b"late").hex(),
        "expect": [exp("RST_STREAM", stream=1, error=STREAM_CLOSED)],
        "comment": "late DATA after RST: stream error, connection survives",
    })
    steps.append({
        "feed_hex": window_update(1, 100).hex(),
        "expect": [],
        "comment": "late WINDOW_UPDATE on closed stream is ignored (§5.1)",
    })
    steps.append({
        "feed_hex": ping().hex(),
        "expect": [exp("PING", flags=ACK, stream=0, length=8,
                       payload=b"12345678")],
        "comment": "connection-level liveness proves the stream error did "
                   "not tear down the connection",
    })
    return {
        "name": "rst_then_late_data",
        "description": "Frames arriving after RST_STREAM produce stream-level "
                       "STREAM_CLOSED errors; the connection stays alive",
        "rfc": "RFC 7540 §5.1, §5.4.2",
        "config": FIXTURE_CONFIG,
        "steps": steps,
    }


def case_goaway_boundary():
    steps = exchange_steps()
    steps.append({
        "feed_hex": frame(HEADERS, END_HEADERS | END_STREAM, 1,
                          request_block("GET", "/")).hex(),
        "expect": [exp("HEADERS", flags=END_HEADERS, stream=1, payload=RESP_200),
                   exp("DATA", flags=0, stream=1, length=len(HELLO_BODY),
                       payload=HELLO_BODY),
                   exp("DATA", flags=END_STREAM, stream=1, length=0)],
    })
    steps.append({
        "action": "goaway", "code": 0, "debug": "fixture-triggered shutdown",
        "expect": [exp("GOAWAY", stream=0, error=0, goaway_last=1)],
    })
    steps.append({
        "feed_hex": frame(HEADERS, END_HEADERS | END_STREAM, 3,
                          request_block("GET", "/")).hex(),
        "expect": [exp("RST_STREAM", stream=3, error=REFUSED_STREAM)],
        "comment": "stream 3 > GOAWAY lastStreamID 1: refused, not processed",
    })
    steps.append({
        "feed_hex": ping().hex(),
        "expect": [exp("PING", flags=ACK, stream=0, length=8)],
        "comment": "streams at/below the boundary and connection frames "
                   "remain usable",
    })
    return {
        "name": "goaway_boundary",
        "description": "Streams above the GOAWAY lastStreamID boundary are "
                       "refused with REFUSED_STREAM while the connection "
                       "continues serving in-boundary traffic",
        "rfc": "RFC 7540 §6.8",
        "config": FIXTURE_CONFIG,
        "steps": steps,
    }


def conn_error_case(name, description, rfc, frames_hex, error_code,
                    pre_steps=None):
    steps = pre_steps if pre_steps is not None else exchange_steps()
    steps.append({
        "feed_hex": frames_hex,
        "expect": [exp("GOAWAY", stream=0, error=error_code)],
        "expect_conn_error": ERR[error_code],
        "expect_conn_closed": True,
    })
    return {"name": name, "description": description, "rfc": rfc,
            "config": FIXTURE_CONFIG, "steps": steps}


def case_continuation_interleave():
    return conn_error_case(
        "continuation_interleave",
        "A DATA frame interleaved inside an incomplete header block is a "
        "connection-level PROTOCOL_ERROR",
        "RFC 7540 §6.10",
        (frame(HEADERS, END_STREAM, 1, request_block("GET", "/")) +
         frame(DATA, 0, 1, b"x")).hex(),
        PROTOCOL_ERROR)


def case_continuation_wrong_stream():
    return conn_error_case(
        "continuation_wrong_stream",
        "CONTINUATION for a different stream than the open header block is a "
        "connection-level PROTOCOL_ERROR",
        "RFC 7540 §6.10",
        (frame(HEADERS, 0, 1, request_block("GET", "/")) +
         frame(CONTINUATION, END_HEADERS, 3, b"")).hex(),
        PROTOCOL_ERROR)


def case_stray_continuation():
    return conn_error_case(
        "stray_continuation",
        "CONTINUATION without a preceding HEADERS is a connection-level "
        "PROTOCOL_ERROR",
        "RFC 7540 §6.10",
        frame(CONTINUATION, END_HEADERS, 1, b"").hex(),
        PROTOCOL_ERROR)


def case_frame_size_exceeded():
    pre = exchange_steps()
    pre.append({
        "feed_hex": frame(HEADERS, END_HEADERS, 1,
                          request_block("POST", "/echo")).hex(),
        "expect": [],
    })
    oversized = bytes(16385)
    return conn_error_case(
        "frame_size_exceeded",
        "A frame payload longer than the advertised SETTINGS_MAX_FRAME_SIZE "
        "is a connection-level FRAME_SIZE_ERROR",
        "RFC 7540 §4.2",
        frame(DATA, 0, 1, oversized).hex(),
        FRAME_SIZE_ERROR, pre_steps=pre)


def case_settings_bad_length():
    bad = frame(SETTINGS, 0, 0, b"\x00" * 5)  # not a multiple of 6
    return conn_error_case(
        "settings_bad_length",
        "SETTINGS payload length not a multiple of 6 is a connection-level "
        "FRAME_SIZE_ERROR",
        "RFC 7540 §6.5",
        bad.hex(), FRAME_SIZE_ERROR)


def case_ping_bad_length():
    return conn_error_case(
        "ping_bad_length",
        "PING with a payload other than 8 octets is a connection-level "
        "FRAME_SIZE_ERROR",
        "RFC 7540 §6.7",
        frame(PING, 0, 0, b"1234567").hex(), FRAME_SIZE_ERROR)


def case_half_close_transitions():
    steps = exchange_steps()
    steps.append({
        "feed_hex": frame(HEADERS, END_HEADERS | END_STREAM, 1,
                          request_block("GET", "/")).hex(),
        "expect": [exp("HEADERS", flags=END_HEADERS, stream=1, payload=RESP_200),
                   exp("DATA", flags=0, stream=1, length=len(HELLO_BODY)),
                   exp("DATA", flags=END_STREAM, stream=1, length=0)],
        "expect_stream_state": {"id": 1, "state": "closed"},
        "comment": "END_STREAM both directions: open -> half-closed(remote) "
                   "-> closed",
    })
    steps.append({
        "feed_hex": frame(DATA, 0, 1, b"x").hex(),
        "expect": [exp("RST_STREAM", stream=1, error=STREAM_CLOSED)],
        "comment": "DATA on a closed stream is a stream error, not a "
                   "connection error",
    })
    steps.append({
        "feed_hex": frame(HEADERS, END_HEADERS, 3,
                          request_block("GET", "/")).hex(),
        "expect": [exp("HEADERS", flags=END_HEADERS, stream=3, payload=RESP_200),
                   exp("DATA", flags=0, stream=3, length=len(HELLO_BODY)),
                   exp("DATA", flags=END_STREAM, stream=3, length=0)],
        "expect_stream_state": {"id": 3, "state": "half-closed(local)"},
        "comment": "server may legally finish its response while the client "
                   "stream stays open",
    })
    steps.append({
        "feed_hex": frame(DATA, END_STREAM, 3, b"abc").hex(),
        "expect": [],
        "expect_stream_state": {"id": 3, "state": "closed"},
        "comment": "half-closed(local) still accepts client DATA",
    })
    steps.append({
        "feed_hex": ping().hex(),
        "expect": [exp("PING", flags=ACK, stream=0, length=8)],
    })
    return {
        "name": "half_close_transitions",
        "description": "Half-close state migration is legal in both "
                       "directions; violations raise stream-level "
                       "STREAM_CLOSED while the connection survives",
        "rfc": "RFC 7540 §5.1",
        "config": FIXTURE_CONFIG,
        "steps": steps,
    }


def case_conn_flow_control_exceeded():
    pre = exchange_steps()
    pre.append({
        "feed_hex": frame(HEADERS, END_HEADERS, 1,
                          request_block("POST", "/echo")).hex(),
        "expect": [],
    })
    chunk = bytes(16384)
    pre.append({
        "feed_hex": (frame(DATA, 0, 1, chunk) * 3).hex(),
        "expect": [],
        "comment": "3 x 16384 = 49152 of 65535 connection window consumed",
    })
    return conn_error_case(
        "conn_flow_control_exceeded",
        "Receiving more flow-controlled bytes than the connection window "
        "allows is a connection-level FLOW_CONTROL_ERROR",
        "RFC 7540 §6.9.1",
        frame(DATA, 0, 1, chunk).hex(),  # 49152 + 16384 = 65536 > 65535
        FLOW_CONTROL_ERROR, pre_steps=pre)


def case_even_stream_id():
    return conn_error_case(
        "even_stream_id",
        "A client-initiated stream with an even identifier is a "
        "connection-level PROTOCOL_ERROR",
        "RFC 7540 §5.1.1",
        frame(HEADERS, END_HEADERS | END_STREAM, 2,
              request_block("GET", "/")).hex(),
        PROTOCOL_ERROR)


def case_data_on_idle_stream():
    return conn_error_case(
        "data_on_idle_stream",
        "DATA on a stream in the idle state is a connection-level "
        "PROTOCOL_ERROR",
        "RFC 7540 §5.1",
        frame(DATA, 0, 1, b"x").hex(), PROTOCOL_ERROR)


def case_continuation_valid():
    """A header block split across HEADERS + CONTINUATION is legal."""
    block = request_block("GET", "/")
    cut = 5  # split inside the block; both fragments are meaningless alone
    steps = exchange_steps()
    steps.append({
        "feed_hex": (frame(HEADERS, END_STREAM, 1, block[:cut]) +
                     frame(CONTINUATION, END_HEADERS, 1, block[cut:])).hex(),
        "expect": [exp("HEADERS", flags=END_HEADERS, stream=1, payload=RESP_200),
                   exp("DATA", flags=0, stream=1, length=len(HELLO_BODY)),
                   exp("DATA", flags=END_STREAM, stream=1, length=0)],
        "comment": "reassembled header block is processed normally",
    })
    return {
        "name": "continuation_valid",
        "description": "HEADERS without END_HEADERS followed by CONTINUATION "
                       "with END_HEADERS reassembles one legal header block",
        "rfc": "RFC 7540 §6.10",
        "config": FIXTURE_CONFIG,
        "steps": steps,
    }


def case_echo_roundtrip():
    body = b"fixture-echo-body"
    steps = exchange_steps()
    steps.append({
        "feed_hex": frame(HEADERS, END_HEADERS, 1,
                          request_block("POST", "/echo")).hex(),
        "expect": [],
    })
    steps.append({
        "feed_hex": frame(DATA, END_STREAM, 1, body).hex(),
        "expect": [exp("HEADERS", flags=END_HEADERS, stream=1, payload=RESP_200),
                   exp("DATA", flags=0, stream=1, length=len(body),
                       payload=body),
                   exp("DATA", flags=END_STREAM, stream=1, length=0)],
        "expect_stream_state": {"id": 1, "state": "closed"},
    })
    return {
        "name": "echo_roundtrip",
        "description": "POST /echo returns the request body after END_STREAM",
        "rfc": "RFC 7540 §8.1",
        "config": FIXTURE_CONFIG,
        "steps": steps,
    }


def case_not_found():
    steps = exchange_steps()
    steps.append({
        "feed_hex": frame(HEADERS, END_HEADERS | END_STREAM, 1,
                          request_block("GET", "/nope")).hex(),
        "expect": [exp("HEADERS", flags=END_HEADERS, stream=1, payload=RESP_404),
                   exp("DATA", flags=0, stream=1, length=len(b"not found\n"),
                       payload=b"not found\n"),
                   exp("DATA", flags=END_STREAM, stream=1, length=0)],
    })
    return {
        "name": "not_found",
        "description": "Unknown predefined route yields 404",
        "rfc": "RFC 7540 §8.1",
        "config": FIXTURE_CONFIG,
        "steps": steps,
    }


def main():
    cases = [
        case_window_shrink_negative(),
        case_rst_then_late_data(),
        case_goaway_boundary(),
        case_continuation_interleave(),
        case_continuation_wrong_stream(),
        case_stray_continuation(),
        case_frame_size_exceeded(),
        case_settings_bad_length(),
        case_ping_bad_length(),
        case_half_close_transitions(),
        case_conn_flow_control_exceeded(),
        case_even_stream_id(),
        case_data_on_idle_stream(),
        case_continuation_valid(),
        case_echo_roundtrip(),
        case_not_found(),
    ]
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")
    os.makedirs(outdir, exist_ok=True)
    for c in cases:
        path = os.path.join(outdir, c["name"] + ".json")
        with open(path, "w") as f:
            json.dump(c, f, indent=2)
        print(f"wrote {path} ({len(c['steps'])} steps)")
    print(f"{len(cases)} cases generated")


if __name__ == "__main__":
    main()
