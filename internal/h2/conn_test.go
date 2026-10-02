package h2

import (
	"encoding/binary"
	"testing"

	"h2svc/internal/frame"
	"h2svc/internal/hpack"
)

// autoHandler responds to every complete request with a fixed-size body.
type autoHandler struct {
	bodyBytes int
}

func (h *autoHandler) OnHeaders(c *Conn, s *Stream) {
	_ = c.SendHeaders(s.ID, nil, false)
	_ = c.SendBody(s.ID, make([]byte, h.bodyBytes), true)
}

func (h *autoHandler) OnData(c *Conn, s *Stream, data []byte, endStream bool) {}

func feed(t *testing.T, c *Conn, h frame.Header, payload []byte) error {
	t.Helper()
	return c.HandleFrame(h, payload)
}

func settingsPayload(t *testing.T, initialWindow uint32) []byte {
	t.Helper()
	p := make([]byte, 6)
	binary.BigEndian.PutUint16(p[0:2], uint16(frame.SettingsInitialWindowSize))
	binary.BigEndian.PutUint32(p[2:6], initialWindow)
	return p
}

func headersPayload(t *testing.T) []byte {
	t.Helper()
	var block []byte
	block = append(block, hpack.EncodeIndexed(2)...) // :method: GET
	block = append(block, hpack.EncodeLiteralWithoutIndexing(":path", "/")...)
	return block
}

func feedHeaders(t *testing.T, c *Conn, streamID uint32, endStream bool) {
	t.Helper()
	block := headersPayload(t)
	flags := uint8(frame.FlagEndHeaders)
	if endStream {
		flags |= frame.FlagEndStream
	}
	h := frame.Header{Length: uint32(len(block)), Type: frame.Headers, Flags: flags, StreamID: streamID}
	if err := c.HandleFrame(h, block); err != nil {
		t.Fatalf("headers stream %d: %v", streamID, err)
	}
}

func windowUpdatePayload(inc uint32) []byte {
	p := make([]byte, 4)
	binary.BigEndian.PutUint32(p, inc)
	return p
}

func drainN(c *Conn, n int) []OutFrame {
	var out []OutFrame
	for i := 0; i < n; i++ {
		select {
		case f := <-c.Outbound():
			out = append(out, f)
		default:
			return out
		}
	}
	return out
}

// TestSendWindowGoesNegativeAndStalls asserts the RFC 7540 §6.9.2 behavior:
// a SETTINGS initial-window reduction may drive a stream's send window
// negative, and no DATA may be sent until WINDOW_UPDATE makes it positive.
func TestSendWindowGoesNegativeAndStalls(t *testing.T) {
	c := NewConn(1, Config{SendQueueCapacity: 16}, nil, nil)
	defer c.Close()

	// Peer lowers its initial window to 100 before the stream exists.
	if err := feed(t, c, frame.Header{Length: 6, Type: frame.Settings}, settingsPayload(t, 100)); err != nil {
		t.Fatalf("settings: %v", err)
	}
	drainN(c, 1) // SETTINGS ACK

	feedHeaders(t, c, 1, true)

	// Shrink again: 100 -> 50, delta -50 on the open stream.
	if err := feed(t, c, frame.Header{Length: 6, Type: frame.Settings}, settingsPayload(t, 50)); err != nil {
		t.Fatalf("settings 2: %v", err)
	}
	drainN(c, 1)
	if w, _ := c.StreamSendWindow(1); w != 50 {
		t.Fatalf("stream window: want 50, got %d", w)
	}

	// Buffer 200 body bytes; only 50 fit the window.
	if err := c.SendBody(1, make([]byte, 200), true); err != nil {
		t.Fatalf("SendBody: %v", err)
	}
	if got := drainN(c, 16); len(got) != 1 || got[0].Header.Length != 50 {
		t.Fatalf("expected 1 DATA frame of 50 bytes, got %v", got)
	}
	if w, _ := c.StreamSendWindow(1); w != 0 {
		t.Fatalf("stream window after send: want 0, got %d", w)
	}
	if p := c.PendingBytes(1); p != 150 {
		t.Fatalf("pending: want 150, got %d", p)
	}

	// Drive the window negative with another reduction: 50 -> 10, delta -40.
	if err := feed(t, c, frame.Header{Length: 6, Type: frame.Settings}, settingsPayload(t, 10)); err != nil {
		t.Fatalf("settings 3: %v", err)
	}
	drainN(c, 1)
	if w, _ := c.StreamSendWindow(1); w != -40 {
		t.Fatalf("stream window after shrink: want -40, got %d", w)
	}
	if got := len(drainN(c, 16)); got != 0 {
		t.Fatalf("no DATA may be sent with a negative window, got %d frames", got)
	}

	// WINDOW_UPDATE +40 only reaches zero: still no DATA.
	if err := feed(t, c, frame.Header{Length: 4, Type: frame.WindowUpdate, StreamID: 1}, windowUpdatePayload(40)); err != nil {
		t.Fatalf("window_update: %v", err)
	}
	if w, _ := c.StreamSendWindow(1); w != 0 {
		t.Fatalf("stream window: want 0, got %d", w)
	}
	if got := len(drainN(c, 16)); got != 0 {
		t.Fatalf("no DATA may be sent with a zero window, got %d frames", got)
	}

	// A larger update releases the rest: DATA(150) + empty END_STREAM.
	if err := feed(t, c, frame.Header{Length: 4, Type: frame.WindowUpdate, StreamID: 1}, windowUpdatePayload(1000)); err != nil {
		t.Fatalf("window_update 2: %v", err)
	}
	got := drainN(c, 16)
	if len(got) != 2 || got[0].Header.Length != 150 ||
		got[1].Header.Flags&frame.FlagEndStream == 0 {
		t.Fatalf("expected DATA(150)+END_STREAM after window recovery, got %v", got)
	}
	if p := c.PendingBytes(1); p != 0 {
		t.Fatalf("pending after recovery: want 0, got %d", p)
	}
}

