#!/usr/bin/env python3
"""Independent STUN (RFC 5389) oracle for cross-validation of the Go stack.

This program is written from the RFC text alone and shares NO code with the
Go implementation under test. Python 3 standard library only.

Wire protocol implemented (Binding subset only; no TURN relay):
  * 20-byte header, 32-bit magic cookie 0x2112A442, 96-bit transaction id
  * TLV attributes with four-byte alignment (length excludes padding)
  * XOR-MAPPED-ADDRESS for IPv4/IPv6 (port XOR cookie high 16 bits,
    IPv4 XOR cookie, IPv6 XOR cookie||transaction-id)
  * MESSAGE-INTEGRITY = HMAC-SHA1(key, message up to MI with Length advanced
    by 24)
  * FINGERPRINT = CRC32-IEEE(message up to FP, Length advanced by 8) XOR
    0x5354554E
  * unknown comprehension-required attributes (type < 0x8000) are rejected

Sub-commands (JSON over stdin/stdout, one JSON object per line where noted):
  emit      -> print a JSON array of independently generated test cases
  judge     -> read JSON-lines requests {"name","wire_hex","key_b64"?},
               print a JSON-lines verdict for each

Verdict "kind" uses the same vocabulary as the Go error taxonomy:
  ok | input | integrity | compute
(state / exhaustion categories are properties of the client state machine and
cannot be observed from a datagram, so they are tested in Go instead.)
"""

import base64
import binascii
import hashlib
import hmac as hmac_mod
import json
import struct
import sys

COOKIE = 0x2112A442
FP_XOR = 0x5354554E
HEADER_LEN = 20
BIND_REQUEST = 0x0001
BIND_RESPONSE = 0x0101
BIND_ERROR = 0x0111
KNOWN_TYPES = {BIND_REQUEST, BIND_RESPONSE, BIND_ERROR}

ATTR_MAPPED_ADDRESS = 0x0001
ATTR_USERNAME = 0x0006
ATTR_MESSAGE_INTEGRITY = 0x0008
ATTR_ERROR_CODE = 0x0009
ATTR_UNKNOWN_ATTRIBUTES = 0x000A
ATTR_REALM = 0x0014
ATTR_NONCE = 0x0015
ATTR_XOR_MAPPED_ADDRESS = 0x0020
ATTR_SOFTWARE = 0x8022
ATTR_FINGERPRINT = 0x8028

KNOWN_REQUIRED = {
    ATTR_MAPPED_ADDRESS, ATTR_USERNAME, ATTR_MESSAGE_INTEGRITY,
    ATTR_ERROR_CODE, ATTR_UNKNOWN_ATTRIBUTES, ATTR_REALM, ATTR_NONCE,
    ATTR_XOR_MAPPED_ADDRESS,
    0x0024, 0x0025,  # RFC 8445 PRIORITY, USE-CANDIDATE
}


def pad_len(n):
    return (4 - n % 4) % 4


def xor_address_value(ip_packed, family, port, txid):
    """Independently build an XOR-MAPPED-ADDRESS value from the RFC formula."""
    port_x = port ^ (COOKIE >> 16)
    if family == 0x01:
        mask = struct.pack("!I", COOKIE)
    else:
        mask = struct.pack("!I", COOKIE) + txid
    body = bytes(a ^ b for a, b in zip(ip_packed, mask))
    return b"\x00" + bytes([family]) + struct.pack("!H", port_x) + body


def attr(atype, value, pad_byte=0x00):
    out = struct.pack("!HH", atype, len(value)) + value
    out += bytes([pad_byte]) * pad_len(len(value))
    return out


def encode(msg_type, txid, attr_blobs, key=None, fingerprint=False):
    """attr_blobs are already-framed TLV bytes (so padding is test-controlled)."""
    assert len(txid) == 12
    body = b"".join(attr_blobs)
    if key is None:
        head = struct.pack("!HHI12s", msg_type, len(body), COOKIE, txid)
        return head + body
    declared = len(body) + 4 + 20  # length including MI TLV
    mi_input = struct.pack("!HHI12s", msg_type, declared, COOKIE, txid) + body
    mi = hmac_mod.new(key, mi_input, hashlib.sha1).digest()
    body += attr(ATTR_MESSAGE_INTEGRITY, mi)
    if fingerprint:
        declared = len(body) + 4 + 4  # MI-bearing body + FP TLV
        fp_input = struct.pack("!HHI12s", msg_type, declared, COOKIE, txid) + body
        crc = (binascii.crc32(fp_input) & 0xFFFFFFFF) ^ FP_XOR
        body += attr(ATTR_FINGERPRINT, struct.pack("!I", crc))
    head = struct.pack("!HHI12s", msg_type, len(body), COOKIE, txid)
    return head + body


