package h2

import (
	"fmt"

	"h2svc/internal/hpack"
)

// StreamState is the RFC 7540 §5.1 stream lifecycle state.
type StreamState int

const (
	StateIdle StreamState = iota
	StateOpen
	StateHalfClosedRemote
	StateHalfClosedLocal
	StateClosed
)

func (s StreamState) String() string {
	switch s {
	case StateIdle:
		return "idle"
	case StateOpen:
		return "open"
	case StateHalfClosedRemote:
		return "half-closed(remote)"
	case StateHalfClosedLocal:
		return "half-closed(local)"
	case StateClosed:
		return "closed"
	default:
		return fmt.Sprintf("unknown(%d)", int(s))
	}
}

// Stream tracks one stream's state, flow-control windows and pending output.
type Stream struct {
	ID    uint32
	State StreamState

	// sendWindow is how many flow-controlled bytes we may still send on this
	// stream. It may go negative when the peer lowers SETTINGS_INITIAL_WINDOW_SIZE.
	sendWindow int64
	// recvWindow is how many flow-controlled bytes the peer may still send us.
	recvWindow int64

	Headers []hpack.HeaderField

	// pending is response body bytes buffered while the send window or the
	// outbound queue does not permit sending. Bounded by Config.MaxPendingBodyBytes.
	pending      []byte
	endStreamOnDrain bool

	closedByRST bool
}

func (s *Stream) canSendData() bool {
	return s.State == StateOpen || s.State == StateHalfClosedRemote
}

func (s *Stream) canReceiveData() bool {
	return s.State == StateOpen || s.State == StateHalfClosedLocal
}
