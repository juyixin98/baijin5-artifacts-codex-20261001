package codec

import (
	"fmt"
	"io"
	"net/netip"
)

// ---------------------------------------------------------------------------
// Pure encoders
// ---------------------------------------------------------------------------

// EncodeMethodSelection builds VER, METHOD.
func EncodeMethodSelection(method byte) []byte { return []byte{Version5, method} }

// EncodeNoAcceptable builds the "no acceptable methods" reply (0xFF).
func EncodeNoAcceptable() []byte { return []byte{Version5, MethodNoAccept} }

// EncodeUserPassReply builds the RFC 1929 sub-negotiation reply.
func EncodeUserPassReply(status byte) []byte { return []byte{UserPassVersion, status} }

// EncodeMethods builds a client greeting (used by tests and the oracle).
func EncodeMethods(methods ...byte) []byte {
	if len(methods) > 255 {
		panic("codec: too many methods")
	}
	out := make([]byte, 0, 2+len(methods))
	out = append(out, Version5, byte(len(methods)))
	out = append(out, methods...)
	return out
}

// EncodeUserPass builds an RFC 1929 credential message.
func EncodeUserPass(username, password string) []byte {
	if len(username) > 255 || len(password) > 255 {
		panic("codec: username/password too long")
	}
	out := make([]byte, 0, 3+len(username)+len(password))
	out = append(out, UserPassVersion, byte(len(username)))
	out = append(out, username...)
	out = append(out, byte(len(password)))
	out = append(out, password...)
	return out
}

// EncodeAddr serializes DST.ADDR + DST.PORT (without the ATYP octet).
func EncodeAddr(a Addr) []byte {
	port := []byte{byte(a.Port >> 8), byte(a.Port)}
	switch a.Atyp {
	case AtypIPv4:
		v4 := a.IP.As4()
		out := make([]byte, 0, IPv4AddrLen+PortLen)
		out = append(out, v4[:]...)
		return append(out, port...)
	case AtypIPv6:
		v16 := a.IP.As16()
		out := make([]byte, 0, IPv6AddrLen+PortLen)
		out = append(out, v16[:]...)
		return append(out, port...)
	case AtypDomain:
		out := make([]byte, 0, 1+len(a.Domain)+PortLen)
		out = append(out, byte(len(a.Domain)))
		out = append(out, a.Domain...)
		return append(out, port...)
	default:
		panic(fmt.Sprintf("codec: cannot encode atyp 0x%02x", a.Atyp))
	}
}

// EncodeRequest builds a full SOCKS5 request frame.
func EncodeRequest(cmd byte, dest Addr) []byte {
	out := make([]byte, 0, 4+len(EncodeAddr(dest)))
	out = append(out, Version5, cmd, RSV, dest.Atyp)
	out = append(out, EncodeAddr(dest)...)
	return out
}

// EncodeReply builds VER,REP,RSV,ATYP,BND.ADDR,BND.PORT.
func EncodeReply(rep byte, bind Addr) []byte {
	out := make([]byte, 0, 4+len(EncodeAddr(bind)))
	out = append(out, Version5, rep, RSV, bind.Atyp)
	out = append(out, EncodeAddr(bind)...)
	return out
}

// ZeroBindAddr is the 0.0.0.0:0 bound address used in CONNECT replies.
func ZeroBindAddr() Addr {
	return Addr{Atyp: AtypIPv4, IP: netip.MustParseAddr("0.0.0.0"), Port: 0}
}

// IPv4Addr builds an IPv4 destination.
func IPv4Addr(ip string, port uint16) Addr {
	return Addr{Atyp: AtypIPv4, IP: netip.MustParseAddr(ip), Port: port}
}

// IPv6Addr builds an IPv6 destination.
func IPv6Addr(ip string, port uint16) Addr {
	return Addr{Atyp: AtypIPv6, IP: netip.MustParseAddr(ip), Port: port}
}

// DomainAddr builds a domain destination.
func DomainAddr(name string, port uint16) Addr {
	return Addr{Atyp: AtypDomain, Domain: name, Port: port}
}

// ---------------------------------------------------------------------------
// Pure decoders (the single source of truth shared by stream readers)
// ---------------------------------------------------------------------------

// DecodeMethods parses a complete greeting frame.
func DecodeMethods(b []byte) (MethodRequest, error) {
	if len(b) < 2 {
		return MethodRequest{}, &DecodeError{Stage: "method_negotiation", Reason: ReasonTruncated}
	}
	if b[0] != Version5 {
		return MethodRequest{}, &DecodeError{
			Stage:  "method_negotiation",
			Reason: ReasonUnsupportedVersion,
			Err:    fmt.Errorf("got VER=0x%02x want 0x%02x", b[0], Version5),
		}
	}
	n := int(b[1])
	if n == 0 {
		return MethodRequest{}, &DecodeError{Stage: "method_negotiation", Reason: ReasonNoMethods}
	}
	if len(b) != 2+n {
		return MethodRequest{}, &DecodeError{Stage: "method_negotiation", Reason: ReasonTruncated}
	}
	methods := make([]byte, n)
	copy(methods, b[2:])
	return MethodRequest{Methods: methods}, nil
}

