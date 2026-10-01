// Package protocol contains the SMTP command state machine. It is deliberately
// transport agnostic: it consumes decoded text lines and produces structured
// replies, so it can be exercised without any network I/O.
package protocol

import (
	"context"
	"errors"
	"time"
)

// Phase identifies where the session is in the RFC 5321 command sequence.
type Phase int

const (
	// PhaseInit is after connect, before EHLO/HELO.
	PhaseInit Phase = iota
	// PhaseReady means greeted with no mail transaction in progress.
	PhaseReady
	// PhaseMail means MAIL FROM has been accepted.
	PhaseMail
	// PhaseRcpt means at least one RCPT TO has been accepted.
	PhaseRcpt
	// PhaseData means DATA was accepted and message content is being collected.
	PhaseData
)

// String renders the phase for diagnostics.
func (p Phase) String() string {
	switch p {
	case PhaseInit:
		return "init"
	case PhaseReady:
		return "ready"
	case PhaseMail:
		return "mail"
	case PhaseRcpt:
		return "rcpt"
	case PhaseData:
		return "data"
	default:
		return "unknown"
	}
}

// Limits caps resource usage for one connection / one message.
type Limits struct {
	CommandLineBytes int `json:"command_line_bytes"`
	DataLineBytes    int `json:"data_line_bytes"`
	MessageBytes     int `json:"message_bytes"`
	Recipients       int `json:"recipients_per_message"`
	MessagesPerConn  int `json:"messages_per_connection"`
	CommandsPerConn  int `json:"commands_per_connection"`
}

// Sink is the persistence boundary. Deliver must be atomic: either copies for
// every listed recipient are durable before it returns nil, or no copy is
// observable and an error is returned.
type Sink interface {
	Deliver(ctx context.Context, msg Message) (Receipt, error)
}

// Message is one fully received, decoded mail transaction.
type Message struct {
	ID         string
	From       string
	Recipients []string
	// Data is the canonical RFC 5322 message body with CRLF line endings and
	// DATA dot-transparency already removed.
	Data       []byte
	ReceivedAt time.Time
}

// Receipt reports what was persisted.
type Receipt struct {
	// MessageID is the durable identifier of the stored message.
	MessageID string
	// DeliveredTo lists the mailboxes for which a copy exists.
	DeliveredTo []string
}

// FailureKind categorizes a delivery failure so the state machine can choose
// between a permanent (5xx) and temporary (4xx) reply.
type FailureKind int

const (
	// KindTemporary maps to 451: the receiver did not accept the message and
	// the client may retry the whole transaction later.
	KindTemporary FailureKind = iota
	// KindInsufficient maps to 452: storage quota / space exhausted.
	KindInsufficient
)

// DeliveryError is the only error type the state machine maps to protocol
// replies; any other sink error is treated as an indeterminate temporary
// failure and its details are never leaked to the peer.
type DeliveryError struct {
	Kind FailureKind
	Code string // enhanced-status style machine-readable reason, e.g. "4.3.0"
	Why  string // short diagnostic reason for logs
	Err  error  // wrapped cause
}

func (e *DeliveryError) Error() string {
	if e.Err != nil {
		return "smtp delivery: " + e.Why + ": " + e.Err.Error()
	}
	return "smtp delivery: " + e.Why
}

func (e *DeliveryError) Unwrap() error { return e.Err }

// Wrap returns a copy of the categorized error carrying cause as its wrapped
// error, so sinks can annotate a driver failure without mutating the shared
// sentinel values.
func (e *DeliveryError) Wrap(cause error) *DeliveryError {
	return &DeliveryError{Kind: e.Kind, Code: e.Code, Why: e.Why, Err: cause}
}

// Pre-categorized failures sinks can return directly.
var (
	// ErrTemporary means storage could not durably persist the message.
	ErrTemporary = &DeliveryError{Kind: KindTemporary, Code: "4.3.0", Why: "temporary-storage-failure"}
	// ErrInsufficient means the store reported space/quota exhaustion.
	ErrInsufficient = &DeliveryError{Kind: KindInsufficient, Code: "4.3.1", Why: "insufficient-storage"}
)

// AsDeliveryError classifies an arbitrary sink error.
func AsDeliveryError(err error) *DeliveryError {
	var de *DeliveryError
	if errors.As(err, &de) {
		return de
	}
	return &DeliveryError{Kind: KindTemporary, Code: "4.3.2", Why: "unclassified-sink-error", Err: err}
}
