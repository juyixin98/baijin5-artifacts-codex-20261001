// Package h2 implements the HTTP/2 connection/stream protocol state machine
// (RFC 7540) on top of the frame codec. Connection-level errors and
// stream-level errors are modeled as distinct types and handled separately.
package h2

import (
	"encoding/binary"
	"errors"
	"fmt"
	"sync"

	"h2svc/internal/frame"
	"h2svc/internal/hpack"
)

// Config tunes one connection's protocol behavior.
type Config struct {
	// MaxFrameSize is the largest inbound frame payload accepted, and the
	// value advertised in our SETTINGS_MAX_FRAME_SIZE.
	MaxFrameSize uint32
	// InitialRecvWindow is our per-stream receive window (advertised).
	InitialRecvWindow int64
	// SendQueueCapacity bounds the outbound frame queue.
	SendQueueCapacity int
	// MaxPendingBodyBytes bounds per-stream buffered response body bytes
	// waiting on flow-control window or queue space.
	MaxPendingBodyBytes int
}

func (c Config) withDefaults() Config {
	if c.MaxFrameSize == 0 {
		c.MaxFrameSize = frame.DefaultMaxFrameSize
	}
	if c.InitialRecvWindow == 0 {
		c.InitialRecvWindow = 65535
	}
	if c.SendQueueCapacity == 0 {
		c.SendQueueCapacity = 64
	}
	if c.MaxPendingBodyBytes == 0 {
		c.MaxPendingBodyBytes = 1 << 20
	}
	return c
}

// EventLogger receives diagnostic events; implemented by the journal layer.
type EventLogger interface {
	LogEvent(connID uint64, dir, frameType string, streamID uint32, detail, decision string)
}

// Handler is invoked (without the connection lock held) on request events.
type Handler interface {
	// OnHeaders fires when a complete header block has been decoded.
	OnHeaders(c *Conn, s *Stream)
	// OnData fires for request body DATA; endStream marks the final frame.
	OnData(c *Conn, s *Stream, data []byte, endStream bool)
}

// OutFrame is one queued outbound frame.
type OutFrame struct {
	Header  frame.Header
	Payload []byte
}

// ErrQueueFull is returned when the bounded outbound queue has no room.
var ErrQueueFull = errors.New("h2: outbound send queue full")

// ErrClosed is returned when operating on a closed connection.
var ErrClosed = errors.New("h2: connection closed")

type contState struct {
	streamID  uint32
	endStream bool
	frag      []byte
}

// Conn is one HTTP/2 connection's protocol state machine.
type Conn struct {
	id      uint64
	cfg     Config
	handler Handler
	logger  EventLogger

	mu                  sync.Mutex
	streams             map[uint32]*Stream
	highestClientStream uint32

	sendWindow int64 // connection-level outbound flow-control window
	recvWindow int64 // connection-level inbound flow-control window

	peerInitialWindow int64  // peer's SETTINGS_INITIAL_WINDOW_SIZE (our send)
	peerMaxFrameSize  uint32 // peer's SETTINGS_MAX_FRAME_SIZE (our send)

	cont *contState

	goAwaySent       bool
	goAwayLastStream uint32
	closed           bool

	outq chan OutFrame
	dec  *hpack.Decoder
}

// NewConn creates a server-side connection state machine.
func NewConn(id uint64, cfg Config, h Handler, logger EventLogger) *Conn {
	cfg = cfg.withDefaults()
	return &Conn{
		id:                id,
		cfg:               cfg,
		handler:           h,
		logger:            logger,
		streams:           make(map[uint32]*Stream),
		sendWindow:        65535,
		recvWindow:        cfg.InitialRecvWindow,
		peerInitialWindow: 65535,
		peerMaxFrameSize:  frame.DefaultMaxFrameSize,
		outq:              make(chan OutFrame, cfg.SendQueueCapacity),
		dec:               hpack.NewDecoder(),
	}
}

// Outbound returns the bounded queue of frames to write to the peer.
func (c *Conn) Outbound() <-chan OutFrame { return c.outq }

// QueueLen reports current outbound queue occupancy (diagnostics/tests).
func (c *Conn) QueueLen() int { return len(c.outq) }

func (c *Conn) log(dir, ftype string, streamID uint32, detail, decision string) {
	if c.logger != nil {
		c.logger.LogEvent(c.id, dir, ftype, streamID, detail, decision)
	}
}