// DecodeUserPass parses a complete RFC 1929 frame.
func DecodeUserPass(b []byte) (UserPassRequest, error) {
	if len(b) < 2 {
		return UserPassRequest{}, &DecodeError{Stage: "userpass", Reason: ReasonTruncated}
	}
	if b[0] != UserPassVersion {
		return UserPassRequest{}, &DecodeError{
			Stage:  "userpass",
			Reason: ReasonBadUserPassVersion,
			Err:    fmt.Errorf("got VER=0x%02x want 0x%02x", b[0], UserPassVersion),
		}
	}
	ulen := int(b[1])
	if len(b) < 2+ulen+1 {
		return UserPassRequest{}, &DecodeError{Stage: "userpass", Reason: ReasonTruncated}
	}
	user := string(b[2 : 2+ulen])
	plenAt := 2 + ulen
	plen := int(b[plenAt])
	if len(b) != plenAt+1+plen {
		return UserPassRequest{}, &DecodeError{Stage: "userpass", Reason: ReasonTruncated}
	}
	pass := string(b[plenAt+1:])
	return UserPassRequest{Username: user, Password: pass}, nil
}

// DecodeRequest parses a complete request frame.
func DecodeRequest(b []byte) (Request, error) {
	if len(b) < 4 {
		return Request{}, &DecodeError{Stage: "request", Reason: ReasonTruncated}
	}
	if b[0] != Version5 {
		return Request{}, &DecodeError{
			Stage:  "request",
			Reason: ReasonUnsupportedVersion,
			Err:    fmt.Errorf("got VER=0x%02x want 0x%02x", b[0], Version5),
		}
	}
	if b[2] != RSV {
		return Request{}, &DecodeError{
			Stage:  "request",
			Reason: ReasonBadReserved,
			Err:    fmt.Errorf("got RSV=0x%02x want 0x00", b[2]),
		}
	}
	atyp := b[3]
	dest, err := decodeAddr(b[4:], atyp)
	if err != nil {
		return Request{}, err
	}
	if b[1] != CmdConnect {
		return Request{Cmd: b[1], Dest: dest}, &DecodeError{
			Stage:  "request",
			Reason: ReasonUnsupportedCommand,
			Err:    fmt.Errorf("got CMD=0x%02x only CONNECT(0x01) supported", b[1]),
		}
	}
	return Request{Cmd: CmdConnect, Dest: dest}, nil
}

func decodeAddr(b []byte, atyp byte) (Addr, error) {
	switch atyp {
	case AtypIPv4:
		if len(b) != IPv4AddrLen+PortLen {
			return Addr{}, &DecodeError{
				Stage: "address", Reason: ReasonIPv4Length,
				Err: fmt.Errorf("IPv4 addr needs %d bytes got %d", IPv4AddrLen+PortLen, len(b)),
			}
		}
		ip := netip.AddrFrom4([4]byte{b[0], b[1], b[2], b[3]})
		return Addr{Atyp: atyp, IP: ip, Port: bePort(b[4], b[5])}, nil
	case AtypIPv6:
		if len(b) != IPv6AddrLen+PortLen {
			return Addr{}, &DecodeError{
				Stage: "address", Reason: ReasonIPv6Length,
				Err: fmt.Errorf("IPv6 addr needs %d bytes got %d", IPv6AddrLen+PortLen, len(b)),
			}
		}
		var arr [16]byte
		copy(arr[:], b[:16])
		return Addr{Atyp: atyp, IP: netip.AddrFrom16(arr), Port: bePort(b[16], b[17])}, nil
	case AtypDomain:
		if len(b) < 1 {
			return Addr{}, &DecodeError{Stage: "address", Reason: ReasonTruncated}
		}
		dlen := int(b[0])
		if dlen == 0 {
			return Addr{}, &DecodeError{Stage: "address", Reason: ReasonDomainEmpty}
		}
		if dlen > DomainMaxLen {
			return Addr{}, &DecodeError{Stage: "address", Reason: ReasonDomainTooLong}
		}
		if len(b) != 1+dlen+PortLen {
			return Addr{}, &DecodeError{Stage: "address", Reason: ReasonTruncated}
		}
		return Addr{
			Atyp:   atyp,
			Domain: string(b[1 : 1+dlen]),
			Port:   bePort(b[1+dlen], b[1+dlen+1]),
		}, nil
	default:
		return Addr{}, &DecodeError{
			Stage: "address", Reason: ReasonUnsupportedAtyp,
			Err: fmt.Errorf("ATYP=0x%02x", atyp),
		}
	}
}

