// Package wire implements RFC 7252 message framing and the RFC 7959 Block
// options. It is deliberately free of any retransmission or blockwise state:
// its only responsibility is converting between Message values and datagrams.
package wire

import (
	"bytes"
	"fmt"
	"sort"
)

// Version is the CoAP protocol version implemented by this package.
const Version = 1

// MaxDatagram bounds what this implementation will emit or accept. RFC 7952
// recommends staying within the IPv6 minimum MTU (1280 bytes); block-wise
// transfer exists precisely so individual datagrams stay below it.
const MaxDatagram = 1280

// Type is the 2-bit message type in the first header byte.
type Type uint8

// Message types as defined by RFC 7252 section 4.2.
const (
	CON Type = 0 // Confirmable
	NON Type = 1 // Non-confirmable
	ACK Type = 2 // Acknowledgement
	RST Type = 3 // Reset
)

func (t Type) String() string {
	switch t {
	case CON:
		return "CON"
	case NON:
		return "NON"
	case ACK:
		return "ACK"
	case RST:
		return "RST"
	default:
		return fmt.Sprintf("Type(%d)", uint8(t))
	}
}

// Code is an 8-bit CoAP code (class in the high 3 bits, detail low 5).
type Code uint8

// Standard codes used by this implementation.
const (
	CodeEmpty Code = 0

	CodeGET    Code = 1
	CodePOST   Code = 2
	CodePUT    Code = 3
	CodeDELETE Code = 4

	CodeCreated      Code = 2<<5 | 1  // 2.01
	CodeDeleted      Code = 2<<5 | 2  // 2.02
	CodeValid        Code = 2<<5 | 3  // 2.03
	CodeChanged      Code = 2<<5 | 4  // 2.04
	CodeContent      Code = 2<<5 | 5  // 2.05
	CodeContinue     Code = 2<<5 | 31 // 2.31, RFC 7959
	CodeBadRequest   Code = 4<<5 | 0  // 4.00
	CodeBadOption    Code = 4<<5 | 2  // 4.02
	CodeNotFound     Code = 4<<5 | 4  // 4.04
	CodeNotAccept    Code = 4<<5 | 6  // 4.06
	CodeConflict     Code = 4<<5 | 9  // 4.09
	CodeIncomplete   Code = 4<<5 | 8  // 4.08 Request Entity Incomplete
	CodeEntityTooBig Code = 4<<5 | 13 // 4.13
	CodeUnsupported  Code = 4<<5 | 15 // 4.15 Unsupported Content-Format
	CodeInternal     Code = 5<<5 | 0  // 5.00
)

// Class returns the high 3-bit response/request class, e.g. 2 or 4.
func (c Code) Class() uint8 { return uint8(c) >> 5 }

// Detail returns the low 5-bit detail, e.g. 5 for 2.05.
func (c Code) Detail() uint8 { return uint8(c) & 0x1f }

// String renders the canonical name for methods ("GET") and the
// "class.detail" form for responses, e.g. "2.05".
func (c Code) String() string {
	switch c {
	case CodeEmpty:
		return "0.00"
	case CodeGET:
		return "GET"
	case CodePOST:
		return "POST"
	case CodePUT:
		return "PUT"
	case CodeDELETE:
		return "DELETE"
	default:
		return fmt.Sprintf("%d.%02d", c.Class(), c.Detail())
	}
}

// IsRequest reports whether the code is a request method.
func (c Code) IsRequest() bool { return c >= CodeGET && c <= CodeDELETE }

// IsSuccess reports a 2.xx code.
func (c Code) IsSuccess() bool { return c.Class() == 2 }

// Option numbers referenced by this implementation.
const (
	OptIfMatch       = 1
	OptUriHost       = 3
	OptETag          = 4
	OptIfNoneMatch   = 5
	OptUriPort       = 7
	OptLocationPath  = 8
	OptUriPath       = 11
	OptContentFormat = 12
	OptMaxAge        = 14
	OptUriQuery      = 15
	OptAccept        = 17
	OptLocationQuery = 20
	OptBlock2        = 23
	OptBlock1        = 27
	OptSize2         = 28
	OptProxyURI      = 35
	OptSize1         = 60
)

