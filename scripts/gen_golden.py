#!/usr/bin/env python3
"""Generate CoAP wire-format golden vectors with an INDEPENDENT minimal encoder.

These vectors cross-check the Go codec: Go never generates the expected bytes,
and Go decoding is asserted against structures this script emits. The tiny
encoder here is deliberately written from scratch (not shared with Go).
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(
    os.path.join(HERE, "..", "test", "testdata", "golden", "wire_vectors.json")
)

# RFC 7252
T_CON, T_NON, T_ACK, T_RST = 0, 1, 2, 3
M_GET, M_POST, M_PUT, M_DEL = 1, 2, 3, 4
OPT_URI_PATH, OPT_CONTENT_FORMAT, OPT_ETAG = 11, 12, 4
OPT_BLOCK1, OPT_BLOCK2, OPT_SIZE2 = 27, 23, 28


def uvar(n: int) -> bytes:
    out = bytearray()
    while True:
        k = n % 256
        n //= 256
        if n > 0:
            out.append(k)
        else:
            out.append(k)
            return bytes(out)


def opt_delta(delta: int, ext: bytes) -> int:
    if delta < 13:
        return delta
    return 13 if len(ext) == 1 else 14


def encode_option(number: int, value: bytes, prev: int):
    delta = number - prev
    if delta < 13:
        d = delta
        dext = b""
    elif delta <= 255 + 13:
        d, dext = 13, bytes((delta - 13,))
    else:
        d, dext = 14, struct.pack("!H", delta - 269)
    ln = len(value)
    if ln < 13:
        l, lext = ln, b""
    elif ln <= 255 + 13:
        l, lext = 13, bytes((ln - 13,))
    else:
        l, lext = 14, struct.pack("!H", ln - 269)
    return bytes((d << 4 | l,)) + dext + lext + value


def block_value(num: int, more: bool, szx: int) -> int:
    # RFC 7959: NUM in high bits, M in bit 3, SZX in bits 0..2.
    return (num << 4) | (0b1000 if more else 0) | (szx & 0x7)


def encode_message(ver, mtype, tkl, code, mid, token: bytes, options, payload: bytes):
    first = (ver << 6) | (mtype << 4) | tkl
    out = bytearray((first, code, (mid >> 8) & 0xFF, mid & 0xFF))
    out += token
    prev = 0
    # Stable sort by option NUMBER only: repeated options (e.g. Uri-Path)
    # must retain their insertion/segment order on the wire.
    for number, value in sorted(options, key=lambda o: o[0]):
        out += encode_option(number, value, prev)
        prev = number
    if payload:
        out.append(0xFF)
        out += payload
    return bytes(out)


def uintopt(v: int) -> bytes:
    b = bytearray()
    while v:
        b.append(v & 0xFF)
        v >>= 8
    return bytes(reversed(b)) or b"\x00"


def cases():
    out = []

    def add(name, raw, parsed, note):
        out.append({"name": name, "note": note,
                    "hex": raw.hex(), "parsed": parsed})

    # 1: empty ping/pong ACK
    raw = encode_message(1, T_ACK, 0, 0, 0x1234, b"", [], b"")
    add("empty_ack", raw, {"type": "ACK", "code": "0.00", "mid": 4660,
                           "token_hex": "", "payload_hex": "",
                           "options": []}, "empty message")

    # 2: CON GET /time? no payload, 2-byte token
    opts = [(OPT_URI_PATH, b"time")]
    raw = encode_message(1, T_CON, 2, M_GET, 0xABCD, b"\x01\x02", opts, b"")
    add("con_get_time", raw, {"type": "CON", "code": "GET", "mid": 0xABCD,
        "token_hex": "0102", "payload_hex": "",
        "options": [{"number": 11, "name": "Uri-Path", "value_hex": "74696d65"}]},
        "basic CON GET, token independent of mid")

    # 3: 2.05 Content CON response with payload, long Uri-Path using delta ext
    opts = [(OPT_CONTENT_FORMAT, uintopt(0)), (OPT_URI_PATH, b"a"),
            (OPT_URI_PATH, b"longsegment"), (OPT_URI_PATH, b"z")]
    body = b"hello-blockwise"
    raw = encode_message(1, T_CON, 4, 69, 0x0001, b"\xde\xad\xbe\xef", opts, body)
    add("con_content_multipath", raw, {"type": "CON", "code": "2.05",
        "mid": 1, "token_hex": "deadbeef", "payload_hex": body.hex(),
        "options": [
            {"number": 11, "name": "Uri-Path", "value_hex": "61"},
            {"number": 11, "name": "Uri-Path", "value_hex":
             b"longsegment".hex()},
            {"number": 11, "name": "Uri-Path", "value_hex": "7a"},
            {"number": 12, "name": "Content-Format", "value_hex": "00"}]},
        "repeated options + payload marker")

    # 4: Block1 num=3 more=1 szx=2 (block size 32) => (3<<4)|0b1010 = 0x3A
    v = block_value(3, True, 2)
    opts = [(OPT_BLOCK1, uintopt(v)), (OPT_URI_PATH, b"up")]
    body = bytes(range(32))
    raw = encode_message(1, T_CON, 1, M_PUT, 0x7777, b"\x09", opts, body)
    add("block1_mid_put", raw, {"type": "CON", "code": "PUT", "mid": 0x7777,
        "token_hex": "09", "payload_hex": body.hex(),
        "options": [
            {"number": 11, "name": "Uri-Path", "value_hex": "7570"},
            {"number": 27, "name": "Block1", "value_hex": "3a",
             "block": {"num": 3, "more": True, "szx": 2, "size": 64}}]},
        "Block1 num3/more/szx2(size64) raw value 0x3a")

    # 5: Block2 num=4100 more=0 szx=5 (512): exercises 16-bit block num
    v = block_value(4100, False, 5)
    opts = [(OPT_BLOCK2, uintopt(v))]
    raw = encode_message(1, T_ACK, 0, 69, 0x0100, b"", opts, b"Z" * 512)
    add("block2_bignum_ack", raw, {"type": "ACK", "code": "2.05",
        "mid": 256, "token_hex": "", "payload_hex": ("5a" * 512),
        "options": [{"number": 23, "name": "Block2",
                     "value_hex": uintopt(v).hex(),
                     "block": {"num": 4100, "more": False, "szx": 5,
                               "size": 512}}]},
        "block num above 20 bits bounds in 3-byte option")

    # 6: ETag + Block2 in a 2.03 Valid response
    opts = [(OPT_ETAG, b"\x00\xca\xfe"), (OPT_BLOCK2, uintopt(0))]
    raw = encode_message(1, T_ACK, 0, 67, 0x0200, b"", opts, b"")
    add("valid_etag_block2", raw, {"type": "ACK", "code": "2.03",
        "mid": 512, "token_hex": "", "payload_hex": "",
        "options": [
            {"number": 4, "name": "ETag", "value_hex": "00cafe"},
            {"number": 23, "name": "Block2", "value_hex": "00",
             "block": {"num": 0, "more": False, "szx": 0, "size": 16}}]},
        "2.03 Valid carries ETag")

    # 7: Size2 option (uint 5000 => 0x1388)
    opts = [(OPT_SIZE2, uintopt(5000))]
    raw = encode_message(1, T_ACK, 0, (4 << 5) | 13, 0x0300, b"", opts, b"")
    add("size2_413", raw, {"type": "ACK", "code": "4.13",
        "mid": 768, "token_hex": "", "payload_hex": "",
        "options": [{"number": 28, "name": "Size2", "value_hex": "1388"}]},
        "Request Entity Too Large advertises Size2")

    # 8: token != mid semantic stress: same MID twice (retransmit), distinct
    # token values are legal across separate requests; encode one more.
    opts = [(OPT_URI_PATH, b"t"), (OPT_URI_PATH, b"5")]
    raw = encode_message(1, T_CON, 5, M_GET, 0x0042,
                         bytes.fromhex("aabbccddee"), opts, b"")
    add("con_5byte_token", raw, {"type": "CON", "code": "GET", "mid": 0x42,
        "token_hex": "aabbccddee", "payload_hex": "",
        "options": [{"number": 11, "name": "Uri-Path", "value_hex": "74"},
                    {"number": 11, "name": "Uri-Path",
                     "value_hex": "35"}]},
        "5-byte token, short mid: proves token/mid independence")
    return out


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    data = {"rfc": ["RFC 7252", "RFC 7959"], "cases": cases()}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")
    print(f"wrote {OUT} with {len(data['cases'])} vectors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
