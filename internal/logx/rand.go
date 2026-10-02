package logx

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
)

func readRand(b []byte) (int, error) { return rand.Read(b) }

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
