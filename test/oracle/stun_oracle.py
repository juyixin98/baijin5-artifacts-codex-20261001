#!/usr/bin/env python3
"""
Independent STUN oracle for the stunlab test suite.

This module is written from scratch from RFC 5389 / RFC 8489 (and the
XOR-MAPPED-ADDRESS worked examples of RFC 5769) using only the Python standard
library. It shares NO code with the Go implementation under test: different
language, independently written parsers and HMAC path.

It is used in three ways:

  1. As a library by cross_check.py to decode messages the Go programs emit
     and verify them from Python's own parser.
  2. As a fixture generator (``fixtures`` subcommand) that emits frozen
     known-answer vectors into test/testdata/fixtures.json, which the Go
     golden test loads and asserts against.
  3. As a tiny UDP peer (``reply`` subcommand) that emits controlled responses
     or malformed datagrams for negative-path end-to-end checks.

Only the Binding subset is implemented, deliberately; there is no TURN relay.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import struct
import sys
import time

MAGIC_COOKIE = 0x2112A442
HEADER_LEN = 20
MI_LEN = 20

# Message classes (on-wire bits).
CLASS_REQUEST = 0x0000
CLASS_INDICATION = 0x0010
CLASS_SUCCESS = 0x0100
CLASS_ERROR = 0x0110

METHOD_BINDING = 0x0001

ATTR_MAPPED_ADDRESS = 0x0001
ATTR_USERNAME = 0x0006
ATTR_MESSAGE_INTEGRITY = 0x0008
ATTR_ERROR_CODE = 0x0009
ATTR_XOR_MAPPED_ADDRESS = 0x0020
ATTR_UNKNOWN_ATTRIBUTES = 0x8005
ATTR_SOFTWARE = 0x8022
ATTR_FINGERPRINT = 0x8028


class StunError(Exception):
    """Parse failure. ``kind`` mirrors the Go failure categories."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


# ---------------------------------------------------------------------------
# Attribute (de)serialisation
# ---------------------------------------------------------------------------

def encode_attributes(attrs):
    """attrs: list of (type:int, value:bytes) -> bytes (padded TLV stream)."""
    out = bytearray()
    for atype, value in attrs:
        if len(value) > 0xFFFF:
            raise StunError("input_error", f"attr 0x{atype:04x} too long")
        out += struct.pack("!HH", atype, len(value))
        out += value
        out += b"\x00" * ((-len(value)) % 4)
    return bytes(out)


def decode_attributes(body):
    """bytes -> list of (type, value). Validates lengths and padding."""
    attrs = []
    off = 0
    n = len(body)
    while off < n:
        if n - off < 4:
            raise StunError("input_error",
                            f"truncated attribute header at offset {off}")
        atype, vlen = struct.unpack("!HH", body[off:off + 4])
        start = off + 4
        end = start + vlen
        if end > n:
            raise StunError("input_error",
                            f"attr 0x{atype:04x} length {vlen} overruns body")
        value = body[start:end]
        pad = (-vlen) % 4
        if pad and end + pad > n:
            raise StunError("input_error",
                            f"attr 0x{atype:04x} padding overruns body")
        attrs.append((atype, value))
        off = end + pad
    return attrs


def encode_mapped_address(ip: str, port: int) -> bytes:
    addr = ipaddress.ip_address(ip)
    if addr.version == 4:
        return b"\x00\x01" + struct.pack("!H", port) + addr.packed
    return b"\x00\x02" + struct.pack("!H", port) + addr.packed


def decode_mapped_address(value: bytes):
    if len(value) < 4:
        raise StunError("input_error", "mapped-address too short")
    if value[0] != 0:
        raise StunError("input_error", "mapped-address reserved byte non-zero")
    family, port = value[1], struct.unpack("!H", value[2:4])[0]
    if family == 1 and len(value) == 8:
        return str(ipaddress.IPv4Address(value[4:8])), port
    if family == 2 and len(value) == 20:
        return str(ipaddress.IPv6Address(value[4:20])), port
    raise StunError("input_error", f"bad mapped-address family/length {family}/{len(value)}")