// TestSendQueueBounded proves the outbound queue bound: DATA stalls when the
// queue is full, remaining bytes stay pending, and FlushAll resumes them.
func TestSendQueueBounded(t *testing.T) {
	const cap = 4
	c := NewConn(1, Config{SendQueueCapacity: cap}, nil, nil)
	defer c.Close()

	feedHeaders(t, c, 1, true)
	if err := c.SendHeaders(1, nil, false); err != nil {
		t.Fatalf("SendHeaders: %v", err)
	}
	drainN(c, 1) // response HEADERS

	// Raise the connection and stream windows so only the queue bounds sending.
	if err := feed(t, c, frame.Header{Length: 4, Type: frame.WindowUpdate, StreamID: 0}, windowUpdatePayload(200000)); err != nil {
		t.Fatalf("window_update conn: %v", err)
	}
	if err := feed(t, c, frame.Header{Length: 4, Type: frame.WindowUpdate, StreamID: 1}, windowUpdatePayload(200000)); err != nil {
		t.Fatalf("window_update stream: %v", err)
	}

	// Queue capacity 4, frame size 16384: 4 DATA frames fill the queue.
	body := make([]byte, 4*16384+100)
	if err := c.SendBody(1, body, true); err != nil {
		t.Fatalf("SendBody: %v", err)
	}
	if got := c.QueueLen(); got != cap {
		t.Fatalf("queue occupancy: want %d, got %d", cap, got)
	}
	if p := c.PendingBytes(1); p != 100 {
		t.Fatalf("pending with full queue: want 100, got %d", p)
	}

	// Freeing two slots and flushing moves the remaining DATA plus the
	// trailing END_STREAM frame (2 leftover + 2 new = full again).
	drainN(c, 2)
	c.FlushAll()
	if got := c.QueueLen(); got != cap {
		t.Fatalf("queue after flush: want %d, got %d", cap, got)
	}
	got := drainN(c, cap)
	if len(got) != 4 || got[2].Header.Length != 100 ||
		got[3].Header.Flags&frame.FlagEndStream == 0 {
		t.Fatalf("expected DATA,DATA,DATA(100),END_STREAM; got %v", got)
	}
	if p := c.PendingBytes(1); p != 0 {
		t.Fatalf("pending at end: want 0, got %d", p)
	}
}

// TestPendingBoundExceeded covers the per-stream pending-body bound.
func TestPendingBoundExceeded(t *testing.T) {
	c := NewConn(1, Config{SendQueueCapacity: 1, MaxPendingBodyBytes: 100}, nil, nil)
	defer c.Close()

	feedHeaders(t, c, 1, true)

	err := c.SendBody(1, make([]byte, 500), true)
	se, ok := err.(*StreamError)
	if !ok {
		t.Fatalf("want *StreamError, got %v", err)
	}
	if se.Code != frame.EnhanceYourCalm {
		t.Fatalf("want ENHANCE_YOUR_CALM, got %s", se.Code)
	}
	// The stream was reset; the connection stays usable.
	if st, _ := c.StreamStateOf(1); st != StateClosed {
		t.Fatalf("stream state after reset: want closed, got %s", st)
	}
	if c.IsClosed() {
		t.Fatal("stream-level bound violation must not close the connection")
	}
}

// TestRSTOnIdleStreamIsConnError: RST_STREAM for a never-opened stream is a
// connection-level PROTOCOL_ERROR (RFC 7540 §5.1).
func TestRSTOnIdleStreamIsConnError(t *testing.T) {
	c := NewConn(1, Config{}, nil, nil)
	defer c.Close()
	err := feed(t, c, frame.Header{Length: 4, Type: frame.RSTStream, StreamID: 5}, make([]byte, 4))
	ce, ok := err.(*ConnError)
	if !ok {
		t.Fatalf("want *ConnError, got %v", err)
	}
	if ce.Code != frame.ProtocolError {
		t.Fatalf("want PROTOCOL_ERROR, got %s", ce.Code)
	}
	if !c.IsClosed() {
		t.Fatal("connection must be closed after a connection-level error")
	}
}

