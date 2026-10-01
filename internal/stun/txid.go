package stun

import (
	"crypto/rand"

	"localstun/internal/stunerror"
)

// NewTransactionID returns a cryptographically random 96-bit transaction id
// (RFC 5389 §6: MUST be globally unique; random is the recommended method).
func NewTransactionID() (TransactionID, error) {
	var id TransactionID
	if _, err := rand.Read(id[:]); err != nil {
		return TransactionID{}, stunerror.Wrap(stunerror.KindCompute, "txid", "system random source failed", err)
	}
	return id, nil
}

// MustTransactionID panics on random-source failure; intended for fixtures and
// tests where failure is unrecoverable.
func MustTransactionID() TransactionID {
	id, err := NewTransactionID()
	if err != nil {
		panic(err)
	}
	return id
}
