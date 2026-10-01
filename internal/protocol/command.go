// Package protocol implements the SMTP session state machine: greeting,
// EHLO/HELO, MAIL, RCPT, DATA, RSET, NOOP, VRFY, QUIT — with explicit
// per-transaction state and RFC 5321 reply codes.
package protocol

import (
	"errors"
	"strings"
)

// errSyntax marks a malformed command argument (reply 501).
var errSyntax = errors.New("protocol: bad syntax")

// parseCommand splits a command line into an upper-cased verb and its
// argument. A line with no verb yields an empty verb.
func parseCommand(line []byte) (verb, arg string) {
	s := strings.TrimRight(string(line), " ")
	if i := strings.IndexByte(s, ' '); i >= 0 {
		return strings.ToUpper(s[:i]), strings.TrimSpace(s[i+1:])
	}
	return strings.ToUpper(s), ""
}

// parsePath extracts the address from a MAIL/RCPT argument of the form
// "FROM:<addr>" or "TO:<addr>", ignoring trailing ESMTP parameters.
// An empty path ("<>") is returned as "".
func parsePath(arg, prefix string) (string, error) {
	if len(arg) < len(prefix) || !strings.EqualFold(arg[:len(prefix)], prefix) {
		return "", errSyntax
	}
	rest := strings.TrimSpace(arg[len(prefix):])
	if !strings.HasPrefix(rest, "<") {
		return "", errSyntax
	}
	end := strings.IndexByte(rest, '>')
	if end < 0 {
		return "", errSyntax
	}
	return rest[1:end], nil
}

// splitAddress validates and splits addr into local part and lower-cased
// domain. Empty addr (null reverse-path) is rejected here; callers decide
// whether to permit it.
func splitAddress(addr string) (local, domain string, err error) {
	i := strings.LastIndexByte(addr, '@')
	if i <= 0 || i == len(addr)-1 {
		return "", "", errSyntax
	}
	return addr[:i], strings.ToLower(addr[i+1:]), nil
}

// MaskAddress redacts the local part of an address for log output, e.g.
// "alice@example.test" becomes "a***@example.test".
func MaskAddress(a string) string {
	i := strings.LastIndexByte(a, '@')
	if i <= 0 {
		return "***"
	}
	return a[:1] + "***@" + a[i+1:]
}
