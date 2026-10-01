package protocol

import "strings"

// RedactAddress masks the local part of an email address for logs while
// keeping enough structure to correlate deliveries: "alice@example" →
// "a***@example". The empty bounce path renders as "<>".
func RedactAddress(addr string) string {
	if addr == "" {
		return "<>"
	}
	local, domain, ok := strings.Cut(addr, "@")
	if !ok {
		return mask(local)
	}
	return mask(local) + "@" + domain
}

// RedactToken masks a free-form identifier (EHLO argument, HELO name).
func RedactToken(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	if len(s) <= 2 {
		return strings.Repeat("*", len(s))
	}
	return string(s[0]) + strings.Repeat("*", len(s)-1)
}

func mask(local string) string {
	switch {
	case len(local) == 0:
		return "?"
	case len(local) == 1:
		return string(local[0])
	case len(local) == 2:
		return string(local[0]) + "*"
	default:
		return string(local[0]) + strings.Repeat("*", len(local)-2) + string(local[len(local)-1])
	}
}