// TestWindowUpdateZeroIncrement covers RFC 7540 §6.9: increment 0 on a
// stream is a stream-level PROTOCOL_ERROR; on the connection it is a
// connection-level one.
func TestWindowUpdateZeroIncrement(t *testing.T) {
	c := NewConn(1, Config{}, &autoHandler{bodyBytes: 0}, nil)
	defer c.Close()
	feedHeaders(t, c, 1, false)
	drainN(c, 2) // response HEADERS + empty END_STREAM DATA

	// Stream-level: RST_STREAM(PROTOCOL_ERROR), connection survives.
	if err := feed(t, c, frame.Header{Length: 4, Type: frame.WindowUpdate, StreamID: 1}, windowUpdatePayload(0)); err != nil {
		t.Fatalf("stream window_update: %v", err)
	}
	got := drainN(c, 1)
	if len(got) != 1 || got[0].Header.Type != frame.RSTStream {
		t.Fatalf("want RST_STREAM, got %v", got)
	}
	if c.IsClosed() {
		t.Fatal("stream-level error must not close the connection")
	}

	// Connection-level: fatal.
	err := feed(t, c, frame.Header{Length: 4, Type: frame.WindowUpdate, StreamID: 0}, windowUpdatePayload(0))
	ce, ok := err.(*ConnError)
	if !ok || ce.Code != frame.ProtocolError {
		t.Fatalf("want connection PROTOCOL_ERROR, got %v", err)
	}
}

// TestPaddedDataAndHeaders: padding is validated and stripped (RFC 7540 §6.1).
func TestPaddedDataAndHeaders(t *testing.T) {
	c := NewConn(1, Config{}, nil, nil)
	defer c.Close()

	// Padded HEADERS: pad length 3, block, 3 pad bytes.
	block := headersPayload(t)
	payload := append([]byte{3}, block...)
	payload = append(payload, 0, 0, 0)
	h := frame.Header{Length: uint32(len(payload)), Type: frame.Headers,
		Flags: frame.FlagEndHeaders | frame.FlagPadded, StreamID: 1}
	if err := feed(t, c, h, payload); err != nil {
		t.Fatalf("padded headers: %v", err)
	}
	if st, _ := c.StreamStateOf(1); st != StateOpen {
		t.Fatalf("stream state: want open, got %s", st)
	}

	// Pad length consuming the whole payload is a connection PROTOCOL_ERROR.
	bad := []byte{5, 0, 0, 0, 0}
	err := feed(t, c, frame.Header{Length: 5, Type: frame.Data, Flags: frame.FlagPadded, StreamID: 1}, bad)
	ce, ok := err.(*ConnError)
	if !ok || ce.Code != frame.ProtocolError {
		t.Fatalf("want connection PROTOCOL_ERROR, got %v", err)
	}
}

// TestPriorityAccepted: PRIORITY frames are well-formed-checked and ignored.
func TestPriorityAccepted(t *testing.T) {
	c := NewConn(1, Config{}, nil, nil)
	defer c.Close()
	if err := feed(t, c, frame.Header{Length: 5, Type: frame.Priority, StreamID: 1}, make([]byte, 5)); err != nil {
		t.Fatalf("priority: %v", err)
	}
	// Wrong length on an existing stream is a stream-level FRAME_SIZE_ERROR.
	feedHeaders(t, c, 1, false)
	if err := feed(t, c, frame.Header{Length: 4, Type: frame.Priority, StreamID: 1}, make([]byte, 4)); err != nil {
		t.Fatalf("bad priority: %v", err)
	}
	got := drainN(c, 1)
	if len(got) != 1 || got[0].Header.Type != frame.RSTStream {
		t.Fatalf("want RST_STREAM, got %v", got)
	}
	// PRIORITY on stream 0 is a connection error.
	err := feed(t, c, frame.Header{Length: 5, Type: frame.Priority, StreamID: 0}, make([]byte, 5))
	if ce, ok := err.(*ConnError); !ok || ce.Code != frame.ProtocolError {
		t.Fatalf("want connection PROTOCOL_ERROR, got %v", err)
	}
}

// TestGoAwayReceived: an inbound GOAWAY is logged and tolerated.
func TestGoAwayReceived(t *testing.T) {
	c := NewConn(1, Config{}, nil, nil)
	defer c.Close()
	p := make([]byte, 8)
	binary.BigEndian.PutUint32(p[4:8], uint32(frame.NoError))
	if err := feed(t, c, frame.Header{Length: 8, Type: frame.GoAway}, p); err != nil {
		t.Fatalf("goaway: %v", err)
	}
	if c.IsClosed() {
		t.Fatal("inbound GOAWAY must not close the state machine")
	}
	// Truncated GOAWAY is a connection FRAME_SIZE_ERROR.
	err := feed(t, c, frame.Header{Length: 7, Type: frame.GoAway}, make([]byte, 7))
	if ce, ok := err.(*ConnError); !ok || ce.Code != frame.FrameSizeError {
		t.Fatalf("want connection FRAME_SIZE_ERROR, got %v", err)
	}
}
