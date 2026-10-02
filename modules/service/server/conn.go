package server

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/binary"
	"fmt"
	"io"
	"log/slog"
	"net"
	"sync/atomic"

	"hpacklab.local/hpack"
	"hpacklab.local/service/frame"
)

// HTTP/2 error codes (RFC 7540 section 7).
const (
	errCodeProtocol        uint32 = 0x1
	errCodeInternal        uint32 = 0x2
	errCodeFlowControl     uint32 = 0x3
	errCodeSettingsTimeout uint32 = 0x4
	errCodeStreamClosed    uint32 = 0x5
	errCodeFrameSize       uint32 = 0x6
	errCodeRefusedStream   uint32 = 0x7
	errCodeCancel          uint32 = 0x8
	errCodeCompression     uint32 = 0x9
	errCodeProtocolEnhance uint32 = 0xb
	errCodeHTTP11Required  uint32 = 0xd
)

// SETTINGS identifiers we use.
const (
	settingHeaderTableSize      uint16 = 0x1
	settingEnablePush           uint16 = 0x2
	settingMaxConcurrentStreams uint16 = 0x3
	settingMaxFrameSize         uint16 = 0x5
	settingMaxHeaderListSize    uint16 = 0x6
)

// clientPreface is the fixed HTTP/2 connection preface (RFC 7540 3.5).
var clientPreface = []byte("PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")

var connCounter uint64

type conn struct {
	raw net.Conn
	srv *Server
	br  *bufio.Reader
	bw  *bufio.Writer
	fr  *frame.Reader
	fw  *frame.Writer
	log *slog.Logger

	connID string

	// Per-connection HPACK state. These are NEVER shared with another
	// connection and are replaced by nothing after a failure: a decode
	// error is fatal for the connection.
	decoder *hpack.Decoder
	encoder *hpack.Encoder
	obs     *eventLogger

	peerMaxFrameSize uint32
	lastStreamID     uint32
}

func newID(prefix string) string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	n := atomic.AddUint64(&connCounter, 1)
	return fmt.Sprintf("%s-%d-%x", prefix, n, b[:4])
}

func newConn(raw net.Conn, srv *Server) *conn {
	connID := newID("conn")
	br := bufio.NewReader(raw)
	obs := newEventLogger(srv.log, connID, srv.cfg.Logging.EchoHeaders)
	limits := hpack.Limits{
		MaxStringLen:          srv.cfg.Hpack.MaxStringLen,
		MaxHeaderBlockEmitted: srv.cfg.Hpack.MaxHeaderBlockEmitted,
		MaxHeaderFields:       srv.cfg.Hpack.MaxHeaderFields,
	}
	return &conn{
		raw:              raw,
		srv:              srv,
		br:               br,
		bw:               bufio.NewWriter(raw),
		fr:               frame.NewReader(br, frame.MaxFrameSizeMax),
		fw:               frame.NewWriter(nil), // replaced after buf wrap below
		log:              srv.log.With("conn_id", connID),
		connID:           connID,
		peerMaxFrameSize: frame.MaxFrameSizeDefault,
		decoder: hpack.NewDecoder(hpack.DecoderOptions{
			MaxTableSize: srv.cfg.Hpack.TableSize,
			Limits:       limits,
			Observer:     obs,
		}),
		encoder: hpack.NewEncoder(hpack.EncoderOptions{
			MaxTableSize: srv.cfg.Hpack.TableSize,
			Huffman:      srv.cfg.Hpack.Huffman,
		}),
		obs: obs,
	}
}

func (c *conn) run(ctx context.Context) {
	defer c.raw.Close()
	// Wire the writer to the buffered writer.
	c.fw = frame.NewWriter(c.bw)

	if err := c.handshake(); err != nil {
		c.log.Warn("handshake failed", "error", err.Error())
		return
	}
	if err := c.loop(ctx); err != nil && !ignoreConnClose(err) {
		c.log.Warn("connection ended with error", "error", err.Error())
	}
}

// handshake reads the client preface + client SETTINGS and sends the
// server SETTINGS + acknowledgment.
func (c *conn) handshake() error {
	preface := make([]byte, len(clientPreface))
	if _, err := io.ReadFull(c.br, preface); err != nil {
		return fmt.Errorf("read preface: %w", err)
	}
	if !bytes.Equal(preface, clientPreface) {
		return fmt.Errorf("not an HTTP/2 client preface")
	}
	// First frame MUST be the client's SETTINGS (ack flag clear).
	h, payload, err := c.fr.ReadFrame()
	if err != nil {
		return fmt.Errorf("read client settings: %w", err)
	}
	if h.Type != frame.TypeSettings || h.Flags&frame.FlagAck != 0 || h.Stream != 0 {
		return c.fatal(errCodeProtocol, "expected client SETTINGS")
	}
	if err := c.applySettings(payload); err != nil {
		return c.fatal(errCodeProtocol, err.Error())
	}
	// Advertise our settings, then ack the client's.
	if err := c.fw.Settings(false,
		[2]uint32{uint32(settingHeaderTableSize), uint32(c.srv.cfg.Hpack.TableSize)},
		[2]uint32{uint32(settingEnablePush), 0},
		[2]uint32{uint32(settingMaxConcurrentStreams), uint32(c.srv.cfg.Server.MaxConcurrentStreams)},
		[2]uint32{uint32(settingMaxFrameSize), frame.MaxFrameSizeDefault},
	); err != nil {
		return err
	}
	if err := c.fw.Settings(true); err != nil {
		return err
	}
	return c.flush()
}

// applySettings reads the client's SETTINGS payload. A changed
// HEADER_TABLE_SIZE reconfigures our decoder's allowed ceiling.
func (c *conn) applySettings(payload []byte) error {
	if len(payload)%6 != 0 {
		return fmt.Errorf("malformed SETTINGS payload")
	}
	for i := 0; i < len(payload); i += 6 {
		id := binary.BigEndian.Uint16(payload[i : i+2])
		val := binary.BigEndian.Uint32(payload[i+2 : i+6])
		switch id {
		case settingHeaderTableSize:
			// The peer's advertised size bounds the table WE maintain for
			// encoding responses (our encoder). It does NOT change the
			// ceiling for size updates the peer may send to us, which is
			// fixed by the value WE advertised and was set at construction.
			// UpdateMaxTableSize queues a leading size update on the next
			// response block (RFC 7541 section 4.2).
			c.encoder.UpdateMaxTableSize(val)
		case settingMaxFrameSize:
			if val < frame.MaxFrameSizeMin || val > frame.MaxFrameSizeMax {
				return fmt.Errorf("invalid MAX_FRAME_SIZE %d", val)
			}
			c.peerMaxFrameSize = val
		case settingEnablePush:
			if val != 0 && val != 1 {
				return fmt.Errorf("invalid ENABLE_PUSH %d", val)
			}
		case settingMaxConcurrentStreams, settingMaxHeaderListSize:
			// Accepted but not otherwise enforced in this controlled server.
		default:
			// Unknown settings are ignored per RFC 7540 6.5.3.
		}
	}
	return nil
}

func (c *conn) flush() error { return c.bw.Flush() }

// fatal sends GOAWAY and returns an error to stop the connection.
func (c *conn) fatal(code uint32, detail string) error {
	c.log.Error("sending GOAWAY", "error_code", code, "detail", detail)
	_ = c.fw.GoAway(c.lastStreamID, code, []byte(detail))
	_ = c.flush()
	return fmt.Errorf("connection error 0x%x: %s", code, detail)
}