def decode(buf, key=None):
    """Return a verdict dict. Never raises for protocol malformation."""
    v = {"ok": False, "kind": "input", "reason": "", "msg_type": None,
         "txid": None, "xor": None, "integrity_ok": False,
         "fingerprint_ok": False, "unknown_required": [], "error_code": None}
    try:
        if len(buf) < HEADER_LEN:
            v["reason"] = "shorter than 20-byte header"
            return v
        if buf[0] & 0xC0:
            v["reason"] = "leading two bits not zero"
            return v
        mtype, mlen, cookie = struct.unpack("!HHI", buf[:8])
        txid = buf[8:20]
        v["msg_type"] = mtype
        v["txid"] = txid.hex()
        if mtype not in KNOWN_TYPES:
            v["reason"] = "unknown method/class 0x%04x" % mtype
            return v
        if cookie != COOKIE:
            v["reason"] = "magic cookie mismatch"
            return v
        if mlen != len(buf) - HEADER_LEN:
            v["reason"] = "declared length %d != body %d" % (
                mlen, len(buf) - HEADER_LEN)
            return v

        p = HEADER_LEN
        end = len(buf)
        parsed = []
        unknown_required = []
        mi = None
        mi_prefix_end = None   # wire position where MI TLV begins
        mi_declared_len = None # Length field value when verifying MI
        fp = None
        fp_prefix_end = None   # wire position where FP TLV begins
        seen_mi = False
        while p < end:
            if end - p < 4:
                v["reason"] = "truncated attribute header"
                return v
            atype, alen = struct.unpack("!HH", buf[p:p + 4])
            vstart, vend = p + 4, p + 4 + alen
            if vend > end:
                v["reason"] = "attribute value runs past message end"
                return v
            if seen_mi and atype != ATTR_FINGERPRINT:
                v["reason"] = "attribute other than FINGERPRINT follows MI"
                return v
            value = buf[vstart:vend]
            parsed.append((atype, value))
            if atype == ATTR_MESSAGE_INTEGRITY:
                if alen != 20:
                    v["reason"] = "MI must be 20 bytes"
                    return v
                seen_mi = True
                mi = value
                mi_prefix_end = p
                mi_declared_len = vend - HEADER_LEN
            elif atype == ATTR_FINGERPRINT:
                if alen != 4:
                    v["reason"] = "FINGERPRINT must be 4 bytes"
                    return v
                fp = value
                fp_prefix_end = p  # CRC input stops before FP TLV
            elif atype < 0x8000 and atype not in KNOWN_REQUIRED:
                unknown_required.append(atype)
            p = vend + pad_len(alen)
            if p > end:
                v["reason"] = "attribute padding runs past message end"
                return v

        v["unknown_required"] = ["0x%04x" % t for t in unknown_required]
        if unknown_required:
            v["kind"] = "integrity"
            v["reason"] = "unknown comprehension-required attribute"
            return v

        if mi is not None:
            if key is None:
                v["kind"] = "compute"
                v["reason"] = "MI present but no key supplied"
                return v
            mi_input = bytearray(buf[:mi_prefix_end])
            # Rewrite Length to cover everything up to end of MI.
            struct.pack_into("!H", mi_input, 2, mi_declared_len)
            want = hmac_mod.new(key, bytes(mi_input), hashlib.sha1).digest()
            if not hmac_mod.compare_digest(want, mi):
                v["kind"] = "integrity"
                v["reason"] = "MESSAGE-INTEGRITY mismatch"
                return v
            v["integrity_ok"] = True
            if fp is not None:
                fp_input = bytearray(buf[:fp_prefix_end])
                struct.pack_into(
                    "!H", fp_input, 2, fp_prefix_end + 8 - HEADER_LEN)
                want_crc = struct.unpack("!I", fp)[0]
                got_crc = (binascii.crc32(bytes(fp_input)) & 0xFFFFFFFF) ^ FP_XOR
                if want_crc != got_crc:
                    v["kind"] = "integrity"
                    v["reason"] = "FINGERPRINT mismatch"
                    return v
                v["fingerprint_ok"] = True

        for atype, value in parsed:
            if atype == ATTR_XOR_MAPPED_ADDRESS:
                xor = parse_xor_address(value, txid)
                if isinstance(xor, str):
                    v["reason"] = xor
                    return v
                v["xor"] = xor
            if atype == ATTR_ERROR_CODE and len(value) >= 4:
                v["error_code"] = (value[2] & 0x07) * 100 + value[3]

        v["ok"] = True
        v["kind"] = "ok"
        return v
    except Exception as exc:  # an oracle crash is itself a compute failure
        v["kind"] = "compute"
        v["reason"] = "oracle exception: %r" % exc
        return v