// enqueueControl queues a control frame. Control frames never wait: if the
// bounded queue is full the connection is considered unwritable.
func (c *Conn) enqueueControl(h frame.Header, payload []byte) error {
	select {
	case c.outq <- OutFrame{Header: h, Payload: payload}:
		return nil
	default:
		return ErrQueueFull
	}
}

// enqueueData attempts a non-blocking queue of a DATA frame.
func (c *Conn) enqueueData(h frame.Header, payload []byte) error {
	select {
	case c.outq <- OutFrame{Header: h, Payload: payload}:
		return nil
	default:
		return ErrQueueFull
	}
}

// SendServerSettings queues our initial SETTINGS frame.
func (c *Conn) SendServerSettings() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return ErrClosed
	}
	p := make([]byte, 12)
	binary.BigEndian.PutUint16(p[0:2], uint16(frame.SettingsInitialWindowSize))
	binary.BigEndian.PutUint32(p[2:6], uint32(c.cfg.InitialRecvWindow))
	binary.BigEndian.PutUint16(p[6:8], uint16(frame.SettingsMaxFrameSize))
	binary.BigEndian.PutUint32(p[8:12], c.cfg.MaxFrameSize)
	h := frame.Header{Length: 12, Type: frame.Settings, StreamID: 0}
	c.log("out", "SETTINGS", 0, fmt.Sprintf("initial_window=%d max_frame=%d", c.cfg.InitialRecvWindow, c.cfg.MaxFrameSize), "advertise local settings")
	return c.enqueueControl(h, p)
}

// HandleFrame processes one inbound frame. A returned *ConnError is fatal:
// the caller should send GOAWAY (already queued by HandleFrame) and close.
// Stream-level errors are handled internally by queuing RST_STREAM and are
// not returned.
func (c *Conn) HandleFrame(h frame.Header, payload []byte) error {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return ErrClosed
	}
	c.log("in", h.Type.String(), h.StreamID, fmt.Sprintf("len=%d flags=0x%02x", h.Length, h.Flags), "")

	// CONTINUATION continuity (RFC 7540 §6.10): while a header block is
	// incomplete, only a CONTINUATION for that same stream may arrive.
	if c.cont != nil && (h.Type != frame.Continuation || h.StreamID != c.cont.streamID) {
		c.mu.Unlock()
		return c.fatalConnErr(frame.ProtocolError,
			"expected CONTINUATION for stream %d, got %s on stream %d", c.cont.streamID, h.Type, h.StreamID)
	}
	if c.cont == nil && h.Type == frame.Continuation {
		c.mu.Unlock()
		return c.fatalConnErr(frame.ProtocolError, "CONTINUATION without preceding HEADERS")
	}

	// Frame size bound (RFC 7540 §4.2).
	if h.Length > c.cfg.MaxFrameSize {
		c.mu.Unlock()
		return c.fatalConnErr(frame.FrameSizeError,
			"frame length %d exceeds max %d", h.Length, c.cfg.MaxFrameSize)
	}

	var err error
	switch h.Type {
	case frame.Data:
		err = c.handleData(h, payload)
	case frame.Headers:
		err = c.handleHeaders(h, payload)
	case frame.Priority:
		err = c.handlePriority(h, payload)
	case frame.RSTStream:
		err = c.handleRSTStream(h, payload)
	case frame.Settings:
		err = c.handleSettings(h, payload)
	case frame.Ping:
		err = c.handlePing(h, payload)
	case frame.GoAway:
		err = c.handleGoAway(h, payload)
	case frame.WindowUpdate:
		err = c.handleWindowUpdate(h, payload)
	case frame.Continuation:
		err = c.handleContinuation(h, payload)
	default:
		// Unknown extension frames must be discarded (RFC 7540 §5.5).
		c.log("in", h.Type.String(), h.StreamID, "", "ignored unknown frame type")
	}
	c.mu.Unlock()

	if ce, ok := err.(*ConnError); ok {
		return c.fatalConnErr(ce.Code, "%s", ce.Debug)
	}
	return nil
}

