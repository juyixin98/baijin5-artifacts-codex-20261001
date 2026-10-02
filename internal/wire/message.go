package wire

import (
	"errors"
	"fmt"
)

// Message is one CoAP datagram (RFC 7252 §3).
//
// Token identifies the request/response exchange at the application layer;
// MessageID dedupes CON retransmissions at the message layer. The two are
// deliberately kept as separate fields and must never be interchanged.
type Message struct {
	Type    Type
	Code    Code
	MID     uint16
	Token   []byte
	Options []Option
	// Payload is everything after the 0xFF payload marker; nil when absent.
	Payload []byte
}

// Option accessors. Repeated options (e.g. Uri-Path) are returned in order.

func (m *Message) OptionValues(num int) [][]byte {
	var out [][]byte
	for i := range m.Options {
		if m.Options[i].Number == num {
			out = append(out, m.Options[i].Value)
		}
	}
	return out
}

func (m *Message) FirstOption(num int) ([]byte, bool) {
	for i := range m.Options {
		if m.Options[i].Number == num {
			return m.Options[i].Value, true
		}
	}
	return nil, false
}

// Path returns the Uri-Path segments.
func (m *Message) Path() []string {
	vals := m.OptionValues(OpURIPath)
	out := make([]string, len(vals))
	for i, v := range vals {
		out[i] = string(v)
	}
	return out
}

// Block1 / Block2 accessors return the parsed descriptive/control block.
func (m *Message) Block1() (Block, bool, error) { return m.blockOpt(OpBlock1) }
func (m *Message) Block2() (Block, bool, error) { return m.blockOpt(OpBlock2) }

func (m *Message) blockOpt(num int) (Block, bool, error) {
	raw, ok := m.FirstOption(num)
	if !ok {
		return Block{}, false, nil
	}
	b, err := DecodeBlock(raw)
	if err != nil {
		return Block{}, true, err
	}
	return b, true, nil
}

// MarshalError categories let callers distinguish local programmer errors
// from peer-caused parse errors.
var (
	ErrTokenTooLong    = errors.New("wire: token longer than 8 bytes")
	ErrOptionTooLong   = errors.New("wire: option value too long (>65804)")
	ErrPayloadNoMarker = errors.New("wire: payload requires a 0xFF marker but options misordered")
)

// Marshal serialises the message. Options are sorted and duplicate numbers
// are permitted (delta 0); payloads are prefixed with the 0xFF marker.
func (m *Message) Marshal() ([]byte, error) {
	if len(m.Token) > 8 {
		return nil, ErrTokenTooLong
	}
	opts := make([]Option, len(m.Options))
	copy(opts, m.Options)
	if err := sortAndValidateOptions(opts); err != nil {
		return nil, err
	}

	buf := make([]byte, 0, 128+len(m.Payload))
	// Ver=1 (bits 6-7), Type (bits 4-5), TKL (bits 0-3).
	buf = append(buf, 0x40|byte(m.Type)<<4|byte(len(m.Token)&0x0f))
	buf = append(buf, byte(m.Code))
	buf = append(buf, byte(m.MID>>8), byte(m.MID))
	buf = append(buf, m.Token...)

	prev := 0
	for _, o := range opts {
		delta := o.Number - prev
		prev = o.Number
		n := len(o.Value)
		if n > 65535+269 {
			return nil, ErrOptionTooLong
		}
		buf = appendOptionHeader(buf, delta, n)
		buf = append(buf, o.Value...)
	}
	if len(m.Payload) > 0 {
		buf = append(buf, 0xFF)
		buf = append(buf, m.Payload...)
	}
	return buf, nil
}

func sortAndValidateOptions(opts []Option) error {
	// Stable insertion sort: repeated option numbers keep wire order.
	for i := 1; i < len(opts); i++ {
		for j := i; j > 0 && opts[j-1].Number > opts[j].Number; j-- {
			opts[j-1], opts[j] = opts[j], opts[j-1]
		}
	}
	for i := range opts {
		n := opts[i].Number
		if n < 0 || n > 65535+269 {
			return fmt.Errorf("wire: option number %d out of range", n)
		}
		if n == OpBlock1 || n == OpBlock2 {
			if _, err := DecodeBlock(opts[i].Value); err != nil {
				return err
			}
		}
	}
	// Block options MUST NOT occur more than once (RFC 7959 §2.1).
	if countOptions(opts, OpBlock1) > 1 || countOptions(opts, OpBlock2) > 1 {
		return &ParseError{Reason: "Block1/Block2 option repeated"}
	}
	return nil
}

func countOptions(opts []Option, num int) int {
	n := 0
	for i := range opts {
		if opts[i].Number == num {
			n++
		}
	}
	return n
}

func appendOptionHeader(buf []byte, delta, length int) []byte {
	var d0, l0 int
	var ext []byte
	switch {
	case delta < 13:
		d0 = delta
	case delta < 269:
		d0, ext = 13, append(ext, byte(delta-13))
	default:
		d0 = 14
		v := uint16(delta - 269)
		ext = append(ext, byte(v>>8), byte(v))
	}
	switch {
	case length < 13:
		l0 = length
	case length < 269:
		l0 = 13
		ext = append(ext, byte(length-13))
	default:
		l0 = 14
		v := uint16(length - 269)
		ext = append(ext, byte(v>>8), byte(v))
	}
	buf = append(buf, byte(d0<<4|l0))
	buf = append(buf, ext...)
	return buf
}

