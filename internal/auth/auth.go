// Package auth implements local credential hashing with the Go standard
// library's crypto primitives only: PBKDF2-HMAC-SHA256 with a per-user random
// salt. There is no production account store; users are synthetic fixtures.
package auth

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"fmt"
)

// Parameters are fixed for the local service. They are deliberately modest
// (the threat model is local replay/compatibility testing, not internet login
// brute force) while still exercising real salted, iterated hashing.
const (
	SaltLen    = 16
	KeyLen     = 32
	Iterations = 120_000
)

// ErrCredential is returned when a username is unknown or a password mismatches.
// Callers must not tell the client which half failed.
var ErrCredential = errors.New("auth: invalid credentials")

// GenerateSalt returns SaltLen cryptographically random bytes.
func GenerateSalt() ([]byte, error) {
	b := make([]byte, SaltLen)
	if _, err := rand.Read(b); err != nil {
		return nil, fmt.Errorf("generate salt: %w", err)
	}
	return b, nil
}

// Key derives a key from password and salt with PBKDF2-HMAC-SHA256.
func Key(password, salt []byte, iter, keyLen int) []byte {
	prf := hmac.New(sha256.New, password)
	hashLen := prf.Size()
	numBlocks := (keyLen + hashLen - 1) / hashLen

	var buf [4]byte
	dk := make([]byte, 0, numBlocks*hashLen)
	for block := 1; block <= numBlocks; block++ {
		prf.Reset()
		prf.Write(salt)
		buf[0] = byte(block >> 24)
		buf[1] = byte(block >> 16)
		buf[2] = byte(block >> 8)
		buf[3] = byte(block)
		prf.Write(buf[:])
		u := prf.Sum(nil)
		t := make([]byte, len(u))
		copy(t, u)
		for n := 2; n <= iter; n++ {
			prf.Reset()
			prf.Write(u)
			u = prf.Sum(u[:0])
			for x := range t {
				t[x] ^= u[x]
			}
		}
		dk = append(dk, t...)
	}
	return dk[:keyLen]
}

// Hash is the service-wide derivation using the fixed parameters.
func Hash(password, salt []byte) []byte {
	return Key(password, salt, Iterations, KeyLen)
}

// Verify compares a derived key against the stored one in constant time.
func Verify(password, salt, want []byte) bool {
	got := Hash(password, salt)
	return subtle.ConstantTimeCompare(got, want) == 1
}
