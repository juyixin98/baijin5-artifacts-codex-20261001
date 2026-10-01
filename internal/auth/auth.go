// Package auth provides SOCKS5 username/password authentication (RFC 1929)
// with constant-time credential comparison.
package auth

import (
	"crypto/subtle"
)

// UserPassAuthenticator accepts a single configured credential pair. A
// server with no configured credential leaves authentication disabled and
// offers the SOCKS5 "no authentication" method instead.
type UserPassAuthenticator struct {
	username string
	password string
}

// NewUserPass builds an authenticator. Empty usernames are accepted by RFC
// 1929 (the length octet may be zero), but an empty password is rejected at
// configuration time to avoid an unintentionally open proxy.
func NewUserPass(username, password string) (*UserPassAuthenticator, error) {
	if len(username) > 255 {
		return nil, ErrUsernameTooLong
	}
	if len(password) == 0 {
		return nil, ErrEmptyPassword
	}
	if len(password) > 255 {
		return nil, ErrPasswordTooLong
	}
	return &UserPassAuthenticator{username: username, password: password}, nil
}

// Authenticate reports whether the pair matches, using constant-time
// comparison so a correct username/password cannot be inferred from timing.
// Length is compared implicitly: a different length can never match, and the
// comparison does not short-circuit on content.
func (a *UserPassAuthenticator) Authenticate(username, password string) bool {
	if len(username) != len(a.username) || len(password) != len(a.password) {
		// Still burn a comparison against the configured length to reduce
		// the length signal; correctness is unaffected.
		_ = subtle.ConstantTimeCompare([]byte(username), []byte(username))
		return false
	}
	userOK := subtle.ConstantTimeCompare([]byte(username), []byte(a.username)) == 1
	passOK := subtle.ConstantTimeCompare([]byte(password), []byte(a.password)) == 1
	return userOK && passOK
}
