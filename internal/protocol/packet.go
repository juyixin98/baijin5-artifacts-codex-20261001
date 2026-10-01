// Package protocol implements byte-level encode/decode of the (S)NTPv4
// message layout (RFC 5905 §7 / RFC 4330).
//
// Only the 48-byte header is modeled, which is all an SNTP client needs.
// All multi-byte integers are big-endian on the wire.
package protocol

import (
	"encoding/binary"
	"errors"
	"fmt"
	"time"
)

// PacketSize is the fixed size of an NTP header in bytes.
const PacketSize = 48

// Epoch offsets between NTP (1900-01-01) and Unix (1970-01-01), in seconds.
const ntpEpochOffsetSec = 2208988800

// Leap second indicator (LI field, 2 bits).
const (
	LINoWarning      uint8 = 0
	LIAddSecond      uint8 = 1
	LISubtractSecond uint8 = 2
	LIAlarm          uint8 = 3 // clock not synchronized
)

// Mode field values (3 bits).
const (
	ModeReserved   uint8 = 0
	ModeSymActive  uint8 = 1
	ModeSymPassive uint8 = 2
	ModeClient     uint8 = 3
	ModeServer     uint8 = 4
	ModeBroadcast  uint8 = 5
)

// Packet is the decoded form of a 48-byte NTP header.
//
// The four reference timestamps relevant to on-wire delay/offset math are:
//
//	ReferenceID     - server identifier / kiss code
//	ReferenceTime   - when the server last set/validated its clock
//	OriginTime      - client TransmitTime echoed by the server ("x1")
//	ReceiveTime     - server receive of the request ("x2")
//	TransmitTime    - server reply transmit ("x3"); client receive is "x4"
type Packet struct {
	LI             uint8
	Version        uint8
	Mode           uint8
	Stratum        uint8
	Poll           int8 // log2 seconds
	Precision      int8 // log2 seconds
	RootDelay      NTPShort
	RootDispersion NTPShort
	ReferenceID    uint32
	ReferenceTime  NTPTimestamp
	OriginTime     NTPTimestamp
	ReceiveTime    NTPTimestamp
	TransmitTime   NTPTimestamp
}

// NTPShort is a 32-bit fixed-point duration: 16 bits seconds + 16 bits fraction.
type NTPShort struct {
	Seconds  uint16
	Fraction uint16
}

// Duration converts the short-format field to a Go duration (always non-negative).
func (s NTPShort) Duration() time.Duration {
	ns := int64(s.Seconds)*int64(time.Second) +
		(int64(s.Fraction) << 16 * int64(time.Second) >> 32)
	return time.Duration(ns)
}

// NTPTimestamp is a 64-bit NTP timestamp: 32 bits seconds since 1900 + 32 bits fraction.
type NTPTimestamp uint64

// Seconds returns the integer seconds-since-1900 component.
func (t NTPTimestamp) Seconds() uint32 { return uint32(t >> 32) }

// Fraction returns the 32-bit fractional-second component.
func (t NTPTimestamp) Fraction() uint32 { return uint32(t) }

// Time converts to time.Time. Zero (pre-1900 wraparound aside) maps to the NTP epoch.
func (t NTPTimestamp) Time() time.Time {
	sec := int64(t.Seconds()) - ntpEpochOffsetSec
	// fraction * 1e9 / 2^32, done in int64 (max ~4.29e9*1e9 overflows int63,
	// so split: fraction/2^32 * 1e9 computed as high*1e9 + low*1e9/2^32).
	frac := t.Fraction()
	hi := uint64(frac) >> 16
	lo := uint64(frac) & 0xffff
	nanos := hi * 1_000_000_000 >> 16
	nanos += lo * 1_000_000_000 >> 32
	return time.Unix(sec, int64(nanos)).UTC()
}

// Duration returns the signed difference between two NTP timestamps.
func (t NTPTimestamp) Sub(o NTPTimestamp) time.Duration {
	return t.Time().Sub(o.Time())
}

// TimestampFromTime converts a time.Time to an NTP timestamp.
func TimestampFromTime(t time.Time) NTPTimestamp {
	t = t.UTC()
	sec := uint64(t.Unix() + ntpEpochOffsetSec)
	nanos := uint64(t.Nanosecond())
	// nanos * 2^32 / 1e9, split to avoid uint64 overflow in the multiply.
	frac := nanos << 32 / 1_000_000_000
	return NTPTimestamp(sec<<32 | frac)
}

