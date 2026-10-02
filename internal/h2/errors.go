package h2

import (
	"fmt"

	"h2svc/internal/frame"
)

// ConnError is a connection-level error (RFC 7540 §5.4.1): the connection is
// terminated with GOAWAY carrying this code. It never maps to a single stream.
type ConnError struct {
	Code  frame.ErrCode
	Debug string
}

func (e *ConnError) Error() string {
	return fmt.Sprintf("connection error %s: %s", e.Code, e.Debug)
}

// StreamError is a stream-level error (RFC 7540 §5.4.2): the stream is reset
// with RST_STREAM carrying this code; the connection stays alive.
type StreamError struct {
	StreamID uint32
	Code     frame.ErrCode
	Debug    string
}

func (e *StreamError) Error() string {
	return fmt.Sprintf("stream %d error %s: %s", e.StreamID, e.Code, e.Debug)
}

func connErr(code frame.ErrCode, format string, args ...any) *ConnError {
	return &ConnError{Code: code, Debug: fmt.Sprintf(format, args...)}
}

func streamErr(id uint32, code frame.ErrCode, format string, args ...any) *StreamError {
	return &StreamError{StreamID: id, Code: code, Debug: fmt.Sprintf(format, args...)}
}
