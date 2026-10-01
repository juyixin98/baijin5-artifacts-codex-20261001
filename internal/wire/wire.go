// Package wire implements byte-level encoding and decoding for the subset of
// SOCKS5 (RFC 1928) used by this proxy: method negotiation (RFC 1928),
// username/password authentication (RFC 1929) and the CONNECT command.
//
// Decoding is intentionally strict. Every length field is validated against
// the bytes actually present, so callers can distinguish a truncated frame
// (the peer stopped sending) from a frame whose declared length is invalid.
// All functions in this package are allocation-light and free of any I/O or
// policy decisions; the protocol state machine in internal/proto drives them.
package wire

import (
	"errors"
	"fmt"
	"net"
	"net/netip"
	"strconv"
)

// SOCKS5 protocol constants.
const (
	Version5 byte = 0x05

	MethodNoAuth       byte = 0x00 // no authentication required
	MethodUserPass     byte = 0x02 // username/password (RFC 1929)
	MethodNoAcceptable byte = 0xFF // no acceptable methods offered

	UserPassVersion byte = 0x01 // VER of the RFC 1929 sub-negotiation
	AuthStatusOK    byte = 0x00
	// AuthStatusFail is the RFC 1929 failure status. Any non-zero value
	// means failure; 0x01 is the canonical one.
	AuthStatusFail byte = 0x01

	CmdConnect byte = 0x01
	Reserved   byte = 0x00

	ATypIPv4   byte = 0x01
	ATypDomain byte = 0x03
	ATypIPv6   byte = 0x04

	RepSucceeded            byte = 0x00
	RepGeneralFailure       byte = 0x01
	RepConnectionNotAllowed byte = 0x02
	RepNetworkUnreachable   byte = 0x03
	RepHostUnreachable      byte = 0x04
	RepConnectionRefused    byte = 0x05
	RepTTLExpired           byte = 0x06
	RepCommandNotSupported  byte = 0x07
	RepAddressNotSupported  byte = 0x08
)

// Frame size bounds, derived from the one-octet length fields of RFC 1928.
const (
	IPv4Len          = 4
	IPv6Len          = 16
	PortLen          = 2
	MaxDomainLen     = 255
	MaxMethodCount   = 255
	greetingHeadLen  = 2 // VER, NMETHODS
	authHeadLen      = 2 // VER, ULEN
	requestHeadLen   = 4 // VER, CMD, RSV, ATYP
	domainLenByteCnt = 1 // the one-octet DST.ADDR length for ATYP_DOMAIN
)

// MaxFrameLen values, useful for allocating bounded read buffers.
const (
	MaxGreetingLen = greetingHeadLen + MaxMethodCount                           // 257
	MaxAuthLen     = 1 + 1 + MaxDomainLen + 1 + MaxDomainLen                    // 513
	MaxRequestLen  = requestHeadLen + domainLenByteCnt + MaxDomainLen + PortLen // 262
)

// Decode errors. Callers can use errors.Is to classify malformed input.
var (
	ErrShortFrame   = errors.New("wire: short frame")
	ErrExtraBytes   = errors.New("wire: frame longer than declared")
	ErrBadVersion   = errors.New("wire: unsupported protocol version")
	ErrBadReserved  = errors.New("wire: reserved field must be 0x00")
	ErrBadATyp      = errors.New("wire: unsupported address type")
	ErrBadDomainLen = errors.New("wire: domain length must be between 1 and 255")
	ErrBadMethodVer = errors.New("wire: unsupported username/password sub-negotiation version")
	ErrEmptyMethodL = errors.New("wire: NMETHODS must be at least 1")
)

// Greeting is a decoded method-selection request.
type Greeting struct {
	Methods []byte // offered methods, in client order, copied
}

// DecodeGreeting decodes a complete greeting frame:
//
//	+----+----------+----------+
//	|VER | NMETHODS | METHODS  |
//	+----+----------+----------+
//	| 1  |    1     | 1 to 255 |
//	+----+----------+----------+
func DecodeGreeting(frame []byte) (Greeting, error) {
	if len(frame) < greetingHeadLen {
		return Greeting{}, fmt.Errorf("%w: have %d bytes, need %d", ErrShortFrame, len(frame), greetingHeadLen)
	}
	if frame[0] != Version5 {
		return Greeting{}, fmt.Errorf("%w: got 0x%02x, want 0x%02x", ErrBadVersion, frame[0], Version5)
	}
	n := int(frame[1])
	if n == 0 {
		return Greeting{}, ErrEmptyMethodL
	}
	want := greetingHeadLen + n
	if len(frame) < want {
		return Greeting{}, fmt.Errorf("%w: have %d bytes, NMETHODS=%d needs %d", ErrShortFrame, len(frame), n, want)
	}
	if len(frame) > want {
		return Greeting{}, fmt.Errorf("%w: have %d bytes, frame declares %d", ErrExtraBytes, len(frame), want)
	}
	out := make([]byte, n)
	copy(out, frame[greetingHeadLen:want])
	return Greeting{Methods: out}, nil
}