// Encode serializes the packet into exactly 48 bytes (big-endian).
func (p *Packet) Encode() []byte {
	b := make([]byte, PacketSize)
	b[0] = (p.LI << 6) | (p.Version << 3) | p.Mode
	b[1] = p.Stratum
	b[2] = byte(p.Poll)
	b[3] = byte(p.Precision)
	binary.BigEndian.PutUint16(b[4:6], p.RootDelay.Seconds)
	binary.BigEndian.PutUint16(b[6:8], p.RootDelay.Fraction)
	binary.BigEndian.PutUint16(b[8:10], p.RootDispersion.Seconds)
	binary.BigEndian.PutUint16(b[10:12], p.RootDispersion.Fraction)
	binary.BigEndian.PutUint32(b[12:16], p.ReferenceID)
	binary.BigEndian.PutUint32(b[16:20], p.ReferenceTime.Seconds())
	binary.BigEndian.PutUint32(b[20:24], p.ReferenceTime.Fraction())
	binary.BigEndian.PutUint32(b[24:28], p.OriginTime.Seconds())
	binary.BigEndian.PutUint32(b[28:32], p.OriginTime.Fraction())
	binary.BigEndian.PutUint32(b[32:36], p.ReceiveTime.Seconds())
	binary.BigEndian.PutUint32(b[36:40], p.ReceiveTime.Fraction())
	binary.BigEndian.PutUint32(b[40:44], p.TransmitTime.Seconds())
	binary.BigEndian.PutUint32(b[44:48], p.TransmitTime.Fraction())
	return b
}

// Decode errors.
var (
	ErrShortPacket = errors.New("protocol: packet shorter than 48 bytes")
	ErrBadVersion  = errors.New("protocol: unsupported NTP version")
	ErrBadMode     = errors.New("protocol: packet is not a server response")
)

// Decode parses a 48-byte buffer into a Packet and validates version/mode.
func Decode(b []byte) (*Packet, error) {
	if len(b) < PacketSize {
		return nil, fmt.Errorf("%w: got %d bytes", ErrShortPacket, len(b))
	}
	p := &Packet{
		LI:        b[0] >> 6,
		Version:   (b[0] >> 3) & 0x7,
		Mode:      b[0] & 0x7,
		Stratum:   b[1],
		Poll:      int8(b[2]),
		Precision: int8(b[3]),
	}
	if p.Version < 1 || p.Version > 4 {
		return nil, fmt.Errorf("%w: %d", ErrBadVersion, p.Version)
	}
	if p.Mode != ModeServer {
		return nil, fmt.Errorf("%w: mode=%d", ErrBadMode, p.Mode)
	}
	p.RootDelay = NTPShort{
		Seconds:  binary.BigEndian.Uint16(b[4:6]),
		Fraction: binary.BigEndian.Uint16(b[6:8]),
	}
	p.RootDispersion = NTPShort{
		Seconds:  binary.BigEndian.Uint16(b[8:10]),
		Fraction: binary.BigEndian.Uint16(b[10:12]),
	}
	p.ReferenceID = binary.BigEndian.Uint32(b[12:16])
	p.ReferenceTime = NTPTimestamp(binary.BigEndian.Uint64(b[16:24]))
	p.OriginTime = NTPTimestamp(binary.BigEndian.Uint64(b[24:32]))
	p.ReceiveTime = NTPTimestamp(binary.BigEndian.Uint64(b[32:40]))
	p.TransmitTime = NTPTimestamp(binary.BigEndian.Uint64(b[40:48]))
	return p, nil
}

// DecodeLax parses a 48-byte buffer without validating version/mode.
// It is used by the server state machine, which must inspect arbitrary
// inbound bytes (and apply its own mode check) before replying.
func DecodeLax(b []byte) (*Packet, error) {
	if len(b) < PacketSize {
		return nil, fmt.Errorf("%w: got %d bytes", ErrShortPacket, len(b))
	}
	p := &Packet{
		LI:        b[0] >> 6,
		Version:   (b[0] >> 3) & 0x7,
		Mode:      b[0] & 0x7,
		Stratum:   b[1],
		Poll:      int8(b[2]),
		Precision: int8(b[3]),
	}
	p.RootDelay = NTPShort{
		Seconds:  binary.BigEndian.Uint16(b[4:6]),
		Fraction: binary.BigEndian.Uint16(b[6:8]),
	}
	p.RootDispersion = NTPShort{
		Seconds:  binary.BigEndian.Uint16(b[8:10]),
		Fraction: binary.BigEndian.Uint16(b[10:12]),
	}
	p.ReferenceID = binary.BigEndian.Uint32(b[12:16])
	p.ReferenceTime = NTPTimestamp(binary.BigEndian.Uint64(b[16:24]))
	p.OriginTime = NTPTimestamp(binary.BigEndian.Uint64(b[24:32]))
	p.ReceiveTime = NTPTimestamp(binary.BigEndian.Uint64(b[32:40]))
	p.TransmitTime = NTPTimestamp(binary.BigEndian.Uint64(b[40:48]))
	return p, nil
}

// NewClientRequest builds a v4 client-mode request stamped with transmit time t.
func NewClientRequest(t time.Time) *Packet {
	return &Packet{
		LI:           LINoWarning,
		Version:      4,
		Mode:         ModeClient,
		Poll:         6,
		Precision:    -20,
		TransmitTime: TimestampFromTime(t),
	}
}

// IsKissOfDeath reports whether a server packet is a KoD (stratum 0).
// Stratum-0 replies carry a 4-character kiss code in ReferenceID.
func (p *Packet) IsKissOfDeath() bool { return p.Stratum == 0 }

// KissCode renders the ReferenceID as the 4-char ASCII kiss code for stratum-0.
func (p *Packet) KissCode() string {
	id := p.ReferenceID
	return string([]byte{byte(id >> 24), byte(id >> 16), byte(id >> 8), byte(id)})
}
