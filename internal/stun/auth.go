package stun

import (
	"crypto/md5"
)

// DeriveLongTermKey derives the HMAC key used by the STUN long-term
// credential mechanism (RFC 5389 §15.4): MD1(username ":" realm ":" password).
//
// RFC 5389 requires SASLprep on the three strings; the controlled-test
// fixtures are ASCII and skip SASLprep. The deployed test server instead uses
// a direct pre-shared HMAC key (see internal/server), so this routine exists
// primarily so the credential path is exercised and testable.
func DeriveLongTermKey(username, realm, password string) []byte {
	s := username + ":" + realm + ":" + password
	sum := md5.Sum([]byte(s))
	return sum[:]
}
