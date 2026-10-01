// Package storage defines the durable message store used by the SMTP
// protocol layer. The contract that matters for correctness: Commit must
// not report success before the message is durable on disk, and Abort
// must leave no trace behind.
package storage

import "io"

// Envelope is the SMTP transaction envelope: the reverse path and the
// set of accepted recipients (the final delivery scope).
type Envelope struct {
	MailFrom string
	RcptTo   []string
}

// Pending is one in-flight message being streamed to disk.
type Pending interface {
	io.Writer
	// Commit durably persists the message and returns its assigned ID.
	// A nil error means the message is on disk and indexed.
	Commit() (id string, err error)
	// Abort discards the message and removes any temporary artifacts.
	Abort() error
}

// Store accepts new messages for local delivery.
type Store interface {
	Begin(env Envelope) (Pending, error)
}