def parse_xor_address(value, txid):
    if len(value) < 4 or value[0] != 0:
        return "malformed XOR-MAPPED-ADDRESS"
    family = value[1]
    port = struct.unpack("!H", value[2:4])[0] ^ (COOKIE >> 16)
    if family == 0x01:
        if len(value) != 8:
            return "IPv4 XOR address must be 8 bytes"
        ip = bytes(a ^ b for a, b in zip(value[4:8], struct.pack("!I", COOKIE)))
        return {"family": "ipv4", "ip": ".".join(map(str, ip)), "port": port}
    if family == 0x02:
        if len(value) != 20:
            return "IPv6 XOR address must be 20 bytes"
        mask = struct.pack("!I", COOKIE) + txid
        ip = bytes(a ^ b for a, b in zip(value[4:20], mask))
        return {"family": "ipv6",
                "ip": ":".join("%02x%02x" % (ip[i], ip[i + 1])
                                for i in range(0, 16, 2)),
                "port": port}
    return "unknown address family 0x%02x" % family


# ---------------------------------------------------------------------------
# Independent case generation
# ---------------------------------------------------------------------------

KEY = b"VOkJxbRl1RmTxUk/WvJxBt"
TXID = bytes.fromhex("b7e7a701bc34d686fa87dfae")


