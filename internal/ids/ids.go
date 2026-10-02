// Package ids generates and distinguishes the two independent CoAP
// correlation identifiers (RFC 7252 §5.3.1, §4.4):
//
//   - Message ID (MID): message-layer identity, used to dedupe CON
//     retransmissions. It is scoped to a single endpoint pair + transport.
//   - Token: request/response correlation at the application layer; one
//     block-wise transfer (RFC 7959) intentionally reuses one Token while
//     every block request still carries a fresh MID.
//
// The two MUST NOT be interchanged; this package gives them distinct types.
package ids

import (
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"sync"
)

// Token is an opaque 0..8 byte request correlation value.
type Token []byte

// Key renders a token as a stable map key (zero-length token is "∅").
func (t Token) Key() string { return "t:" + hex.EncodeToString(t) }
func (t Token) Hex() string {
	if len(t) == 0 {
		return "∅"
	}
	return hex.EncodeToString(t)
}

// TokenSource mints unpredictable tokens with crypto/rand so that parallel
// block-wise transfers from one host cannot collide (RFC 7252 §5.3.1:
// clients SHOULD generate tokens in a way that tokens currently in use are
// unique).
type TokenSource struct {
	mu     sync.Mutex
	length int
}

// NewTokenSource returns a source minting tokens of length bytes (1..8).
func NewTokenSource(length int) (*TokenSource, error) {
	if length < 1 || length > 8 {
		return nil, fmt.Errorf("ids: token length %d outside 1..8", length)
	}
	return &TokenSource{length: length}, nil
}

// New mints a token, retrying on the (practically impossible) rand failure.
func (s *TokenSource) New() Token {
	s.mu.Lock()
	n := s.length
	s.mu.Unlock()
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		// crypto/rand failure is unrecoverable on a healthy host; fail loud
		// rather than minting a predictable token.
		panic(fmt.Errorf("ids: cannot read crypto/rand: %w", err))
	}
	return Token(b)
}

// MID is a 16-bit message-layer identifier.
type MID uint16

// MIDSource hands out per-socket message IDs, wrapping after 65535.
// Callers MUST NOT reuse a MID to the same endpoint within
// EXCHANGE_LIFETIME; a monotonically wrapping counter is the RFC 7252
// §4.4 recommended strategy and is sufficient for this local subset.
type MIDSource struct {
	mu   sync.Mutex
	next uint16
}

// NewMIDSource starts at a randomised offset so parallel test processes do
// not emit identical runs (the wire semantics do not depend on this).
func NewMIDSource() *MIDSource {
	var seed [2]byte
	_, _ = rand.Read(seed[:])
	return &MIDSource{next: binary.BigEndian.Uint16(seed[:])}
}

// Next returns the following MID.
func (m *MIDSource) Next() MID {
	m.mu.Lock()
	v := m.next
	m.next++
	m.mu.Unlock()
	return MID(v)
}

// EndpointMID pairs a remote endpoint string with its MID: the same MID
// value used against two different remotes is not a duplicate.
type EndpointMID struct {
	Remote string
	MID    MID
}
