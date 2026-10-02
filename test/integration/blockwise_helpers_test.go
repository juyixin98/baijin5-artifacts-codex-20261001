package integration_test

import (
	"sync/atomic"

	"coaplab/internal/wire"
	"coaplab/test/oracle"
)

// wireResult pairs the independent oracle view with the SUT-parsed message.
type wireResult struct {
	view oracle.MessageView
	msg  *wire.Message
}

func bytesEqual(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// oracleAddChanged feeds block 0 (etag e1) then block 1 (etag e2) into a
// FRESH independent oracle reassembler; it must refuse to splice.
func oracleAddChanged(b0, b1 wireResult) error {
	r := oracle.NewReferenceReassembler()
	if err := r.Add(b0.view.BlockOpt.NUM, b0.view.BlockOpt.M, b0.view.BlockOpt.SZX,
		b0.view.Payload, b0.view.ETag); err != nil {
		return err
	}
	return r.Add(b1.view.BlockOpt.NUM, b1.view.BlockOpt.M, b1.view.BlockOpt.SZX,
		b1.view.Payload, b1.view.ETag)
}

// block2Num extracts a request Block2 control number (proxy hook only).
func block2Num(m *wire.Message) (uint32, bool) {
	b, has, err := m.Block2()
	if err != nil || !has {
		return 0, false
	}
	return b.NUM, true
}

func atomicCAS(flag *int32) bool { return atomic.CompareAndSwapInt32(flag, 0, 1) }

// repeatASCII builds n copies of c (deterministic test representation).
func repeatASCII(c byte, n int) []byte {
	b := make([]byte, n)
	for i := range b {
		b[i] = c
	}
	return b
}