def emit_cases():
    cases = []

    def add(name, wire, expectation, key=None):
        cases.append({"name": name, "wire_hex": wire.hex(),
                      "key_b64": base64.b64encode(key).decode() if key else None,
                      "expect": expectation})

    v4 = bytes([192, 0, 2, 1])
    v6 = bytes.fromhex("20010db8123456780011223344556677")

    # 1. Valid IPv4 success with MI+FP, NONZERO padding bytes on a 9-byte
    #    SOFTWARE value (9 -> 3 pad bytes 0xDD) to prove padding handling.
    blob = attr(ATTR_SOFTWARE, b"test vect", pad_byte=0xDD)
    blob += attr(ATTR_XOR_MAPPED_ADDRESS, xor_address_value(v4, 0x01, 32853, TXID))
    wire = encode(BIND_RESPONSE, TXID, [blob], key=KEY, fingerprint=True)
    add("valid_ipv4_mi_fp_padding9", wire,
        {"ok": True, "kind": "ok", "msg_type": BIND_RESPONSE,
         "xor": {"family": "ipv4", "ip": "192.0.2.1", "port": 32853},
         "integrity_ok": True, "fingerprint_ok": True}, KEY)

    # 2. Valid IPv6 success.
    blob = attr(ATTR_SOFTWARE, b"sv")  # 2 bytes -> 2 pad
    blob += attr(ATTR_XOR_MAPPED_ADDRESS, xor_address_value(v6, 0x02, 32853, TXID))
    wire = encode(BIND_RESPONSE, TXID, [blob], key=KEY, fingerprint=True)
    add("valid_ipv6_mi_fp_padding2", wire,
        {"ok": True, "kind": "ok", "msg_type": BIND_RESPONSE,
         "xor": {"family": "ipv6",
                 "ip": "2001:0db8:1234:5678:0011:2233:4455:6677",
                 "port": 32853},
         "integrity_ok": True, "fingerprint_ok": True}, KEY)

    # 3. No-integrity request with 1-byte and 5-byte attribute values.
    blob = attr(ATTR_SOFTWARE, b"x", pad_byte=0x01)
    blob += attr(ATTR_USERNAME, b"abcde", pad_byte=0xEE)  # 5 -> 3 pad
    wire = encode(BIND_REQUEST, TXID, [blob])
    add("valid_request_no_integrity_padding1_5", wire,
        {"ok": True, "kind": "ok", "msg_type": BIND_REQUEST,
         "integrity_ok": False, "fingerprint_ok": False}, None)

    # 4. Unknown comprehension-OPTIONAL attribute (0x8031) must be accepted.
    blob = attr(0x8031, b"\x00\x00\x00")
    wire = encode(BIND_REQUEST, TXID, [blob])
    add("unknown_optional_accepted", wire,
        {"ok": True, "kind": "ok", "msg_type": BIND_REQUEST}, None)

    # 5. Unknown comprehension-REQUIRED attribute (0x0bad) must be rejected.
    blob = attr(0x0BAD, b"\x01\x02")
    wire = encode(BIND_REQUEST, TXID, [blob])
    add("unknown_required_rejected", wire,
        {"ok": False, "kind": "integrity",
         "unknown_required": ["0x0bad"]}, None)

    # 6. Leading bits set.
    bad = bytes([0xC0]) + wire[1:]
    add("malformed_leading_bits", bad,
        {"ok": False, "kind": "input"}, None)

    # 7. Bad magic cookie.
    bad = wire[:4] + b"\x00\x00\x00\x00" + wire[8:]
    add("malformed_bad_cookie", bad,
        {"ok": False, "kind": "input"}, None)

    # 8. Declared length inconsistent (says one byte more).
    bad = wire[:2] + struct.pack("!H", struct.unpack("!H", wire[2:4])[0] + 1) + wire[4:]
    add("malformed_length_mismatch", bad,
        {"ok": False, "kind": "input"}, None)

    # 9. Truncated attribute value (chop last 3 bytes of a valid message).
    blob = attr(ATTR_SOFTWARE, b"abc")
    good = encode(BIND_REQUEST, TXID, [blob])
    add("malformed_truncated_attr", good[:-3],
        {"ok": False, "kind": "input"}, None)

    # 10. Integrity tamper: flip one byte inside the XOR address value
    #     (wire 25: value region is bytes 24..31) and recompute nothing.
    blob = attr(ATTR_XOR_MAPPED_ADDRESS, xor_address_value(v4, 0x01, 32853, TXID))
    good = encode(BIND_RESPONSE, TXID, [blob], key=KEY, fingerprint=True)
    tampered = good[:25] + bytes([good[25] ^ 0xFF]) + good[26:]
    add("tamper_integrity", tampered,
        {"ok": False, "kind": "integrity"}, KEY)

    # 11. Fingerprint tamper: flip last byte.
    tampered = good[:-1] + bytes([good[-1] ^ 0x01])
    add("tamper_fingerprint", tampered,
        {"ok": False, "kind": "integrity"}, KEY)

    # 12. MI present but verifier holds no key.
    add("mi_present_no_key", good,
        {"ok": False, "kind": "compute"}, None)

    # 13. Garbage datagram.
    add("malformed_short", b"\x00\x01\x00",
        {"ok": False, "kind": "input"}, None)

    # 14. A request with a DIFFERENT transaction id (decodable; state machine
    #     must still reject it as unmatched - codec result shown for record).
    other = bytes.fromhex("00112233445566778899aabb")
    wire = encode(BIND_RESPONSE, other, [attr(ATTR_SOFTWARE, b"z")],
                  key=None, fingerprint=False)
    add("decodable_foreign_txid", wire,
        {"ok": True, "kind": "ok", "msg_type": BIND_RESPONSE,
         "txid": "00112233445566778899aabb"}, None)

    return cases


def judge_request(req):
    wire = binascii.unhexlify(req["wire_hex"])
    key = base64.b64decode(req["key_b64"]) if req.get("key_b64") else None
    verdict = decode(wire, key)
    out = {"name": req.get("name", ""),
           "ok": verdict["ok"], "kind": verdict["kind"],
           "reason": verdict["reason"], "msg_type": verdict["msg_type"],
           "txid": verdict["txid"], "xor": verdict["xor"],
           "integrity_ok": verdict["integrity_ok"],
           "fingerprint_ok": verdict["fingerprint_ok"],
           "unknown_required": verdict["unknown_required"],
           "error_code": verdict["error_code"]}
    return out


def main(argv):
    if len(argv) != 2 or argv[1] not in ("emit", "judge"):
        sys.stderr.write(__doc__)
        return 2
    if argv[1] == "emit":
        json.dump(emit_cases(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        sys.stdout.write(json.dumps(judge_request(req), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