// fatalConnErr queues GOAWAY and marks the connection closed.
func (c *Conn) fatalConnErr(code frame.ErrCode, format string, args ...any) *ConnError {
	ce := connErr(code, format, args...)
	c.mu.Lock()
	defer c.mu.Unlock()
	c.log("out", "GOAWAY", 0, ce.Debug, "connection error: "+code.String())
	if !c.goAwaySent {
		c.goAwaySent = true
		c.goAwayLastStream = c.highestClientStream
		p := make([]byte, 8+len(ce.Debug))
		binary.BigEndian.PutUint32(p[0:4], c.goAwayLastStream)
		binary.BigEndian.PutUint32(p[4:8], uint32(code))
		copy(p[8:], ce.Debug)
		h := frame.Header{Length: uint32(len(p)), Type: frame.GoAway, StreamID: 0}
		// Best effort: the queue may be full; the connection is closing anyway.
		_ = c.enqueueControl(h, p)
	}
	c.closed = true
	return ce
}

// streamReset queues RST_STREAM for a stream-level error; connection stays up.
func (c *Conn) streamReset(se *StreamError) {
	c.log("out", "RST_STREAM", se.StreamID, se.Debug, "stream error: "+se.Code.String())
	p := make([]byte, 4)
	binary.BigEndian.PutUint32(p, uint32(se.Code))
	h := frame.Header{Length: 4, Type: frame.RSTStream, StreamID: se.StreamID}
	if err := c.enqueueControl(h, p); err != nil {
		c.log("out", "RST_STREAM", se.StreamID, se.Debug, "queue full, connection will be closed")
	}
	if s, ok := c.streams[se.StreamID]; ok {
		s.State = StateClosed
		s.closedByRST = true
	}
}

// stripPadding validates and removes padding (RFC 7540 §6.1/§6.2).
func stripPadding(h frame.Header, payload []byte) ([]byte, error) {
	if h.Flags&frame.FlagPadded == 0 {
		return payload, nil
	}
	if len(payload) < 1 {
		return nil, connErr(frame.ProtocolError, "padded frame missing pad length")
	}
	pad := int(payload[0])
	if pad >= len(payload) {
		return nil, connErr(frame.ProtocolError, "pad length %d >= payload length %d", pad, len(payload))
	}
	return payload[1 : len(payload)-pad], nil
}

func (c *Conn) handleData(h frame.Header, payload []byte) error {
	if h.StreamID == 0 {
		return connErr(frame.ProtocolError, "DATA on stream 0")
	}
	body, err := stripPadding(h, payload)
	if err != nil {
		return err
	}
	s, ok := c.streams[h.StreamID]
	if !ok {
		return connErr(frame.ProtocolError, "DATA on idle stream %d", h.StreamID)
	}

	// Flow-control accounting happens even on closed streams (RFC 7540 §6.9).
	flowLen := int64(len(payload))
	c.recvWindow -= flowLen
	s.recvWindow -= flowLen
	if c.recvWindow < 0 {
		return connErr(frame.FlowControlError, "connection receive window underflow by %d", -c.recvWindow)
	}
	if s.recvWindow < 0 {
		c.streamReset(streamErr(s.ID, frame.FlowControlError, "stream receive window underflow"))
		return nil
	}

	// Stream-level legality: DATA on a stream that is closed or half-closed
	// (remote) — e.g. a late frame after RST_STREAM — is a stream error, not
	// a connection error.
	if !s.canReceiveData() {
		c.streamReset(streamErr(s.ID, frame.StreamClosed,
			"DATA on %s stream", s.State))
		return nil
	}

	end := h.Flags&frame.FlagEndStream != 0
	if end {
		if s.State == StateOpen {
			s.State = StateHalfClosedRemote
		} else if s.State == StateHalfClosedLocal {
			s.State = StateClosed
		}
	}
	handler := c.handler
	data := append([]byte(nil), body...)
	c.mu.Unlock()
	if handler != nil {
		handler.OnData(c, s, data, end)
	}
	c.mu.Lock()
	return nil
}

