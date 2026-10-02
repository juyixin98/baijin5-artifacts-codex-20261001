package server

import (
	"context"
	"errors"
	"fmt"

	"hpacklab.local/hpack"
	"hpacklab.local/service/frame"
	"hpacklab.local/service/store"
)

// completeHeaderBlock decodes one assembled block for hb.streamID. It
// returns replied=true when an HTTP response was sent (END_STREAM was on the
// HEADERS frame). On HPACK failure it sends GOAWAY with COMPRESSION_ERROR
// and returns a terminating error: the decoder is poisoned and the HTTP/2
// connection state is no longer assumed synchronized.
func (c *conn) completeHeaderBlock(ctx context.Context, hb *hbState) (replied bool, err error) {
	requestID := newID("req")
	c.obs.beginBlock(requestID, hb.streamID)

	fields, derr := c.decoder.DecodeBlock(hb.block)
	if derr != nil {
		return false, c.failCompression(requestID, hb, derr)
	}

	emitted := 0
	for _, f := range fields {
		emitted += len(f.Name) + len(f.Value)
	}

	c.log.Info("decoded header block",
		"request_id", requestID,
		"stream_id", hb.streamID,
		"block_bytes", len(hb.block),
		"field_count", len(fields),
		"emitted_bytes", emitted,
		"status", "ok")

	if err := c.persist(ctx, store.Result{
		ID:           requestID,
		ConnID:       c.connID,
		StreamID:     hb.streamID,
		BlockBytes:   len(hb.block),
		EmittedBytes: emitted,
		Fields:       toStoreFields(fields),
	}); err != nil {
		c.log.Error("persist failed", "request_id", requestID, "error", err.Error())
	}

	if hb.endStream {
		if err := c.respond(ctx, hb.streamID); err != nil {
			return false, err
		}
		return true, nil
	}
	return false, nil
}

// failCompression records the failure, emits a GOAWAY(COMPRESSION_ERROR) and
// returns a terminating error. After this the connection MUST end: RFC 7541
// state can no longer be trusted to be synchronized.
func (c *conn) failCompression(requestID string, hb *hbState, derr error) error {
	var he *hpack.Error
	kind, detail := "unknown", derr.Error()
	if errors.As(derr, &he) {
		kind = he.Kind.String()
		detail = he.Detail
	}
	c.log.Error("header block decode failed",
		"request_id", requestID,
		"stream_id", hb.streamID,
		"block_bytes", len(hb.block),
		"error_kind", kind,
		"error_detail", detail,
		"status", "error",
		"fatal", "connection-closing; hpack state desynchronized")

	if err := c.persist(context.Background(), store.Result{
		ID:         requestID,
		ConnID:     c.connID,
		StreamID:   hb.streamID,
		BlockBytes: len(hb.block),
		Failed:     true,
		Kind:       kind,
		Detail:     detail,
	}); err != nil {
		c.log.Error("persist failure record failed", "request_id", requestID, "error", err.Error())
	}
	// COMPRESSION_ERROR (0x9): endpoint received a header block that it
	// could not decompress. GOAWAY closes the whole connection.
	return c.fatal(errCodeCompression, fmt.Sprintf("hpack decode failed (%s)", kind))
}

// respond sends a small fixed 204 response whose header block is encoded
// with this connection's encoder, proving end-to-end round-tripping.
func (c *conn) respond(ctx context.Context, streamID uint32) error {
	_ = ctx
	respFields := []hpack.HeaderField{
		{Name: ":status", Value: "204"},
		{Name: "server", Value: "hpackd"},
	}
	block := c.encoder.EncodeBlock(respFields)
	// Single HEADERS frame in this controlled server (blocks are tiny).
	if uint32(len(block)) > c.peerMaxFrameSize {
		return fmt.Errorf("encoded response block %d exceeds peer max frame size %d", len(block), c.peerMaxFrameSize)
	}
	if err := c.fw.Headers(streamID, frame.FlagEndHeaders|frame.FlagEndStream, block); err != nil {
		return err
	}
	return c.flush()
}

func toStoreFields(fs []hpack.HeaderField) []store.Field {
	out := make([]store.Field, 0, len(fs))
	for _, f := range fs {
		out = append(out, store.Field{Name: f.Name, Value: f.Value, Sensitive: f.Sensitive})
	}
	return out
}

// persist records one result when storage is configured; storage errors are
// logged by callers and never take the connection down.
func (c *conn) persist(ctx context.Context, r store.Result) error {
	if c.srv.store == nil {
		return nil
	}
	return c.srv.store.RecordResult(ctx, r)
}
