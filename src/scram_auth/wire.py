"""SCRAM-SHA-256 wire-message grammar (RFC 5802 section 2.3 / 5.1, RFC 7677).

The parser is deliberately strict:

* attributes must appear in the exact order prescribed by the grammar;
* duplicate attributes are rejected;
* only attributes this implementation understands are accepted (``m=``
  mandatory extensions and unknown attributes abort the exchange);
* base64 fields must decode without skipping characters;
* values must be printable ASCII and must not contain a raw comma.

Supported channel-binding scope (explicit, by construction):

* ``n`` / ``y`` GS2 flags with the ``SCRAM-SHA-256`` non-PLUS mechanism;
* ``p=tls-server-end-point`` with the ``SCRAM-SHA-256-PLUS`` mechanism, where
  the channel-binding value is the server-certificate hash supplied by the
  caller.  ``tls-unique`` / ``tls-exporter`` are **not** implemented.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from .errors import (
    ChannelBindingMismatch,
    InvalidEncoding,
    ProtocolViolation,
    UnsupportedChannelBinding,
    UnsupportedMechanism,
)

# Grammar terminal sets.
_PRINTABLE = set(range(0x21, 0x7F))  # U+0021..U+007E
VALUE_SEPARATOR = ord(",")
CB_TLS_SERVER_END_POINT = "tls-server-end-point"
SUPPORTED_CB_TYPES = (CB_TLS_SERVER_END_POINT,)


def _validate_value_chars(value: str, field: str) -> None:
    for ch in value:
        code = ord(ch)
        if code not in _PRINTABLE:
            raise ProtocolViolation(
                f"attribute {field!r} contains non-printable-ASCII character U+{code:04X}",
                detail={"field": field},
            )
        if code == VALUE_SEPARATOR:
            raise ProtocolViolation(
                f"attribute {field!r} contains a raw comma", detail={"field": field}
            )


def b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64_decode(value: str, field: str) -> bytes:
    if not value:
        raise InvalidEncoding(f"attribute {field!r} must not be empty", detail={"field": field})
    try:
        # validate=True rejects stray whitespace and non-alphabet characters.
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidEncoding(f"attribute {field!r} is not valid base64", detail={"field": field}) from exc


@dataclass(frozen=True)
class Gs2Header:
    """Parsed GS2 header of a client-first message."""

    cb_flag: str          # "n", "y" or "p"
    cb_type: str | None   # "tls-server-end-point" when cb_flag == "p"
    authzid: str | None   # decoded a= value if present

    @property
    def is_plus(self) -> bool:
        return self.cb_flag == "p"

    def serialise(self) -> bytes:
        if self.cb_flag == "p":
            if not self.cb_type:
                raise ProtocolViolation("p= GS2 flag requires a channel-binding type")
            flag = f"p={self.cb_type}"
        else:
            flag = self.cb_flag
        authzid = f",a={self.authzid}" if self.authzid is not None else ""
        return f"{flag},{authzid},".encode("utf-8")


def parse_gs2_header(raw: str) -> tuple[Gs2Header, str]:
    """Split ``flag[,a=authzid],<bare>`` and validate the GS2 flag.

    Returns the header and the bare client-first remainder.
    """
    parts = raw.split(",", 2)
    if len(parts) != 3:
        raise ProtocolViolation(
            "client-first message must contain a GS2 header ending in a comma",
            detail={"observed": raw[:16]},
        )
    flag, authzid_field, bare = parts
    authzid: str | None = None
    if authzid_field:
        if not authzid_field.startswith("a="):
            raise ProtocolViolation(
                "second GS2 field, if present, must be the authzid (a=)",
                detail={"observed": authzid_field[:16]},
            )
        authzid = authzid_field[2:]
        _validate_value_chars(authzid, "a")
    if flag in ("n", "y"):
        return Gs2Header(cb_flag=flag, cb_type=None, authzid=authzid), bare
    if flag.startswith("p="):
        cb_type = flag[2:]
        if cb_type not in SUPPORTED_CB_TYPES:
            raise UnsupportedChannelBinding(
                f"channel-binding type {cb_type!r} is not supported; "
                f"scope is {SUPPORTED_CB_TYPES} or non-PLUS (n/y)",
                detail={"observed": cb_type},
            )
        return Gs2Header(cb_flag="p", cb_type=cb_type, authzid=authzid), bare
    raise ProtocolViolation(f"unsupported GS2 channel-binding flag {flag!r}", detail={"observed": flag[:32]})


def _parse_attributes(raw: str, expected_order: tuple[str, ...], *, max_attributes: int) -> dict[str, str]:
    """Parse ``k=v`` pairs enforcing order and uniqueness.

    Only the keys in *expected_order* may occur; they must appear in that
    relative order.  Keys between them are not allowed (strict grammar).
    """
    if not raw:
        raise ProtocolViolation("empty attribute list")
    chunks = raw.split(",")
    if len(chunks) > max_attributes:
        raise ProtocolViolation(
            f"too many attributes: {len(chunks)} > {max_attributes}",
            detail={"count": len(chunks), "limit": max_attributes},
        )
    result: dict[str, str] = {}
    cursor = 0
    for chunk in chunks:
        if len(chunk) < 2 or chunk[1] != "=" or not chunk[0].isascii() or not chunk[0].isalpha():
            raise ProtocolViolation(f"malformed attribute {chunk[:16]!r}")
        key, value = chunk[0], chunk[2:]
        if key in result:
            raise ProtocolViolation(f"duplicate attribute {key!r}", detail={"attribute": key})
        if cursor >= len(expected_order) or key != expected_order[cursor]:
            if key == "m":
                raise UnsupportedMechanism(
                    "mandatory extension (m=) is not understood by this implementation",
                    detail={"attribute": "m"},
                )
            raise ProtocolViolation(
                f"unexpected attribute {key!r}; expected {expected_order[cursor:cursor + 1] or 'end'}",
                detail={"attribute": key, "expected_from": expected_order[cursor:]},
            )
        cursor += 1
        _validate_value_chars(value, key)
        result[key] = value
    missing = [k for k in expected_order if k not in result]
    if missing:
        raise ProtocolViolation(f"missing required attribute(s): {','.join(missing)}", detail={"missing": missing})
    return result


def unescape_username(wire_name: str) -> str:
    """Reverse RFC 5802 2.2 ``=2C`` / ``=3D`` escaping, validating sequences."""
    out: list[str] = []
    i = 0
    while i < len(wire_name):
        ch = wire_name[i]
        if ch == "=":
            seq = wire_name[i:i + 3]
            if seq == "=2C":
                out.append(",")
            elif seq == "=3D":
                out.append("=")
            else:
                raise ProtocolViolation(
                    f"invalid username escape sequence {seq!r}", detail={"sequence": seq}
                )
            i += 3
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def escape_username(name: str) -> str:
    return name.replace("=", "=3D").replace(",", "=2C")


@dataclass(frozen=True)
class ClientFirst:
    gs2: Gs2Header
    username: str
    client_nonce: str
    raw: str
    bare: str  # client-first-message-bare (used in AuthMessage)


@dataclass(frozen=True)
class ServerFirst:
    nonce: str
    salt: bytes
    iteration_count: int
    raw: str


@dataclass(frozen=True)
class ClientFinal:
    channel_binding: bytes
    nonce: str
    proof: bytes
    raw_without_proof: str
    raw: str


def parse_client_first(message: str, *, max_attributes: int) -> ClientFirst:
    gs2, bare = parse_gs2_header(message)
    attrs = _parse_attributes(bare, ("n", "r"), max_attributes=max_attributes)
    wire_name = attrs["n"]
    if not wire_name:
        raise ProtocolViolation("username (n=) must not be empty")
    if not attrs["r"]:
        raise ProtocolViolation("client nonce (r=) must not be empty")
    username = unescape_username(wire_name)
    return ClientFirst(gs2=gs2, username=username, client_nonce=attrs["r"], raw=message, bare=bare)


def build_server_first(nonce: str, salt: bytes, iteration_count: int) -> str:
    # Concatenation (not an f-string) so the literal "s=" attribute key is safe.
    return "r=" + nonce + ",s=" + b64_encode(salt) + ",i=" + str(iteration_count)


def parse_server_first(message: str, *, max_attributes: int) -> ServerFirst:
    attrs = _parse_attributes(message, ("r", "s", "i"), max_attributes=max_attributes)
    salt = b64_decode(attrs["s"], "s")
    try:
        iterations = int(attrs["i"])
    except ValueError as exc:
        raise ProtocolViolation("iteration count (i=) must be a decimal integer") from exc
    if iterations < 1 or str(iterations) != attrs["i"]:
        raise ProtocolViolation("iteration count (i=) must be a positive canonical integer")
    return ServerFirst(nonce=attrs["r"], salt=salt, iteration_count=iterations, raw=message)


def channel_binding_value(gs2: Gs2Header, tls_end_point_hash: bytes | None) -> bytes:
    """Build the ``c=`` payload: GS2 header || channel binding value.

    For ``n``/``y`` the binding value is empty; for ``p=tls-server-end-point``
    the caller must supply the server certificate hash (RFC 5929).
    """
    header = gs2.serialise()
    if gs2.cb_flag in ("n", "y"):
        if tls_end_point_hash:
            raise ChannelBindingMismatch("cert hash supplied for a non-PLUS GS2 flag")
        return header
    if gs2.cb_type == CB_TLS_SERVER_END_POINT:
        if not tls_end_point_hash:
            raise ChannelBindingMismatch(
                "p=tls-server-end-point requires the server certificate hash, none was provided"
            )
        if len(tls_end_point_hash) not in (32, 20):
            raise ChannelBindingMismatch(
                "tls-server-end-point hash must be a 32-byte SHA-256 (or 20-byte SHA-1) digest",
                detail={"length": len(tls_end_point_hash)},
            )
        return header + tls_end_point_hash
    raise UnsupportedChannelBinding(f"unsupported GS2 flag {gs2.cb_flag!r}")


def build_client_final_without_proof(cb_value: bytes, nonce: str) -> str:
    return f"c={b64_encode(cb_value)},r={nonce}"


def build_client_final(without_proof: str, proof: bytes) -> str:
    return f"{without_proof},p={b64_encode(proof)}"


def parse_client_final(message: str, *, max_attributes: int) -> ClientFinal:
    attrs = _parse_attributes(message, ("c", "r", "p"), max_attributes=max_attributes)
    cb = b64_decode(attrs["c"], "c")
    proof = b64_decode(attrs["p"], "p")
    head = message.rsplit(",p=", 1)[0]
    return ClientFinal(
        channel_binding=cb,
        nonce=attrs["r"],
        proof=proof,
        raw_without_proof=head,
        raw=message,
    )


def build_server_final(server_sig: bytes) -> str:
    return f"v={b64_encode(server_sig)}"


def parse_server_final_verifier(message: str, *, max_attributes: int) -> bytes:
    # Server-final may carry e= (error) or extensions; success must be v=.
    first = message.split(",", 1)[0]
    if first.startswith("e="):
        raise ProtocolViolation(f"server reported authentication error: {first[2:]}", detail={"server_error": first[2:]})
    attrs = _parse_attributes(message, ("v",), max_attributes=max_attributes)
    return b64_decode(attrs["v"], "v")
