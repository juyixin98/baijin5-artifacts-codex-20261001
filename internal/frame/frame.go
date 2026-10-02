// Package frame implements the HTTP/2 frame byte codec (RFC 7540 §4.1).
// It is a pure byte-level layer: no protocol state is kept here.
package frame

import (
	"encoding/binary"
	"fmt"
	"io"
)

// HeaderLen is the fixed length of an HTTP/2 frame header in bytes.
const HeaderLen = 9

// Frame size bounds (RFC 7540 §4.2).
const (
	DefaultMaxFrameSize uint32 = 16384
	MaxAllowedFrameSize uint32 = 16777215 // 2^24-1
)

// Type is the 8-bit frame type field.
type Type uint8

const (
	Data         Type = 0x0
	Headers      Type = 0x1
	Priority     Type = 0x2
	RSTStream    Type = 0x3
	Settings     Type = 0x4
	PushPromise  Type = 0x5
	Ping         Type = 0x6
	GoAway       Type = 0x7
	WindowUpdate Type = 0x8
	Continuation Type = 0x9
)

func (t Type) String() string {
	switch t {
	case Data:
		return "DATA"
	case Headers:
		return "HEADERS"
	case Priority:
		return "PRIORITY"
	case RSTStream:
		return "RST_STREAM"
	case Settings:
		return "SETTINGS"
	case PushPromise:
		return "PUSH_PROMISE"
	case Ping:
		return "PING"
	case GoAway:
		return "GOAWAY"
	case WindowUpdate:
		return "WINDOW_UPDATE"
	case Continuation:
		return "CONTINUATION"
	default:
		return fmt.Sprintf("UNKNOWN(0x%x)", uint8(t))
	}
}

// Frame flags.
const (
	FlagEndStream  uint8 = 0x1 // DATA, HEADERS
	FlagAck        uint8 = 0x1 // SETTINGS, PING
	FlagEndHeaders uint8 = 0x4 // HEADERS, CONTINUATION
	FlagPadded     uint8 = 0x8 // DATA, HEADERS
	FlagPriority   uint8 = 0x20
)

// ErrCode is a 32-bit HTTP/2 error code (RFC 7540 §7).
type ErrCode uint32

const (
	NoError            ErrCode = 0x0
	ProtocolError      ErrCode = 0x1
	InternalError      ErrCode = 0x2
	FlowControlError   ErrCode = 0x3
	SettingsTimeout    ErrCode = 0x4
	StreamClosed       ErrCode = 0x5
	FrameSizeError     ErrCode = 0x6
	RefusedStream      ErrCode = 0x7
	Cancel             ErrCode = 0x8
	CompressionError   ErrCode = 0x9
	ConnectError       ErrCode = 0xa
	EnhanceYourCalm    ErrCode = 0xb
	InadequateSecurity ErrCode = 0xc
	HTTP11Required     ErrCode = 0xd
)

func (c ErrCode) String() string {
	names := map[ErrCode]string{
		NoError:            "NO_ERROR",
		ProtocolError:      "PROTOCOL_ERROR",
		InternalError:      "INTERNAL_ERROR",
		FlowControlError:   "FLOW_CONTROL_ERROR",
		SettingsTimeout:    "SETTINGS_TIMEOUT",
		StreamClosed:       "STREAM_CLOSED",
		FrameSizeError:     "FRAME_SIZE_ERROR",
		RefusedStream:      "REFUSED_STREAM",
		Cancel:             "CANCEL",
		CompressionError:   "COMPRESSION_ERROR",
		ConnectError:       "CONNECT_ERROR",
		EnhanceYourCalm:    "ENHANCE_YOUR_CALM",
		InadequateSecurity: "INADEQUATE_SECURITY",
		HTTP11Required:     "HTTP_1_1_REQUIRED",
	}
	if n, ok := names[c]; ok {
		return n
	}
	return fmt.Sprintf("UNKNOWN(0x%x)", uint32(c))
}

// Header is the 9-octet frame header.
type Header struct {
	Length   uint32 // 24-bit payload length
	Type     Type
	Flags    uint8
	StreamID uint32 // 31-bit
}

func (h Header) String() string {
	return fmt.Sprintf("%s len=%d flags=0x%02x stream=%d", h.Type, h.Length, h.Flags, h.StreamID)
}

// Marshal encodes the header into its 9-byte wire form.
func (h Header) Marshal() [HeaderLen]byte {
	var b [HeaderLen]byte
	b[0] = byte(h.Length >> 16)
	b[1] = byte(h.Length >> 8)
	b[2] = byte(h.Length)
	b[3] = byte(h.Type)
	b[4] = h.Flags
	binary.BigEndian.PutUint32(b[5:9], h.StreamID&0x7fffffff)
	return b
}

// ParseHeader decodes a 9-byte wire header.
func ParseHeader(b [HeaderLen]byte) Header {
	return Header{
		Length:   uint32(b[0])<<16 | uint32(b[1])<<8 | uint32(b[2]),
		Type:     Type(b[3]),
		Flags:    b[4],
		StreamID: binary.BigEndian.Uint32(b[5:9]) & 0x7fffffff,
	}
}

// ReadHeader reads exactly one frame header from r.
func ReadHeader(r io.Reader) (Header, error) {
	var b [HeaderLen]byte
	if _, err := io.ReadFull(r, b[:]); err != nil {
		return Header{}, err
	}
	return ParseHeader(b), nil
}

// SettingsID is a SETTINGS parameter identifier (RFC 7540 §6.5.2).
type SettingsID uint16

const (
	SettingsHeaderTableSize      SettingsID = 0x1
	SettingsEnablePush           SettingsID = 0x2
	SettingsMaxConcurrentStreams SettingsID = 0x3
	SettingsInitialWindowSize    SettingsID = 0x4
	SettingsMaxFrameSize         SettingsID = 0x5
	SettingsMaxHeaderListSize    SettingsID = 0x6
)

// ClientPreface is the magic octet sequence a client sends first (RFC 7540 §3.5).
const ClientPreface = "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