// PayloadMarker separates options from payload (RFC 7252 section 3).
const PayloadMarker = 0xFF

// MaxTokenLength is the largest legal token (TKL is a 4-bit field).
const MaxTokenLength = 8

// Message is one CoAP datagram in decoded form.
type Message struct {
	Type      Type
	Code      Code
	MessageID uint16
	Token     []byte
	Options   Options
	Payload   []byte
}

// NewMessage builds a message with an empty, canonical option set.
func NewMessage(t Type, code Code, mid uint16, token []byte) *Message {
	tok := append([]byte(nil), token...)
	return &Message{Type: t, Code: code, MessageID: mid, Token: tok}
}

// ResponseTo builds a piggybacked ACK response. The token is echoed, because
// tokens correlate the request/response pair while the MID correlates the
// message exchange; the two identifiers must never be conflated.
func ResponseTo(req *Message, code Code) *Message {
	m := NewMessage(ACK, code, req.MessageID, req.Token)
	return m
}

// EmptyACK builds a four-byte empty acknowledgement (separate-response flow).
func EmptyACK(mid uint16) *Message {
	return &Message{Type: ACK, Code: CodeEmpty, MessageID: mid}
}

// EmptyRST builds a four-byte Reset, used for unknown CON messages.
func EmptyRST(mid uint16) *Message {
	return &Message{Type: RST, Code: CodeEmpty, MessageID: mid}
}

// IsEmpty reports whether this is a 4-byte code-0.00 message.
func (m *Message) IsEmpty() bool {
	return m.Code == CodeEmpty && len(m.Token) == 0 && len(m.Options) == 0 &&
		len(m.Payload) == 0
}

// Path joins repeated Uri-Path options with "/" (without a leading slash).
func (m *Message) Path() string {
	segs := m.Options.Strings(OptUriPath)
	return joinPath(segs)
}

func joinPath(segs []string) string {
	var b bytes.Buffer
	for i, s := range segs {
		if i > 0 {
			b.WriteByte('/')
		}
		b.WriteString(s)
	}
	return b.String()
}

// Encode returns the on-the-wire bytes. Options are copied and sorted so the
// output is canonical; callers do not have to insert them in order.
func (m *Message) Encode() ([]byte, error) {
	if len(m.Token) > MaxTokenLength {
		return nil, fmt.Errorf("%w: token length %d > %d", ErrMalformed,
			len(m.Token), MaxTokenLength)
	}
	if m.Code == CodeEmpty && (!m.isEmptyBody()) {
		return nil, fmt.Errorf("%w: code 0.00 must be an empty message", ErrMalformed)
	}
	opts := append(Options(nil), m.Options...)
	sort.Sort(opts)
	if err := validateOptions(opts); err != nil {
		return nil, err
	}

	var b bytes.Buffer
	first := byte(Version<<6) | byte(m.Type<<4) | byte(len(m.Token))
	b.WriteByte(first)
	b.WriteByte(byte(m.Code))
	b.WriteByte(byte(m.MessageID >> 8))
	b.WriteByte(byte(m.MessageID))
	b.Write(m.Token)

	prev := 0
	for _, o := range opts {
		if err := writeOption(&b, o.Number, o.Value, &prev); err != nil {
			return nil, err
		}
	}
	if len(m.Payload) > 0 {
		b.WriteByte(PayloadMarker)
		b.Write(m.Payload)
	}
	if b.Len() > MaxDatagram {
		return nil, fmt.Errorf("%w: datagram %d bytes > %d",
			ErrTooLarge, b.Len(), MaxDatagram)
	}
	return b.Bytes(), nil
}

func (m *Message) isEmptyBody() bool {
	return len(m.Options) == 0 && len(m.Payload) == 0 && len(m.Token) == 0
}