// MessageParseError is the peer-failure category for malformed datagrams.
// Reason is diagnostic; the datagram is always rejected, never processed.
type MessageParseError struct {
	Reason string
	Bytes  []byte
}

func (e *MessageParseError) Error() string { return "wire: malformed message: " + e.Reason }

// Parse decodes one datagram. Empty messages (code 0.00, no options, no
// payload) are valid and used as pure ACKs/RSTs.
func Parse(datagram []byte) (*Message, error) {
	if len(datagram) < 4 {
		return nil, &MessageParseError{Reason: fmt.Sprintf("short datagram (%d bytes)", len(datagram)), Bytes: dup(datagram)}
	}
	h := datagram[0]
	if h>>6 != 1 {
		return nil, &MessageParseError{Reason: fmt.Sprintf("unsupported version %d", h>>6), Bytes: dup(datagram)}
	}
	t := Type((h >> 4) & 0x3)
	tkl := int(h & 0x0f)
	m := &Message{
		Type: t,
		Code: Code(datagram[1]),
		MID:  uint16(datagram[2])<<8 | uint16(datagram[3]),
	}
	p := 4
	if len(datagram) < p+tkl {
		return nil, &MessageParseError{Reason: "TKL runs past datagram", Bytes: dup(datagram)}
	}
	m.Token = dup(datagram[p : p+tkl])
	p += tkl

	prev := 0
	for p < len(datagram) {
		if datagram[p] == 0xFF {
			p++
			m.Payload = dup(datagram[p:])
			p = len(datagram)
			break
		}
		first := datagram[p]
		p++
		deltaCode := int(first >> 4)
		lenCode := int(first & 0x0f)
		if deltaCode == 15 || lenCode == 15 {
			return nil, &MessageParseError{Reason: "reserved nibble 15 outside payload marker", Bytes: dup(datagram)}
		}
		var err error
		var delta int
		if delta, p, err = readExt(datagram, p, deltaCode, "delta"); err != nil {
			return nil, err
		}
		var olen int
		if olen, p, err = readExt(datagram, p, lenCode, "length"); err != nil {
			return nil, err
		}
		if p+olen > len(datagram) {
			return nil, &MessageParseError{Reason: fmt.Sprintf("option value runs past datagram at num %d", prev+delta), Bytes: dup(datagram)}
		}
		num := prev + delta
		if num < prev { // overflow guard
			return nil, &MessageParseError{Reason: "option number overflow", Bytes: dup(datagram)}
		}
		// RFC 7252 §3.1: options MUST be in ascending order.
		if delta == 0 && len(m.Options) == 0 {
			return nil, &MessageParseError{Reason: "first option has delta 0", Bytes: dup(datagram)}
		}
		if delta == 0 && num != m.Options[len(m.Options)-1].Number {
			return nil, &MessageParseError{Reason: "non-repeat option encoded with delta 0", Bytes: dup(datagram)}
		}
		val := dup(datagram[p : p+olen])
		// Note: a reserved SZX=7 in a Block option is NOT a wire-format
		// error — the datagram parses. RFC 7959 §2.2 requires a 4.00 Bad
		// Request RESPONSE, so semantic validation happens in the handler
		// via Message.Block1()/Block2() rather than dropping the datagram.
		m.Options = append(m.Options, Option{Number: num, Value: val})
		p += olen
		prev = num
	}

	// Structural validation (§4.1, RFC 7959 §2.1).
	if countOptions(m.Options, OpBlock1) > 1 || countOptions(m.Options, OpBlock2) > 1 {
		return nil, &MessageParseError{Reason: "Block1/Block2 option repeated", Bytes: dup(datagram)}
	}
	if m.Code == Empty {
		if len(m.Options) != 0 || len(m.Payload) != 0 || tkl != 0 {
			return nil, &MessageParseError{Reason: "Empty message carries token/options/payload", Bytes: dup(datagram)}
		}
		if t != ACK && t != RST {
			return nil, &MessageParseError{Reason: "Empty message must be ACK or RST", Bytes: dup(datagram)}
		}
	}
	if t == ACK || t == RST {
		// Only Empty messages are used as bare ACKs here, but ACK with a
		// response code is the normal piggyback case; RST must be Empty.
		if t == RST && m.Code != Empty {
			return nil, &MessageParseError{Reason: "RST must be Empty (0.00)", Bytes: dup(datagram)}
		}
	}
	return m, nil
}

func readExt(b []byte, p, code int, what string) (int, int, error) {
	switch code {
	case 13:
		if p+1 > len(b) {
			return 0, p, &MessageParseError{Reason: "truncated extended " + what, Bytes: dup(b)}
		}
		return int(b[p]) + 13, p + 1, nil
	case 14:
		if p+2 > len(b) {
			return 0, p, &MessageParseError{Reason: "truncated extended " + what, Bytes: dup(b)}
		}
		return (int(b[p])<<8 | int(b[p+1])) + 269, p + 2, nil
	default:
		return code, p, nil
	}
}

func dup(b []byte) []byte {
	if b == nil {
		return nil
	}
	out := make([]byte, len(b))
	copy(out, b)
	return out
}

// EmptyACK builds the bare ACK matching a CON's Message ID.
func EmptyACK(mid uint16) *Message {
	return &Message{Type: ACK, Code: Empty, MID: mid}
}

// EmptyRST builds a Reset for a CON/NON the peer cannot process.
func EmptyRST(mid uint16) *Message {
	return &Message{Type: RST, Code: Empty, MID: mid}
}