func bePort(hi, lo byte) uint16 { return uint16(hi)<<8 | uint16(lo) }

// ---------------------------------------------------------------------------
// Stream readers (segmentation-tolerant; delegate structure to pure decoders)
// ---------------------------------------------------------------------------

func readFull(r io.Reader, n int, stage string) ([]byte, error) {
	buf := make([]byte, n)
	if _, err := io.ReadFull(r, buf); err != nil {
		return nil, &DecodeError{Stage: stage, Reason: ReasonTruncated, Err: err}
	}
	return buf, nil
}

// ReadMethods reads one greeting across any TCP segment boundaries.
func ReadMethods(r io.Reader) (MethodRequest, error) {
	head, err := readFull(r, 2, "method_negotiation")
	if err != nil {
		return MethodRequest{}, err
	}
	if head[0] != Version5 {
		_, _ = DecodeMethods(head) // reuse message construction
		return MethodRequest{}, &DecodeError{
			Stage:  "method_negotiation",
			Reason: ReasonUnsupportedVersion,
			Err:    fmt.Errorf("got VER=0x%02x want 0x%02x", head[0], Version5),
		}
	}
	n := int(head[1])
	if n == 0 {
		return MethodRequest{}, &DecodeError{Stage: "method_negotiation", Reason: ReasonNoMethods}
	}
	body, err := readFull(r, n, "method_negotiation")
	if err != nil {
		return MethodRequest{}, err
	}
	return DecodeMethods(append(head, body...))
}

// ReadUserPass reads one RFC 1929 message across segment boundaries.
func ReadUserPass(r io.Reader) (UserPassRequest, error) {
	head, err := readFull(r, 2, "userpass")
	if err != nil {
		return UserPassRequest{}, err
	}
	if head[0] != UserPassVersion {
		return UserPassRequest{}, &DecodeError{
			Stage:  "userpass",
			Reason: ReasonBadUserPassVersion,
			Err:    fmt.Errorf("got VER=0x%02x want 0x%02x", head[0], UserPassVersion),
		}
	}
	ulen := int(head[1])
	user, err := readFull(r, ulen, "userpass")
	if err != nil {
		return UserPassRequest{}, err
	}
	plenOctet, err := readFull(r, 1, "userpass")
	if err != nil {
		return UserPassRequest{}, err
	}
	plen := int(plenOctet[0])
	pass, err := readFull(r, plen, "userpass")
	if err != nil {
		return UserPassRequest{}, err
	}
	frame := make([]byte, 0, 2+ulen+1+plen)
	frame = append(frame, head...)
	frame = append(frame, user...)
	frame = append(frame, plenOctet...)
	frame = append(frame, pass...)
	return DecodeUserPass(frame)
}

// ReadRequest reads one request across segment boundaries.
func ReadRequest(r io.Reader) (Request, error) {
	head, err := readFull(r, 4, "request")
	if err != nil {
		return Request{}, err
	}
	if head[0] != Version5 {
		return Request{}, &DecodeError{
			Stage:  "request",
			Reason: ReasonUnsupportedVersion,
			Err:    fmt.Errorf("got VER=0x%02x want 0x%02x", head[0], Version5),
		}
	}
	atyp := head[3]
	prefix, restLen, err := addrBodyLen(r, atyp)
	if err != nil {
		return Request{}, err
	}
	rest, err := readFull(r, restLen, "address")
	if err != nil {
		return Request{}, err
	}
	frame := make([]byte, 0, 4+len(prefix)+len(rest))
	frame = append(frame, head...)
	frame = append(frame, prefix...)
	frame = append(frame, rest...)
	return DecodeRequest(frame)
}

// addrBodyLen resolves how many address bytes remain after the 4-octet
// header. For a domain the single length octet is read here and returned as
// prefix (it has already been consumed from r); for IP addresses prefix is
// empty and the size is fixed.
func addrBodyLen(r io.Reader, atyp byte) (prefix []byte, restLen int, err error) {
	switch atyp {
	case AtypIPv4:
		return nil, IPv4AddrLen + PortLen, nil
	case AtypIPv6:
		return nil, IPv6AddrLen + PortLen, nil
	case AtypDomain:
		l, err := readFull(r, 1, "address")
		if err != nil {
			return nil, 0, err
		}
		dlen := int(l[0])
		if dlen == 0 {
			return nil, 0, &DecodeError{Stage: "address", Reason: ReasonDomainEmpty}
		}
		if dlen > DomainMaxLen {
			return nil, 0, &DecodeError{Stage: "address", Reason: ReasonDomainTooLong}
		}
		return l, dlen + PortLen, nil
	default:
		return nil, 0, &DecodeError{
			Stage: "address", Reason: ReasonUnsupportedAtyp,
			Err: fmt.Errorf("ATYP=0x%02x", atyp),
		}
	}
}
