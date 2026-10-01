package policy

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/binary"
	"errors"
	"fmt"
	"time"
)

// PBKDF2 parameters. SHA-256, 600k iterations follows current OWASP
// guidance; the iteration count is stored per user so it can be raised.
const (
	pbkdf2Algo       = "PBKDF2-HMAC-SHA256"
	pbkdf2Iterations = 600_000
	pbkdf2SaltLen    = 16
	pbkdf2KeyLen     = 32
)

// SetUser creates or replaces a user's password verifier.
func (s *Store) SetUser(ctx context.Context, username, password string) error {
	if username == "" || len(username) > 255 {
		return errors.New("policy: username must be 1..255 bytes")
	}
	if len(password) == 0 || len(password) > 255 {
		return errors.New("policy: password must be 1..255 bytes")
	}
	salt := make([]byte, pbkdf2SaltLen)
	if _, err := rand.Read(salt); err != nil {
		return fmt.Errorf("policy: salt: %w", err)
	}
	hash := pbkdf2SHA256([]byte(password), salt, pbkdf2Iterations, pbkdf2KeyLen)
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO users(username, salt, hash, iterations, created_at, disabled)
		 VALUES(?,?,?,?,?,0)
		 ON CONFLICT(username) DO UPDATE SET salt=excluded.salt, hash=excluded.hash,
		   iterations=excluded.iterations, disabled=0`,
		username, salt, hash, pbkdf2Iterations, time.Now().UTC().Format(time.RFC3339Nano))
	if err != nil {
		return fmt.Errorf("policy: store user: %w", err)
	}
	return nil
}

// Authenticate implements proto.Authenticator. A disabled or unknown user,
// or a wrong password, returns (false, nil): a checked rejection distinct
// from a backend error (false, err).
func (s *Store) Authenticate(ctx context.Context, username, password string) (bool, error) {
	var salt, hash []byte
	var iterations int
	var disabled int
	err := s.db.QueryRowContext(ctx,
		`SELECT salt, hash, iterations, disabled FROM users WHERE username=?`, username).
		Scan(&salt, &hash, &iterations, &disabled)
	if errors.Is(err, sql.ErrNoRows) {
		// Spend a derivation anyway so unknown-user timing matches a known one.
		dummy := make([]byte, pbkdf2KeyLen)
		_ = pbkdf2SHA256([]byte(password), dummy, pbkdf2Iterations, pbkdf2KeyLen)
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("policy: query user: %w", err)
	}
	if disabled != 0 {
		return false, nil
	}
	got := pbkdf2SHA256([]byte(password), salt, iterations, len(hash))
	return hmac.Equal(got, hash), nil
}

var errUserNotFound = errors.New("user not found")

// DisableUser blocks further logins without deleting the verifier.
func (s *Store) DisableUser(ctx context.Context, username string) error {
	res, err := s.db.ExecContext(ctx, `UPDATE users SET disabled=1 WHERE username=?`, username)
	if err != nil {
		return fmt.Errorf("policy: disable user: %w", err)
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return fmt.Errorf("policy: user %q: %w", username, errUserNotFound)
	}
	return nil
}

// pbkdf2SHA256 derives a key per RFC 2898 / RFC 8018 using only the standard
// library (crypto/hmac + crypto/sha256). Kept local so the binary has no
// dependency on a specific x/crypto version.
func pbkdf2SHA256(password, salt []byte, iter, keyLen int) []byte {
	prf := hmac.New(sha256.New, password)
	hashLen := prf.Size()
	numBlocks := (keyLen + hashLen - 1) / hashLen

	var buf [4]byte
	dk := make([]byte, 0, numBlocks*hashLen)
	U := make([]byte, hashLen)
	for block := 1; block <= numBlocks; block++ {
		prf.Reset()
		prf.Write(salt)
		binary.BigEndian.PutUint32(buf[:], uint32(block))
		prf.Write(buf[:])
		T := prf.Sum(nil)
		copy(U, T)
		for n := 2; n <= iter; n++ {
			prf.Reset()
			prf.Write(U)
			U = prf.Sum(U[:0])
			for x := range T {
				T[x] ^= U[x]
			}
		}
		dk = append(dk, T...)
	}
	return dk[:keyLen]
}