func (c *Conn) handleHeaders(h frame.Header, payload []byte) error {
	if h.StreamID == 0 {
		return connErr(frame.ProtocolError, "HEADERS on stream 0")
	}
	body, err := stripPadding(h, payload)
	if err != nil {
		return err
	}
	if h.Flags&frame.FlagPriority != 0 {
		if len(body) < 5 {
			return connErr(frame.ProtocolError, "HEADERS priority field truncated")
		}
		body = body[5:]
	}

	s, ok := c.streams[h.StreamID]
	if !ok {
		// New stream from the client.
		if h.StreamID%2 == 0 {
			return connErr(frame.ProtocolError, "client opened even stream id %d", h.StreamID)
		}
		if h.StreamID <= c.highestClientStream {
			return connErr(frame.ProtocolError,
				"new stream %d not greater than previous %d", h.StreamID, c.highestClientStream)
		}
		if c.goAwaySent && h.StreamID > c.goAwayLastStream {
			// GOAWAY boundary: streams above lastStreamID are refused, the
			// connection itself stays usable for in-flight streams.
			c.streamReset(streamErr(h.StreamID, frame.RefusedStream,
				"stream id exceeds GOAWAY lastStreamID %d", c.goAwayLastStream))
			return nil
		}
		s = &Stream{
			ID:         h.StreamID,
			State:      StateOpen,
			sendWindow: c.peerInitialWindow,
			recvWindow: c.cfg.InitialRecvWindow,
		}
		c.streams[s.ID] = s
		c.highestClientStream = s.ID
		c.log("in", "HEADERS", s.ID, "", "stream opened: idle -> open")
	} else if s.State == StateHalfClosedRemote || s.State == StateClosed {
		c.streamReset(streamErr(s.ID, frame.StreamClosed, "HEADERS on %s stream", s.State))
		return nil
	}

	endStream := h.Flags&frame.FlagEndStream != 0
	if h.Flags&frame.FlagEndHeaders == 0 {
		// Header block continues in CONTINUATION frames; nothing else may
		// interleave (enforced in HandleFrame).
		c.cont = &contState{streamID: h.StreamID, endStream: endStream, frag: append([]byte(nil), body...)}
		return nil
	}
	return c.processHeaderBlock(s, body, endStream)
}

func (c *Conn) handleContinuation(h frame.Header, payload []byte) error {
	// cont != nil and stream match were verified in HandleFrame.
	c.cont.frag = append(c.cont.frag, payload...)
	if h.Flags&frame.FlagEndHeaders == 0 {
		return nil
	}
	cs := c.cont
	c.cont = nil
	s := c.streams[cs.streamID]
	return c.processHeaderBlock(s, cs.frag, cs.endStream)
}

func (c *Conn) processHeaderBlock(s *Stream, block []byte, endStream bool) error {
	fields, err := c.dec.Decode(block)
	if err != nil {
		return connErr(frame.CompressionError, "header block decode: %v", err)
	}
	s.Headers = fields
	if endStream {
		if s.State == StateOpen {
			s.State = StateHalfClosedRemote
		} else if s.State == StateHalfClosedLocal {
			s.State = StateClosed
		}
	}
	c.log("in", "HEADERS", s.ID, fmt.Sprintf("decoded %d fields, end_stream=%v", len(fields), endStream),
		"state -> "+s.State.String())
	handler := c.handler
	c.mu.Unlock()
	if handler != nil {
		handler.OnHeaders(c, s)
	}
	c.mu.Lock()
	return nil
}

func (c *Conn) handlePriority(h frame.Header, payload []byte) error {
	if h.StreamID == 0 {
		return connErr(frame.ProtocolError, "PRIORITY on stream 0")
	}
	if h.Length != 5 {
		c.streamReset(streamErr(h.StreamID, frame.FrameSizeError, "PRIORITY length %d != 5", h.Length))
		return nil
	}
	return nil // prioritization not implemented; frame accepted
}

func (c *Conn) handleRSTStream(h frame.Header, payload []byte) error {
	if h.StreamID == 0 {
		return connErr(frame.ProtocolError, "RST_STREAM on stream 0")
	}
	if h.Length != 4 {
		return connErr(frame.FrameSizeError, "RST_STREAM length %d != 4", h.Length)
	}
	s, ok := c.streams[h.StreamID]
	if !ok {
		return connErr(frame.ProtocolError, "RST_STREAM on idle stream %d", h.StreamID)
	}
	code := frame.ErrCode(binary.BigEndian.Uint32(payload))
	s.State = StateClosed
	s.closedByRST = true
	s.pending = nil
	c.log("in", "RST_STREAM", s.ID, "code="+code.String(), "state -> closed")
	return nil
}