// GreetingLen returns the total length of a greeting frame given its first two
// header bytes. It lets a state machine read the header and the body in two
// bounded Read calls.
func GreetingLen(head []byte) (int, error) {
	if len(head) < greetingHeadLen {
		return 0, fmt.Errorf("%w: greeting header", ErrShortFrame)
	}
	n := int(head[1])
	if n == 0 {
		return 0, ErrEmptyMethodL
	}
	return greetingHeadLen + n, nil
}

// UserPass is a decoded RFC 1929 username/password frame.
type UserPass struct {
	Username string
	Password string
}

// DecodeUserPass decodes a complete sub-negotiation frame:
//
//	+----+------+----------+------+----------+
//	|VER | ULEN |  UNAME   | PLEN |  PASSWD  |
//	+----+------+----------+------+----------+
//	| 1  |  1   | 1 to 255 |  1   | 1 to 255 |
//	+----+------+----------+------+----------+
//
// ULEN/PLEN of zero are rejected as malformed.
func DecodeUserPass(frame []byte) (UserPass, error) {
	if len(frame) < authHeadLen {
		return UserPass{}, fmt.Errorf("%w: auth header", ErrShortFrame)
	}
	if frame[0] != UserPassVersion {
		return UserPass{}, fmt.Errorf("%w: got 0x%02x, want 0x%02x", ErrBadMethodVer, frame[0], UserPassVersion)
	}
	ulen := int(frame[1])
	if ulen == 0 {
		return UserPass{}, fmt.Errorf("%w: ULEN=0", ErrBadDomainLen)
	}
	if len(frame) < authHeadLen+ulen+1 {
		return UserPass{}, fmt.Errorf("%w: username", ErrShortFrame)
	}
	plen := int(frame[authHeadLen+ulen])
	if plen == 0 {
		return UserPass{}, fmt.Errorf("%w: PLEN=0", ErrBadDomainLen)
	}
	want := authHeadLen + ulen + 1 + plen
	if len(frame) < want {
		return UserPass{}, fmt.Errorf("%w: password", ErrShortFrame)
	}
	if len(frame) > want {
		return UserPass{}, fmt.Errorf("%w: have %d bytes, frame declares %d", ErrExtraBytes, len(frame), want)
	}
	return UserPass{
		Username: string(frame[authHeadLen : authHeadLen+ulen]),
		Password: string(frame[authHeadLen+ulen+1 : want]),
	}, nil
}

// Target is a decoded SOCKS5 address/port pair.
type Target struct {
	ATyp   byte
	Domain string // set when ATyp == ATypDomain
	Addr   netip.Addr
	Port   uint16
}

// Host returns the textual host (domain or IP), independent of ATyp.
func (t Target) Host() string {
	if t.ATyp == ATypDomain {
		return t.Domain
	}
	return t.Addr.String()
}

// String renders host:port for logging and dialing.
func (t Target) String() string {
	if t.ATyp == ATypDomain {
		return net.JoinHostPort(t.Domain, strconv.Itoa(int(t.Port)))
	}
	return netip.AddrPortFrom(t.Addr, t.Port).String()
}

// Request is a decoded CONNECT request.
type Request struct {
	Command byte
	Target  Target
}

// DecodeRequest decodes a complete request frame:
//
//	+----+-----+-------+------+----------+----------+
//	|VER | CMD |  RSV  | ATYP | DST.ADDR | DST.PORT |
//	+----+-----+-------+------+----------+----------+
func DecodeRequest(frame []byte) (Request, error) {
	if len(frame) < requestHeadLen {
		return Request{}, fmt.Errorf("%w: request header", ErrShortFrame)
	}
	if frame[0] != Version5 {
		return Request{}, fmt.Errorf("%w: got 0x%02x, want 0x%02x", ErrBadVersion, frame[0], Version5)
	}
	if frame[2] != Reserved {
		return Request{}, fmt.Errorf("%w: got 0x%02x", ErrBadReserved, frame[2])
	}
	tgt, err := DecodeTarget(frame[requestHeadLen-1:]) // reuse ATyp-first decoder, include length
	if err != nil {
		return Request{}, err
	}
	return Request{Command: frame[1], Target: tgt}, nil
}