def encode_xor_mapped_address(ip: str, port: int, txn: bytes) -> bytes:
    addr = ipaddress.ip_address(ip)
    xport = port ^ (MAGIC_COOKIE >> 16)
    if addr.version == 4:
        mask = struct.pack("!I", MAGIC_COOKIE)
        packed = bytes(b ^ m for b, m in zip(addr.packed, mask))
        return b"\x00\x01" + struct.pack("!H", xport) + packed
    mask = struct.pack("!I", MAGIC_COOKIE) + txn
    packed = bytes(b ^ m for b, m in zip(addr.packed, mask))
    return b"\x00\x02" + struct.pack("!H", xport) + packed


def decode_xor_mapped_address(value: bytes, txn: bytes):
    if len(value) < 4:
        raise StunError("input_error", "xor-mapped-address too short")
    if value[0] != 0:
        raise StunError("input_error", "xor-mapped-address reserved byte non-zero")
    family, xport = value[1], struct.unpack("!H", value[2:4])[0]
    port = xport ^ (MAGIC_COOKIE >> 16)
    if family == 1 and len(value) == 8:
        mask = struct.pack("!I", MAGIC_COOKIE)
        packed = bytes(b ^ m for b, m in zip(value[4:8], mask))
        return str(ipaddress.IPv4Address(packed)), port
    if family == 2 and len(value) == 20:
        mask = struct.pack("!I", MAGIC_COOKIE) + txn
        packed = bytes(b ^ m for b, m in zip(value[4:20], mask))
        return str(ipaddress.IPv6Address(packed)), port
    raise StunError("input_error", "bad xor-mapped-address family/length")