func (c *Conn) handleSettings(h frame.Header, payload []byte) error {
	if h.StreamID != 0 {
		return connErr(frame.ProtocolError, "SETTINGS on stream %d", h.StreamID)
	}
	if h.Flags&frame.FlagAck != 0 {
		if h.Length != 0 {
			return connErr(frame.FrameSizeError, "SETTINGS ACK with length %d", h.Length)
		}
		return nil
	}
	if h.Length%6 != 0 {
		return connErr(frame.FrameSizeError, "SETTINGS length %d not multiple of 6", h.Length)
	}
	var delta int64
	var hasInitial bool
	for i := 0; i+6 <= len(payload); i += 6 {
		id := frame.SettingsID(binary.BigEndian.Uint16(payload[i : i+2]))
		v := binary.BigEndian.Uint32(payload[i+2 : i+6])
		switch id {
		case frame.SettingsInitialWindowSize:
			if v > 0x7fffffff {
				return connErr(frame.FlowControlError, "INITIAL_WINDOW_SIZE %d > 2^31-1", v)
			}
			delta = int64(v) - c.peerInitialWindow
			hasInitial = true
		case frame.SettingsMaxFrameSize:
			if v < frame.DefaultMaxFrameSize || v > frame.MaxAllowedFrameSize {
				return connErr(frame.ProtocolError, "MAX_FRAME_SIZE %d out of range", v)
			}
			c.peerMaxFrameSize = v
		case frame.SettingsEnablePush:
			if v > 1 {
				return connErr(frame.ProtocolError, "ENABLE_PUSH %d invalid", v)
			}
		}
	}
	if hasInitial {
		old := c.peerInitialWindow
		c.peerInitialWindow += delta
		// The delta applies to every stream's send window and may drive it
		// negative (RFC 7540 §6.9.2); sending DATA then stalls until the
		// window recovers via WINDOW_UPDATE.
		for _, s := range c.streams {
			if s.State == StateOpen || s.State == StateHalfClosedRemote ||
				s.State == StateHalfClosedLocal {
				s.sendWindow += delta
				c.log("in", "SETTINGS", s.ID,
					fmt.Sprintf("initial_window %d -> %d, stream send window now %d", old, c.peerInitialWindow, s.sendWindow),
					"window adjusted by SETTINGS delta")
			}
		}
	}
	ack := frame.Header{Length: 0, Type: frame.Settings, Flags: frame.FlagAck, StreamID: 0}
	if err := c.enqueueControl(ack, nil); err != nil {
		return connErr(frame.InternalError, "send queue full for SETTINGS ACK")
	}
	return nil
}

func (c *Conn) handlePing(h frame.Header, payload []byte) error {
	if h.StreamID != 0 {
		return connErr(frame.ProtocolError, "PING on stream %d", h.StreamID)
	}
	if h.Length != 8 {
		return connErr(frame.FrameSizeError, "PING length %d != 8", h.Length)
	}
	if h.Flags&frame.FlagAck != 0 {
		return nil
	}
	ack := frame.Header{Length: 8, Type: frame.Ping, Flags: frame.FlagAck, StreamID: 0}
	if err := c.enqueueControl(ack, append([]byte(nil), payload...)); err != nil {
		return connErr(frame.InternalError, "send queue full for PING ACK")
	}
	return nil
}

func (c *Conn) handleGoAway(h frame.Header, payload []byte) error {
	if h.StreamID != 0 {
		return connErr(frame.ProtocolError, "GOAWAY on stream %d", h.StreamID)
	}
	if h.Length < 8 {
		return connErr(frame.FrameSizeError, "GOAWAY length %d < 8", h.Length)
	}
	last := binary.BigEndian.Uint32(payload[0:4]) & 0x7fffffff
	code := frame.ErrCode(binary.BigEndian.Uint32(payload[4:8]))
	c.log("in", "GOAWAY", 0, fmt.Sprintf("last=%d code=%s", last, code), "peer is closing")
	return nil
}