// DecodeTarget decodes ATYP + DST.ADDR + DST.PORT. frame starts at ATyp.
func DecodeTarget(frame []byte) (Target, error) {
	if len(frame) < 2 {
		return Target{}, fmt.Errorf("%w: address header", ErrShortFrame)
	}
	atyp := frame[0]
	switch atyp {
	case ATypIPv4:
		if len(frame) != 1+IPv4Len+PortLen {
			return Target{}, fmt.Errorf("wire: IPv4 frame is %d bytes, want %d", len(frame), 1+IPv4Len+PortLen)
		}
		addr := netip.AddrFrom4([4]byte(frame[1 : 1+IPv4Len]))
		port := bePort(frame[1+IPv4Len:])
		return Target{ATyp: atyp, Addr: addr, Port: port}, nil
	case ATypIPv6:
		if len(frame) != 1+IPv6Len+PortLen {
			return Target{}, fmt.Errorf("wire: IPv6 frame is %d bytes, want %d", len(frame), 1+IPv6Len+PortLen)
		}
		addr := netip.AddrFrom16([16]byte(frame[1 : 1+IPv6Len]))
		port := bePort(frame[1+IPv6Len:])
		return Target{ATyp: atyp, Addr: addr, Port: port}, nil
	case ATypDomain:
		dlen := int(frame[1])
		if dlen == 0 || dlen > MaxDomainLen {
			return Target{}, fmt.Errorf("%w: length field is %d", ErrBadDomainLen, dlen)
		}
		want := 1 + domainLenByteCnt + dlen + PortLen
		if len(frame) != want {
			return Target{}, fmt.Errorf("wire: domain frame is %d bytes, want %d", len(frame), want)
		}
		domain := string(frame[2 : 2+dlen])
		port := bePort(frame[2+dlen:])
		return Target{ATyp: atyp, Domain: domain, Port: port}, nil
	default:
		return Target{}, fmt.Errorf("%w: 0x%02x", ErrBadATyp, atyp)
	}
}

// RequestBodyLen returns the total request-frame length (including VER) given
// at least the first 4 bytes. For ATYP_DOMAIN the 5th byte (length) is
// required as well.
func RequestBodyLen(head []byte) (int, error) {
	if len(head) < requestHeadLen {
		return 0, fmt.Errorf("%w: request header", ErrShortFrame)
	}
	switch head[3] {
	case ATypIPv4:
		return requestHeadLen + IPv4Len + PortLen, nil
	case ATypIPv6:
		return requestHeadLen + IPv6Len + PortLen, nil
	case ATypDomain:
		if len(head) < requestHeadLen+1 {
			return 0, fmt.Errorf("%w: domain length byte", ErrShortFrame)
		}
		dlen := int(head[requestHeadLen])
		if dlen == 0 || dlen > MaxDomainLen {
			return 0, fmt.Errorf("%w: length field is %d", ErrBadDomainLen, dlen)
		}
		return requestHeadLen + domainLenByteCnt + dlen + PortLen, nil
	default:
		return 0, fmt.Errorf("%w: 0x%02x", ErrBadATyp, head[3])
	}
}

// EncodeMethodSelection renders the server's method choice.
func EncodeMethodSelection(method byte) []byte {
	return []byte{Version5, method}
}

// EncodeAuthStatus renders the RFC 1929 sub-negotiation status reply.
func EncodeAuthStatus(status byte) []byte {
	return []byte{UserPassVersion, status}
}

// EncodeReply renders VER, REP, RSV, ATYP, BND.ADDR, BND.PORT. The bound
// address must be the concrete local address of the established connection
// so the reply is consistent with what was actually dialed.
func EncodeReply(rep byte, bound Target) []byte {
	switch bound.ATyp {
	case ATypIPv4:
		out := make([]byte, 1+1+1+1+IPv4Len+PortLen)
		out[0], out[1], out[2], out[3] = Version5, rep, Reserved, ATypIPv4
		if bound.Addr.IsValid() {
			a4 := bound.Addr.As4()
			copy(out[4:4+IPv4Len], a4[:])
		} // else BND.ADDR stays 0.0.0.0 (failure reply convention)
		putBePort(out[4+IPv4Len:], bound.Port)
		return out
	case ATypIPv6:
		out := make([]byte, 1+1+1+1+IPv6Len+PortLen)
		out[0], out[1], out[2], out[3] = Version5, rep, Reserved, ATypIPv6
		a16 := bound.Addr.As16()
		copy(out[4:4+IPv6Len], a16[:])
		putBePort(out[4+IPv6Len:], bound.Port)
		return out
	default:
		// Failures and unnamed sockets: encode an all-zero IPv4 endpoint,
		// the conventional BND.ADDR for unsuccessful replies.
		out := make([]byte, 1+1+1+1+IPv4Len+PortLen)
		out[0], out[1], out[2], out[3] = Version5, rep, Reserved, ATypIPv4
		return out
	}
}

// FailureReply renders an unsuccessful reply with BND.ADDR=0.0.0.0:0.
func FailureReply(rep byte) []byte {
	return EncodeReply(rep, Target{ATyp: ATypIPv4})
}

// TargetFromAddrPort builds a Target with the correct ATyp for an IP literal.
func TargetFromAddrPort(ap netip.AddrPort) Target {
	addr := ap.Addr().Unmap() // v4-mapped IPv6 must be encoded as ATYP=1
	atyp := ATypIPv6
	if addr.Is4() {
		atyp = ATypIPv4
	}
	return Target{ATyp: atyp, Addr: addr, Port: ap.Port()}
}

func bePort(b []byte) uint16 {
	return uint16(b[0])<<8 | uint16(b[1])
}

func putBePort(b []byte, port uint16) {
	b[0] = byte(port >> 8)
	b[1] = byte(port)
}
