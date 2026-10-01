package compat_test

import (
	"time"

	"ntpsim/internal/protocol"
)

// encodeClient builds the 48 wire bytes of a v4 client request.
func encodeClient(t1 time.Time) []byte {
	return protocol.NewClientRequest(t1).Encode()
}
