// Package h2t is a tiny HTTP/2 test client for the controlled hpackd
// service. It speaks just enough of RFC 7540 over a plain TCP h2c
// connection to send one HEADERS block and collect the server's frames.
package h2t

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"time"

	"hpacklab.local/hpack"
	"hpacklab.local/service/frame"
)

var preface = []byte("PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")

// Frame is one received HTTP/2 frame.
type Frame struct {
	Type    byte
	Flags   byte
	Stream  uint32
	Payload []byte
}

// Conn is a test h2c connection.
type Conn struct {
	raw net.Conn
	br  *bufio.Reader
	fw  *frame.Writer

	// Encoder/Decoder are the CLIENT-side HPACK state, independent of the
	// server's.
	Encoder *hpack.Encoder
	Decoder *hpack.Decoder
}

// Dial opens an h2c connection with prior knowledge, completes the preface
// and SETTINGS handshake, and returns when the server's SETTINGS arrived.
func Dial(addr string, clientSettings ...[2]uint32) (*Conn, error) {
	raw, err := net.DialTimeout("tcp", addr, 5*time.Second)
	if err != nil {
		return nil, err
	}
	br := bufio.NewReader(raw)
	c := &Conn{
		raw:     raw,
		br:      br,
		fw:      frame.NewWriter(raw),
		Encoder: hpack.NewEncoder(hpack.EncoderOptions{MaxTableSize: 4096, Huffman: true}),
		Decoder: hpack.NewDecoder(hpack.DecoderOptions{MaxTableSize: 4096, Limits: hpack.DefaultLimits()}),
	}
	if _, err := raw.Write(preface); err != nil {
		return nil, err
	}
	if err := c.fw.Settings(false, clientSettings...); err != nil {
		return nil, err
	}
	// Read until we see the server SETTINGS frame; ACK it and drain the
	// server's ACK of our own SETTINGS.
	deadline := time.Now().Add(5 * time.Second)
	_ = raw.SetReadDeadline(deadline)
	sawSettings := false
	for !sawSettings {
		f, err := c.readFrame()
		if err != nil {
			return nil, err
		}
		if f.Type == frame.TypeSettings && f.Flags&frame.FlagAck == 0 {
			sawSettings = true
			if err := c.fw.Settings(true); err != nil {
				return nil, err
			}
		}
	}
	_ = raw.SetReadDeadline(time.Time{})
	return c, nil
}

func (c *Conn) readFrame() (Frame, error) {
	var hdr [9]byte
	if _, err := io.ReadFull(c.br, hdr[:]); err != nil {
		return Frame{}, err
	}
	f := Frame{
		Type:   hdr[3],
		Flags:  hdr[4],
		Stream: binary.BigEndian.Uint32(hdr[5:9]) & 0x7fffffff,
	}
	n := uint32(hdr[0])<<16 | uint32(hdr[1])<<8 | uint32(hdr[2])
	f.Payload = make([]byte, n)
	if _, err := io.ReadFull(c.br, f.Payload); err != nil {
		return Frame{}, err
	}
	return f, nil
}

// SendRawHeaders sends a HEADERS frame with END_STREAM|END_HEADERS carrying
// exactly block, allowing tests to inject malformed HPACK.
func (c *Conn) SendRawHeaders(stream uint32, block []byte) error {
	return c.fw.Headers(stream, frame.FlagEndStream|frame.FlagEndHeaders, block)
}

// SendHeaders encodes fields with the client encoder and sends a HEADERS
// frame with END_STREAM|END_HEADERS.
func (c *Conn) SendHeaders(stream uint32, fields []hpack.HeaderField) error {
	block := c.Encoder.EncodeBlock(fields)
	return c.SendRawHeaders(stream, block)
}

// ReadResponse reads frames up to and including an END_HEADERS-bearing
// HEADERS frame on stream, decoding its block (plus any CONTINUATION) with
// the client decoder. Other frames are returned in extra for inspection
// (GOAWAY in particular).
func (c *Conn) ReadResponse(stream uint32, timeout time.Duration) (fields []hpack.HeaderField, extra []Frame, err error) {
	_ = c.raw.SetReadDeadline(time.Now().Add(timeout))
	var block []byte
	for {
		f, rerr := c.readFrame()
		if rerr != nil {
			return nil, extra, rerr
		}
		switch f.Type {
		case frame.TypeHeaders, frame.TypeContinuation:
			block = append(block, f.Payload...)
			if f.Flags&frame.FlagEndHeaders != 0 {
				got, derr := c.Decoder.DecodeBlock(block)
				if derr != nil {
					return nil, extra, fmt.Errorf("client decoded bad server block: %w", derr)
				}
				return got, extra, nil
			}
		default:
			extra = append(extra, f)
			if f.Type == frame.TypeGoAway {
				return nil, extra, nil
			}
		}
	}
}

// Close tears down the connection.
func (c *Conn) Close() error { return c.raw.Close() }

// GoAwayCode extracts the error code from a GOAWAY frame payload.
func GoAwayCode(f Frame) (lastStream, code uint32, debug string, ok bool) {
	if f.Type != frame.TypeGoAway || len(f.Payload) < 8 {
		return 0, 0, "", false
	}
	return binary.BigEndian.Uint32(f.Payload[0:4]), binary.BigEndian.Uint32(f.Payload[4:8]), string(bytes.TrimRight(f.Payload[8:], "\x00")), true
}
