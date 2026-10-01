package auth

import "errors"

// Configuration-time errors for the credential authenticator.
var (
	ErrEmptyPassword   = errors.New("auth: password must not be empty")
	ErrUsernameTooLong = errors.New("auth: username exceeds 255 bytes")
	ErrPasswordTooLong = errors.New("auth: password exceeds 255 bytes")
)