def encode_error_code(code: int, reason: str) -> bytes:
    if not 300 <= code <= 699:
        raise StunError("input_error", f"error code out of range {code}")
    reason_b = reason.encode("utf-8")
    if len(reason_b) > 128:
        raise StunError("input_error", "reason too long")
    return bytes([0, 0, code // 100, code % 100]) + reason_b


def decode_error_code(value: bytes):
    if len(value) < 4:
        raise StunError("input_error", "error-code too short")
    cls, num = value[2] & 0x07, value[3]
    if not 3 <= cls <= 6 or num > 99:
        raise StunError("input_error", f"bad error-code {cls}/{num}")
    return cls * 100 + num, value[4:].decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Message (de)serialisation and MESSAGE-INTEGRITY
# ---------------------------------------------------------------------------

def encode_type(method: int, cls: int) -> int:
    return method | cls


def decode_type(wire: int):
    return wire & 0xFEEF, wire & 0x0110


def marshal(method, cls, txn, attrs):
    body = encode_attributes(attrs)
    return struct.pack("!HHI", encode_type(method, cls), len(body),
                       MAGIC_COOKIE) + txn + body


def add_message_integrity(method, cls, txn, attrs, key: bytes) -> bytes:
    body = encode_attributes(attrs)
    total = len(body) + 4 + MI_LEN
    head = struct.pack("!HHI", encode_type(method, cls), total,
                       MAGIC_COOKIE) + txn
    mi_head = struct.pack("!HH", ATTR_MESSAGE_INTEGRITY, MI_LEN)
    covered = head + body + mi_head + b"\x00" * MI_LEN
    mac = hmac.new(key, covered, hashlib.sha1).digest()
    return head + body + mi_head + mac


def unmarshal(raw: bytes):
    """Returns dict(method, class, txn, attrs, raw). Strict length checks."""
    if len(raw) < HEADER_LEN:
        raise StunError("input_error", f"datagram too short: {len(raw)}")
    if raw[0] & 0xC0:
        raise StunError("input_error", "leading two bits non-zero")
    mtype, mlen, cookie = struct.unpack("!HHI", raw[:8])
    if cookie != MAGIC_COOKIE:
        raise StunError("input_error", f"bad magic cookie 0x{cookie:08x}")
    if mlen != len(raw) - HEADER_LEN:
        raise StunError("input_error",
                        f"length {mlen} != body {len(raw) - HEADER_LEN}")
    method, cls = decode_type(mtype)
    txn = raw[8:20]
    attrs = decode_attributes(raw[HEADER_LEN:]) if mlen else []
    return {"method": method, "class": cls, "txn": txn,
            "attrs": attrs, "raw": raw}


def verify_message_integrity(raw: bytes, key: bytes):
    """Strict MI check. Raises StunError on any failure category."""
    if len(raw) < HEADER_LEN:
        raise StunError("input_error", "datagram too short")
    off = HEADER_LEN
    mi_at = -1
    while off < len(raw):
        if len(raw) - off < 4:
            raise StunError("integrity_failure", "truncated attr before MI")
        atype, vlen = struct.unpack("!HH", raw[off:off + 4])
        nxt = off + 4 + vlen + ((-vlen) % 4)
        if nxt > len(raw):
            raise StunError("integrity_failure", "attr overruns datagram")
        if atype == ATTR_MESSAGE_INTEGRITY:
            if vlen != MI_LEN:
                raise StunError("integrity_failure", "MI value length != 20")
            if nxt != len(raw):
                raise StunError("integrity_failure", "MI is not last attr")
            mi_at = off
            break
        off = nxt
    if mi_at < 0:
        raise StunError("integrity_failure", "MI absent")
    covered = bytearray(raw[:mi_at + 4 + MI_LEN])
    struct.pack_into("!H", covered, 2, mi_at + 4 + MI_LEN - HEADER_LEN)
    # The signer computes the tag over the MI attribute with its 20-byte
    # VALUE set to zero (the tag cannot cover itself). The verifier must
    # reconstruct exactly that input.
    covered[mi_at + 4:mi_at + 4 + MI_LEN] = b"\x00" * MI_LEN
    want = hmac.new(key, bytes(covered), hashlib.sha1).digest()
    got = raw[mi_at + 4:mi_at + 4 + MI_LEN]
    if not hmac.compare_digest(want, got):
        raise StunError("integrity_failure", "HMAC mismatch")
    return True


def attr(messages, atype):
    for t, v in messages["attrs"]:
        if t == atype:
            return v
    return None


# ---------------------------------------------------------------------------
# Fixture generation
# ---------------------------------------------------------------------------

def build_fixtures():
    """Frozen known-answer vectors, all derived independently here in Python.

    The RFC 5769 2.3/2.4 XOR values are normative external vectors; the HMAC
    tags are generated by this independent implementation and frozen so the
    Go side cannot grade itself.
    """
    txn = bytes.fromhex("b7e7a701bc34d686fa87dfae")

    # RFC 5769 fixed XOR-MAPPED-ADDRESS attribute values.
    rfc = [
        {"name": "rfc5769_ipv4", "family": "ipv4", "ip": "192.0.2.1",
         "port": 32853, "value_hex": "0001a147e112a643"},
        {"name": "rfc5769_ipv6", "family": "ipv6",
         "ip": "2001:db8:1234:5678:11:2233:4455:6677", "port": 32853,
         "value_hex": "0002a1470113a9faa5d3f179bc25f4b5bed2b9d9"},
    ]
    for v in rfc:
        enc = encode_xor_mapped_address(v["ip"], v["port"], txn)
        v["oracle_encodes_correctly"] = enc.hex() == v["value_hex"]

    # Full messages, with and without MI, IPv4 and IPv6.
    key = b"lab-shared-secret"
    messages = []

    def add_msg(name, ip, port, use_mi):
        xorv = encode_xor_mapped_address(ip, port, txn)
        mapv = encode_mapped_address(ip, port)
        attrs = [
            (ATTR_XOR_MAPPED_ADDRESS, xorv),
            (ATTR_MAPPED_ADDRESS, mapv),
            (ATTR_SOFTWARE, b"stunlab-oracle/1.0"),
        ]
        if use_mi:
            raw = add_message_integrity(METHOD_BINDING, CLASS_SUCCESS,
                                        txn, attrs, key)
        else:
            raw = marshal(METHOD_BINDING, CLASS_SUCCESS, txn, attrs)
        messages.append({
            "name": name, "txn_hex": txn.hex(), "ip": ip, "port": port,
            "integrity": use_mi, "key": key.decode() if use_mi else "",
            "hex": raw.hex(),
            "declared_length": struct.unpack("!H", raw[2:4])[0],
            "actual_body": len(raw) - 20,
        })

    add_msg("success_ipv4_plain", "198.51.100.7", 40960, False)
    add_msg("success_ipv4_mi", "198.51.100.7", 40960, True)
    add_msg("success_ipv6_plain", "2001:db8::abcd", 53, False)
    add_msg("success_ipv6_mi", "2001:db8::abcd", 53, True)

    # Padding vectors: SOFTWARE values of each length residue.
    padding = []
    for n in range(1, 9):
        val = bytes(range(1, n + 1))
        body = encode_attributes([(ATTR_SOFTWARE, val)])
        padding.append({"value_len": n, "wire_len": len(body),
                        "total_wire": HEADER_LEN + len(body), "hex": body.hex()})

    # Error vectors.
    errors = []
    for code, reason in [(400, "Bad Request"), (420, "Unknown Attribute")]:
        attrs = [(ATTR_ERROR_CODE, encode_error_code(code, reason))]
        if code == 420:
            attrs.append((ATTR_UNKNOWN_ATTRIBUTES,
                          struct.pack("!HH", 0x0099, 0x7777)))
        raw = marshal(METHOD_BINDING, CLASS_ERROR, txn, attrs)
        errors.append({"name": f"error_{code}", "code": code,
                       "reason": reason, "hex": raw.hex()})

    # Integrity negative vectors derived from a good MI message.
    good = next(m for m in messages if m["name"] == "success_ipv4_mi")
    good_raw = bytearray.fromhex(good["hex"])
    neg = []

    tag_flip = bytearray(good_raw)
    tag_flip[-1] ^= 0x80
    neg.append({"name": "tag_bitflip", "hex": bytes(tag_flip).hex(),
                "expect_kind": "integrity_failure"})

    txn_flip = bytearray(good_raw)
    txn_flip[12] ^= 0x01
    neg.append({"name": "txn_bitflip", "hex": bytes(txn_flip).hex(),
                "expect_kind": "integrity_failure"})

    wrong_key = good  # same bytes, different key
    neg.append({"name": "wrong_key", "hex": wrong_key["hex"],
                "key": "definitely-the-wrong-key",
                "expect_kind": "integrity_failure"})

    # Length tampering without touching covered bytes would be a structural
    # error; provide a declared-length mismatch vector too.
    len_bad = bytearray(good_raw)
    struct.pack_into("!H", len_bad, 2, struct.unpack("!H", len_bad[2:4])[0] + 4)
    neg.append({"name": "declared_length_mismatch", "hex": bytes(len_bad).hex(),
                "expect_kind": "input_error"})

    return {"generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                              time.gmtime()),
            "rfc5769_xor_vectors": rfc,
            "messages": messages,
            "padding_vectors": padding,
            "error_messages": errors,
            "integrity_negative": neg}


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_fixtures(path):
    data = build_fixtures()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    print(f"wrote {path} ({len(data['messages'])} messages, "
          f"{len(data['integrity_negative'])} negative MI vectors)")


def cmd_decode(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        m = unmarshal(raw)
    except StunError as exc:
        print(json.dumps({"ok": False, "kind": exc.kind, "detail": exc.detail}))
        return 1
    out = {"ok": True, "method": m["method"], "class": m["class"],
           "txn_hex": m["txn"].hex(), "attributes": []}
    for t, v in m["attrs"]:
        entry = {"type": f"0x{t:04x}", "len": len(v), "value_hex": v.hex()}
        if t == ATTR_XOR_MAPPED_ADDRESS:
            entry["decoded"] = decode_xor_mapped_address(v, m["txn"])
        elif t == ATTR_MAPPED_ADDRESS:
            entry["decoded"] = decode_mapped_address(v)
        elif t == ATTR_ERROR_CODE:
            entry["decoded"] = decode_error_code(v)
        out["attributes"].append(entry)
    print(json.dumps(out, indent=2))
    return 0


def cmd_bind(args):
    """Python client issuing one Binding request to a (Go) server.

    Verifies independently: success class, echoed txn, MI when a key is set,
    and that XOR-MAPPED-ADDRESS equals this socket's own ephemeral endpoint.
    With --expect-error, sends an unknown comprehension-required attribute and
    asserts the given STUN error code. Prints one JSON verdict line.
    """
    fam = socket.AF_INET6 if args.net == "udp6" else socket.AF_INET
    if fam == socket.AF_INET6:
        # accept "[::1]:3478" or "::1:3478"-ish input
        raw = args.server.strip()
        if raw.startswith("["):
            host, port = raw.rsplit("]", 1)[0][1:], int(raw.rsplit(":", 1)[1])
        else:
            head, _, tail = raw.rpartition(":")
            host, port = head, int(tail)
    else:
        host, port = args.server.rsplit(":", 1)[0], int(args.server.rsplit(":", 1)[1])
    s = socket.socket(fam, socket.SOCK_DGRAM)
    s.bind(("::", 0) if fam == socket.AF_INET6 else ("0.0.0.0", 0))
    s.settimeout(2.0)
    txn = os.urandom(12)
    key = args.key.encode() if args.key else None

    if args.expect_error:
        attrs = [(0x0099, b"\xde\xad"), (ATTR_SOFTWARE, b"python-oracle")]
    else:
        attrs = [(ATTR_SOFTWARE, b"python-oracle")]
    if key:
        req = add_message_integrity(METHOD_BINDING, CLASS_REQUEST, txn, attrs, key)
    else:
        req = marshal(METHOD_BINDING, CLASS_REQUEST, txn, attrs)

    s.sendto(req, (host, port))
    try:
        data, _ = s.recvfrom(2048)
    except socket.timeout:
        print(json.dumps({"ok": False, "kind": "timeout"}))
        return 3

    try:
        m = unmarshal(data)
        if m["txn"] != txn:
            raise StunError("source_mismatch", "server echoed wrong txn")
        if key:
            verify_message_integrity(data, key)
        local_ip, local_port = s.getsockname()[0], s.getsockname()[1]
        verdict = {"ok": True, "txn_hex": txn.hex(), "local": [local_ip, local_port]}
        if args.expect_error:
            if m["class"] != CLASS_ERROR:
                raise StunError("input_error",
                                f"expected error class, got 0x{m['class']:04x}")
            ev = attr(m, ATTR_ERROR_CODE)
            if ev is None:
                raise StunError("input_error", "error response lacks ERROR-CODE")
            code, reason = decode_error_code(ev)
            if code != args.expect_error:
                raise StunError("input_error", f"expected {args.expect_error}, got {code}")
            unk = attr(m, ATTR_UNKNOWN_ATTRIBUTES)
            unknown = [struct.unpack("!H", unk[i:i+2])[0]
                       for i in range(0, len(unk or b""), 2)]
            verdict["error_code"] = code
            verdict["reason"] = reason
            verdict["unknown_attributes"] = [f"0x{u:04x}" for u in unknown]
        else:
            if m["class"] != CLASS_SUCCESS:
                raise StunError("input_error",
                                f"expected success class, got 0x{m['class']:04x}")
            xv = attr(m, ATTR_XOR_MAPPED_ADDRESS)
            if xv is None:
                raise StunError("input_error", "success lacks XOR-MAPPED-ADDRESS")
            ip, rport = decode_xor_mapped_address(xv, txn)
            # Server sees the client's loopback address; compare port exactly
            # and require the IP to be a loopback of the right family.
            if rport != local_port:
                raise StunError("input_error",
                                f"reflected port {rport} != client port {local_port}")
            ref = ipaddress.ip_address(ip)
            if not ref.is_loopback or ref.version != (6 if fam == socket.AF_INET6 else 4):
                raise StunError("input_error", f"reflected ip not loopback: {ip}")
            verdict["endpoint"] = [ip, rport]
            sw = attr(m, ATTR_SOFTWARE)
            if sw:
                verdict["server_software"] = sw.decode("utf-8", "replace")
        print(json.dumps(verdict))
        return 0
    except StunError as exc:
        print(json.dumps({"ok": False, "kind": exc.kind, "detail": exc.detail}))
        return 2


def cmd_reply(args):
    """Minimal UDP peer emitting one controlled datagram per received request.

    Modes:
      reflect - normal success response (used to cross-check over a real
                socket with an independent implementation)
      badtxn  - response with a wrong transaction id
      badsrc  - always replies from a second socket (source mismatch)
      tamper  - correct response but with one MI tag bit flipped
    """
    fam = socket.AF_INET6 if args.net == "udp6" else socket.AF_INET
    host = "::1" if fam == socket.AF_INET6 else "127.0.0.1"
    s = socket.socket(fam, socket.SOCK_DGRAM)
    s.bind((host, args.port))
    actual_port = s.getsockname()[1]
    print(f"oracle listening on {host}:{actual_port}", flush=True)

    extra = None
    if args.mode == "badsrc":
        extra = socket.socket(fam, socket.SOCK_DGRAM)
        extra.bind((host, 0))

    key = args.key.encode() if args.key else None
    while True:
        data, src = s.recvfrom(2048)
        try:
            m = unmarshal(data)
            txn = m["txn"]
            ip = "::1" if fam == socket.AF_INET6 else "127.0.0.1"
            xorv = encode_xor_mapped_address(ip, src[1], txn)
            attrs = [(ATTR_XOR_MAPPED_ADDRESS, xorv)]
            if args.mode == "badtxn":
                txn = bytes(b ^ 0xFF for b in txn)
            if key:
                resp = add_message_integrity(METHOD_BINDING, CLASS_SUCCESS,
                                             txn, attrs, key)
            else:
                resp = marshal(METHOD_BINDING, CLASS_SUCCESS, txn, attrs)
            if args.mode == "tamper":
                resp = bytearray(resp)
                resp[-1] ^= 0x80
                resp = bytes(resp)
            sock = extra if args.mode == "badsrc" else s
            sock.sendto(resp, src)
        except StunError as exc:
            sys.stderr.write(f"oracle dropped: {exc}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fixtures")
    p.add_argument("path")

    p = sub.add_parser("decode")
    p.add_argument("path")

    p = sub.add_parser("bind")
    p.add_argument("server", help="host:port (use [::1]:port for IPv6)")
    p.add_argument("--net", default="udp4", choices=["udp4", "udp6"])
    p.add_argument("--key", default="")
    p.add_argument("--expect-error", type=int, default=0,
                   help="send an unknown required attr and expect this STUN code")

    p = sub.add_parser("reply")
    p.add_argument("--mode", default="reflect",
                    choices=["reflect", "badtxn", "badsrc", "tamper"])
    p.add_argument("--net", default="udp4", choices=["udp4", "udp6"])
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--key", default="")

    args = parser.parse_args(argv)
    if args.cmd == "fixtures":
        cmd_fixtures(args.path)
        return 0
    if args.cmd == "decode":
        return cmd_decode(args.path)
    if args.cmd == "bind":
        return cmd_bind(args)
    if args.cmd == "reply":
        cmd_reply(args)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