func (c *Conn) handleWindowUpdate(h frame.Header, payload []byte) error {
	if h.Length != 4 {
		return connErr(frame.FrameSizeError, "WINDOW_UPDATE length %d != 4", h.Length)
	}
	inc := int64(binary.BigEndian.Uint32(payload) & 0x7fffffff)
	if inc == 0 {
		if h.StreamID == 0 {
			return connErr(frame.ProtocolError, "WINDOW_UPDATE increment 0 on connection")
		}
		c.streamReset(streamErr(h.StreamID, frame.ProtocolError, "WINDOW_UPDATE increment 0"))
		return nil
	}
	if h.StreamID == 0 {
		c.sendWindow += inc
		if c.sendWindow > 0x7fffffff {
			return connErr(frame.FlowControlError, "connection send window overflow")
		}
		c.log("in", "WINDOW_UPDATE", 0, fmt.Sprintf("conn send window now %d", c.sendWindow), "")
		c.flushAllLocked()
		return nil
	}
	s, ok := c.streams[h.StreamID]
	if !ok {
		// WINDOW_UPDATE on an idle stream is a connection error; on a closed
		// stream it may arrive late and is ignored (RFC 7540 §5.1).
		if h.StreamID > c.highestClientStream {
			return connErr(frame.ProtocolError, "WINDOW_UPDATE on idle stream %d", h.StreamID)
		}
		return nil
	}
	if s.State == StateClosed {
		return nil // late WINDOW_UPDATE after RST_STREAM: ignore
	}
	s.sendWindow += inc
	if s.sendWindow > 0x7fffffff {
		c.streamReset(streamErr(s.ID, frame.FlowControlError, "stream send window overflow"))
		return nil
	}
	c.log("in", "WINDOW_UPDATE", s.ID, fmt.Sprintf("stream send window now %d", s.sendWindow), "")
	c.flushStreamLocked(s)
	return nil
}

// SendHeaders queues a response HEADERS frame. The header block must fit in
// one frame (documented simplification: responses are small and predefined).
func (c *Conn) SendHeaders(streamID uint32, fields []hpack.HeaderField, endStream bool) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return ErrClosed
	}
	s, ok := c.streams[streamID]
	if !ok {
		return fmt.Errorf("h2: SendHeaders on unknown stream %d", streamID)
	}
	if !s.canSendData() {
		return fmt.Errorf("h2: SendHeaders on %s stream %d", s.State, streamID)
	}
	var block []byte
	for _, f := range fields {
		if idx := hpack.FindStatic(f.Name, f.Value); idx > 0 {
			block = append(block, hpack.EncodeIndexed(uint64(idx))...)
		} else {
			block = append(block, hpack.EncodeLiteralWithoutIndexing(f.Name, f.Value)...)
		}
	}
	if uint32(len(block)) > c.peerMaxFrameSize {
		return fmt.Errorf("h2: header block %d bytes exceeds peer max frame %d", len(block), c.peerMaxFrameSize)
	}
	flags := uint8(frame.FlagEndHeaders)
	if endStream {
		flags |= frame.FlagEndStream
	}
	h := frame.Header{Length: uint32(len(block)), Type: frame.Headers, Flags: flags, StreamID: streamID}
	if err := c.enqueueControl(h, block); err != nil {
		return err
	}
	if endStream {
		c.sendHalfCloseLocked(s)
	}
	return nil
}

// SendBody buffers body bytes for a stream and flushes what flow control and
// queue capacity allow. The rest stays pending and is flushed by FlushAll or
// when windows open. Returns a *StreamError if the pending bound is exceeded.
func (c *Conn) SendBody(streamID uint32, body []byte, endStream bool) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return ErrClosed
	}
	s, ok := c.streams[streamID]
	if !ok {
		return fmt.Errorf("h2: SendBody on unknown stream %d", streamID)
	}
	if !s.canSendData() {
		return fmt.Errorf("h2: SendBody on %s stream %d", s.State, streamID)
	}
	if len(s.pending)+len(body) > c.cfg.MaxPendingBodyBytes {
		se := streamErr(streamID, frame.EnhanceYourCalm,
			"pending body %d exceeds bound %d", len(s.pending)+len(body), c.cfg.MaxPendingBodyBytes)
		c.streamReset(se)
		return se
	}
	s.pending = append(s.pending, body...)
	if endStream {
		s.endStreamOnDrain = true
	}
	c.flushStreamLocked(s)
	return nil
}

