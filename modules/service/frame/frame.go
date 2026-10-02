// Package frame implements the small subset of HTTP/2 framing (RFC 7540)
// that the controlled HPACK service needs: reading a client preface,
// SETTINGS/HEADERS/CONTINUATION and the few frame types that must at least
// be tolerated, plus writing SETTINGS, HEADERS, DATA and GOAWAY frames.
//
// HPACK itself lives elsewhere; this package deals only with the 9-octet
// frame header, flags and padding.
package frame

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// Frame type constants (RFC 7540 section 11.2).
const (
	TypeData         = 0x0
	TypeHeaders      = 0x1
	TypePriority     = 0x2
	TypeRSTStream    = 0x3
	TypeSettings     = 0x4
	TypePushPromise  = 0x5
	TypePing         = 0x6
	TypeGoAway       = 0x7
	TypeWindowUpdate = 0x8
	TypeContinuation = 0x9
)

// Flag bits (RFC 7540 section 6).
const (
	FlagAck        = 0x01
	FlagEndStream  = 0x01
	FlagEndHeaders = 0x04
	FlagPadded     = 0x08
	FlagPriority   = 0x20
)

// ErrFrameTooLarge is returned when a frame declares a payload above the
// configured maximum.
var ErrFrameTooLarge = errors.New("http2/frame: payload exceeds max frame size")

// MaxFrameSizeMin/Max are RFC 7540 section 6.5.2 bounds.
const (
	MaxFrameSizeDefault = 16384
	MaxFrameSizeMin     = 16384
	MaxFrameSizeMax     = 16777215
)

// Header is a decoded 9-octet frame header.
type Header struct {
	Type   byte
	Flags  byte
	Stream uint32
	Length uint32
}

// EndStream reports the END_STREAM flag (valid for HEADERS/DATA).
func (h Header) EndStream() bool { return h.Flags&FlagEndStream != 0 }

// EndHeaders reports END_HEADERS (valid for HEADERS/CONTINUATION/PUSH_PROMISE).
func (h Header) EndHeaders() bool { return h.Flags&FlagEndHeaders != 0 }

// Reader reads frames from one HTTP/2 connection.
type Reader struct {
	r       io.Reader
	maxSize uint32
}

// NewReader builds a frame reader with the given payload cap.
func NewReader(r io.Reader, maxSize uint32) *Reader {
	if maxSize < MaxFrameSizeMin {
		maxSize = MaxFrameSizeMin
	}
	return &Reader{r: r, maxSize: maxSize}
}

// ReadFrame returns the next frame header and a payload that is valid only
// until the next ReadFrame call (callers copy what they need).
func (r *Reader) ReadFrame() (Header, []byte, error) {
	var frameHeader [9]byte
	if _, err := io.ReadFull(r.r, frameHeader[:]); err != nil {
		return Header{}, nil, err
	}
	h := Header{
		Length: uint32(frameHeader[0])<<16 | uint32(frameHeader[1])<<8 | uint32(frameHeader[2]),
		Type:   frameHeader[3],
		Flags:  frameHeader[4],
		Stream: binary.BigEndian.Uint32(frameHeader[5:9]) & 0x7fffffff,
	}
	if h.Length > r.maxSize {
		return h, nil, fmt.Errorf("%w: declared %d > %d", ErrFrameTooLarge, h.Length, r.maxSize)
	}
	payload := make([]byte, h.Length)
	if _, err := io.ReadFull(r.r, payload); err != nil {
		return h, nil, err
	}
	return h, payload, nil
}

// Writer writes HTTP/2 frames.
type Writer struct {
	w io.Writer
}

// NewWriter builds a frame writer.
func NewWriter(w io.Writer) *Writer { return &Writer{w: w} }

func (w *Writer) write(typ byte, flags byte, stream uint32, payload []byte) error {
	n := len(payload)
	var hdrBuf [9]byte
	hdrBuf[0] = byte(n >> 16)
	hdrBuf[1] = byte(n >> 8)
	hdrBuf[2] = byte(n)
	hdrBuf[3] = typ
	hdrBuf[4] = flags
	binary.BigEndian.PutUint32(hdrBuf[5:9], stream&0x7fffffff)
	if _, err := w.w.Write(hdrBuf[:]); err != nil {
		return err
	}
	if n > 0 {
		_, err := w.w.Write(payload)
		return err
	}
	return nil
}

// Settings writes a SETTINGS frame; each setting is an id/value pair.
func (w *Writer) Settings(ack bool, settings ...[2]uint32) error {
	flags := byte(0)
	if ack {
		flags = FlagAck
	}
	payload := make([]byte, 0, 6*len(settings))
	for _, s := range settings {
		buf := make([]byte, 6)
		binary.BigEndian.PutUint16(buf[0:2], uint16(s[0]))
		binary.BigEndian.PutUint32(buf[2:6], s[1])
		payload = append(payload, buf...)
	}
	return w.write(TypeSettings, flags, 0, payload)
}

// Headers writes a HEADERS frame carrying blockFragment. The caller decides
// whether END_HEADERS/END_STREAM are set.
func (w *Writer) Headers(stream uint32, flags byte, blockFragment []byte) error {
	return w.write(TypeHeaders, flags, stream, blockFragment)
}

// Continuation writes a CONTINUATION frame.
func (w *Writer) Continuation(stream uint32, flags byte, blockFragment []byte) error {
	return w.write(TypeContinuation, flags, stream, blockFragment)
}

// Data writes a DATA frame.
func (w *Writer) Data(stream uint32, flags byte, data []byte) error {
	return w.write(TypeData, flags, stream, data)
}

// GoAway writes a GOAWAY frame.
func (w *Writer) GoAway(lastStream uint32, code uint32, debug []byte) error {
	payload := make([]byte, 8+len(debug))
	binary.BigEndian.PutUint32(payload[0:4], lastStream&0x7fffffff)
	binary.BigEndian.PutUint32(payload[4:8], code)
	copy(payload[8:], debug)
	return w.write(TypeGoAway, 0, 0, payload)
}

// RSTStream writes a RST_STREAM frame.
func (w *Writer) RSTStream(stream uint32, code uint32) error {
	payload := make([]byte, 4)
	binary.BigEndian.PutUint32(payload, code)
	return w.write(TypeRSTStream, 0, stream, payload)
}

// Ping writes a PING frame.
func (w *Writer) Ping(ack bool, data [8]byte) error {
	flags := byte(0)
	if ack {
		flags = FlagAck
	}
	return w.write(TypePing, flags, 0, data[:])
}

// WindowUpdate writes a WINDOW_UPDATE frame.
func (w *Writer) WindowUpdate(stream, inc uint32) error {
	payload := make([]byte, 4)
	binary.BigEndian.PutUint32(payload, inc&0x7fffffff)
	return w.write(TypeWindowUpdate, 0, stream, payload)
}
