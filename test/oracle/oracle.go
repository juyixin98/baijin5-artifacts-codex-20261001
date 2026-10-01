// Package oracle is an INDEPENDENT reference implementation used only by
// tests. It deliberately does NOT import the project's internal/codec,
// internal/proto or any code under test: its constants, builders and parsers
// are written from the RFC text (RFC 1928 / RFC 1929) so expected wire bytes
// and verdicts are never produced by the implementation being verified.
//
// If the oracle and the proxy agree byte-for-byte, that is cross-validation
// between two independently written encoders, not a self-consistent tautology.
package oracle

import (
	"encoding/binary"
	"errors"
	"fmt"
	"net/netip"
)

// Independently-written protocol constants (values from RFC 1928/1929).
const (
	SocksVersion      = 5
	SubnegVersion     = 1
	MethodNone        = 0x00
	MethodUserPass    = 0x02
	MethodNoneAllowed = 0xFF
	CmdCONNECT        = 1
	AtypIPv4          = 1
	AtypName          = 3
	AtypIPv6          = 4
	StatusOK          = 0x00
	StatusFail        = 0x01
)

// REP values, again transcribed independently from RFC 1928 section 6.
const (
	RepOK          = 0x00
	RepRuleset     = 0x02
	RepNetUnreach  = 0x03
	RepHostUnreach = 0x04
	RepConnRefused = 0x05
	RepCmdUnsup    = 0x07
	RepAtypUnsup   = 0x08
)

// Greeting returns the independently encoded client method greeting.
func Greeting(methods ...byte) []byte {
	if len(methods) > 255 {
		panic("oracle: too many methods")
	}
	b := []byte{SocksVersion, byte(len(methods))}
	return append(b, methods...)
}

// MethodChoice is the exact two bytes a server sends for a chosen method.
func MethodChoice(method byte) []byte { return []byte{SocksVersion, method} }

// UserPass returns independently encoded RFC 1929 credentials.
func UserPass(user, pass string) []byte {
	b := []byte{SubnegVersion, byte(len(user))}
	b = append(b, user...)
	b = append(b, byte(len(pass)))
	b = append(b, pass...)
	return b
}

// SubnegStatus is the exact two bytes a server sends for auth result.
func SubnegStatus(status byte) []byte { return []byte{SubnegVersion, status} }

// ConnectIPv4 encodes a CONNECT request to an IPv4 host:port.
func ConnectIPv4(addr [4]byte, port uint16) []byte {
	b := []byte{SocksVersion, CmdCONNECT, 0x00, AtypIPv4}
	b = append(b, addr[:]...)
	return binary.BigEndian.AppendUint16(b, port)
}

// ConnectIPv6 encodes a CONNECT request to an IPv6 host:port.
func ConnectIPv6(addr [16]byte, port uint16) []byte {
	b := []byte{SocksVersion, CmdCONNECT, 0x00, AtypIPv6}
	b = append(b, addr[:]...)
	return binary.BigEndian.AppendUint16(b, port)
}

// ConnectName encodes a CONNECT request to a DNS name:port.
func ConnectName(name string, port uint16) []byte {
	if len(name) > 255 {
		panic("oracle: name too long")
	}
	b := []byte{SocksVersion, CmdCONNECT, 0x00, AtypName, byte(len(name))}
	b = append(b, name...)
	return binary.BigEndian.AppendUint16(b, port)
}

// Reply is an independently parsed server reply.
type Reply struct {
	Version  byte
	Rep      byte
	Reserved byte
	Atyp     byte
	BindIP   netip.Addr
	BindName string
	Port     uint16
}

// ParseReply independently parses a complete server reply from b.
func ParseReply(b []byte) (Reply, error) {
	if len(b) < 4 {
		return Reply{}, errors.New("oracle: reply shorter than fixed header")
	}
	if b[0] != SocksVersion {
		return Reply{}, fmt.Errorf("oracle: reply version %d", b[0])
	}
	if b[2] != 0 {
		return Reply{}, fmt.Errorf("oracle: reserved byte 0x%02x", b[2])
	}
	r := Reply{Version: b[0], Rep: b[1], Reserved: b[2], Atyp: b[3]}
	rest := b[4:]
	switch r.Atyp {
	case AtypIPv4:
		if len(rest) != 6 {
			return Reply{}, fmt.Errorf("oracle: ipv4 reply body len %d", len(rest))
		}
		var a [4]byte
		copy(a[:], rest[:4])
		r.BindIP = netip.AddrFrom4(a)
		r.Port = binary.BigEndian.Uint16(rest[4:6])
	case AtypIPv6:
		if len(rest) != 18 {
			return Reply{}, fmt.Errorf("oracle: ipv6 reply body len %d", len(rest))
		}
		var a [16]byte
		copy(a[:], rest[:16])
		r.BindIP = netip.AddrFrom16(a)
		r.Port = binary.BigEndian.Uint16(rest[16:18])
	case AtypName:
		if len(rest) < 1 {
			return Reply{}, errors.New("oracle: truncated name length")
		}
		n := int(rest[0])
		if len(rest) != 1+n+2 {
			return Reply{}, fmt.Errorf("oracle: name reply body len %d n %d", len(rest), n)
		}
		r.BindName = string(rest[1 : 1+n])
		r.Port = binary.BigEndian.Uint16(rest[1+n:])
	default:
		return Reply{}, fmt.Errorf("oracle: unknown atyp %d", r.Atyp)
	}
	return r, nil
}

// ReadFull is io.ReadFull without importing io at call sites in tests that
// already have their own; kept for symmetry with the framed readers.
func ReadFull(buf []byte, read func([]byte) (int, error)) error {
	off := 0
	for off < len(buf) {
		n, err := read(buf[off:])
		if n > 0 {
			off += n
		}
		if err != nil && off < len(buf) {
			return err
		}
	}
	return nil
}

// ExpectedSuccessReplyLen is the exact length of the IPv4 zero-bind success
// reply: VER REP RSV ATYP + 4 addr + 2 port.
const ExpectedSuccessReplyLen = 10

// Verdict explains the reference decision for a scenario, independent of the
// implementation. Tests assert the proxy's observed REP equals WantRep.
type Verdict struct {
	Name    string
	WantRep byte
	Note    string
}