// flushStreamLocked moves pending bytes into the outbound queue while both
// the connection and stream send windows and queue capacity allow. A
// non-positive window (e.g. after a SETTINGS window reduction) stalls DATA
// entirely until WINDOW_UPDATE restores it.
func (c *Conn) flushStreamLocked(s *Stream) {
	for len(s.pending) > 0 && s.canSendData() {
		if c.sendWindow <= 0 || s.sendWindow <= 0 {
			c.log("out", "DATA", s.ID,
				fmt.Sprintf("conn_window=%d stream_window=%d pending=%d", c.sendWindow, s.sendWindow, len(s.pending)),
				"DATA stalled: send window not positive")
			return
		}
		n := int64(len(s.pending))
		if n > c.sendWindow {
			n = c.sendWindow
		}
		if n > s.sendWindow {
			n = s.sendWindow
		}
		if n > int64(c.peerMaxFrameSize) {
			n = int64(c.peerMaxFrameSize)
		}
		payload := s.pending[:n]
		h := frame.Header{Length: uint32(n), Type: frame.Data, StreamID: s.ID}
		if err := c.enqueueData(h, payload); err != nil {
			c.log("out", "DATA", s.ID, fmt.Sprintf("pending=%d", len(s.pending)),
				"DATA stalled: send queue full")
			return
		}
		s.pending = s.pending[n:]
		c.sendWindow -= n
		s.sendWindow -= n
	}
	if len(s.pending) == 0 && s.endStreamOnDrain && s.canSendData() {
		h := frame.Header{Length: 0, Type: frame.Data, Flags: frame.FlagEndStream, StreamID: s.ID}
		if err := c.enqueueData(h, nil); err != nil {
			return // retried on next FlushAll
		}
		s.endStreamOnDrain = false
		c.sendHalfCloseLocked(s)
	}
}

// sendHalfCloseLocked applies the local END_STREAM state transition.
func (c *Conn) sendHalfCloseLocked(s *Stream) {
	switch s.State {
	case StateOpen:
		s.State = StateHalfClosedLocal
	case StateHalfClosedRemote:
		s.State = StateClosed
	}
	c.log("out", "END_STREAM", s.ID, "", "state -> "+s.State.String())
}

// FlushAll retries pending DATA after outbound queue space freed up. Called
// by the transport writer after each successful write.
func (c *Conn) FlushAll() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return
	}
	c.flushAllLocked()
}

func (c *Conn) flushAllLocked() {
	for _, s := range c.streams {
		c.flushStreamLocked(s)
	}
}

// InitiateGoAway sends GOAWAY with NO_ERROR and the current highest stream
// as lastStreamID. New streams above that boundary are refused afterwards.
func (c *Conn) InitiateGoAway(code frame.ErrCode, debug string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return ErrClosed
	}
	if c.goAwaySent {
		return nil
	}
	c.goAwaySent = true
	c.goAwayLastStream = c.highestClientStream
	p := make([]byte, 8+len(debug))
	binary.BigEndian.PutUint32(p[0:4], c.goAwayLastStream)
	binary.BigEndian.PutUint32(p[4:8], uint32(code))
	copy(p[8:], debug)
	h := frame.Header{Length: uint32(len(p)), Type: frame.GoAway, StreamID: 0}
	c.log("out", "GOAWAY", 0, fmt.Sprintf("last=%d code=%s %s", c.goAwayLastStream, code, debug), "graceful shutdown")
	return c.enqueueControl(h, p)
}

// Close marks the connection closed and closes the outbound queue.
func (c *Conn) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return
	}
	c.closed = true
	close(c.outq)
}

// --- Test/diagnostic introspection ---

// StreamStateOf returns a stream's state and whether it exists.
func (c *Conn) StreamStateOf(id uint32) (StreamState, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	s, ok := c.streams[id]
	if !ok {
		return StateIdle, false
	}
	return s.State, true
}

// StreamSendWindow returns a stream's current send window (may be negative).
func (c *Conn) StreamSendWindow(id uint32) (int64, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	s, ok := c.streams[id]
	if !ok {
		return 0, false
	}
	return s.sendWindow, true
}

// ConnSendWindow returns the connection-level send window.
func (c *Conn) ConnSendWindow() int64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.sendWindow
}

// PendingBytes returns a stream's buffered unsent body bytes.
func (c *Conn) PendingBytes(id uint32) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	if s, ok := c.streams[id]; ok {
		return len(s.pending)
	}
	return 0
}

// IsClosed reports whether the connection state machine is closed.
func (c *Conn) IsClosed() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.closed
}
