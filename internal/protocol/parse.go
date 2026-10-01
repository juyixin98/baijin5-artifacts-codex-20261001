package protocol

import (
	"strings"
)

// ParseReversePath parses the argument of MAIL FROM: "<reverse-path>" plus
// optional ESMTP parameters (RFC 5321 3.3 / RFC 1870 SIZE / RFC 6152).
// The empty reverse-path "<>" (a bounce) is returned as "".
func ParseReversePath(arg string) (string, map[string]string, error) {
	addr, rest, err := extractAddress(arg)
	if err != nil {
		return "", nil, err
	}
	params, err := parseParams(rest, allowedMailParams)
	if err != nil {
		return "", nil, err
	}
	if addr != "" && !ValidMailbox(addr) {
		return "", nil, errInvalidAddress
	}
	return addr, params, nil
}

// ParseForwardPath parses the argument of RCPT TO.
func ParseForwardPath(arg string) (string, map[string]string, error) {
	addr, rest, err := extractAddress(arg)
	if err != nil {
		return "", nil, err
	}
	if addr == "" {
		return "", nil, errInvalidAddress
	}
	params, err := parseParams(rest, allowedRcptParams)
	if err != nil {
		return "", nil, err
	}
	if !ValidMailbox(addr) {
		return "", nil, errInvalidAddress
	}
	return addr, params, nil
}

// extractAddress pulls the path out of either "<addr> params" or a bare
// "addr params" form. The returned rest is the unparsed parameter tail.
func extractAddress(arg string) (addr, rest string, err error) {
	arg = strings.TrimSpace(arg)
	if arg == "" {
		return "", "", errMissingAddress
	}
	if arg[0] == '<' {
		close := strings.IndexByte(arg, '>')
		if close < 0 {
			return "", "", errUnterminatedPath
		}
		return strings.ToLower(arg[1:close]), strings.TrimSpace(arg[close+1:]), nil
	}
	// Bare form: the address runs up to the first whitespace, if any.
	if sp := strings.IndexAny(arg, " \t"); sp >= 0 {
		return strings.ToLower(strings.TrimSpace(arg[:sp])), strings.TrimSpace(arg[sp:]), nil
	}
	return strings.ToLower(arg), "", nil
}

var (
	allowedMailParams = map[string]bool{
		"AUTH": true, "BODY": true, "SIZE": true, "SMTPUTF8": true,
	}
	allowedRcptParams = map[string]bool{
		"ORCPT": true, "NOTIFY": true,
	}
)

func parseParams(rest string, allowed map[string]bool) (map[string]string, error) {
	if rest == "" {
		return nil, nil
	}
	params := make(map[string]string)
	for _, tok := range strings.Fields(rest) {
		key, val, hasVal := strings.Cut(tok, "=")
		key = strings.ToUpper(key)
		if !allowed[key] {
			return nil, errUnknownParameter
		}
		if hasVal {
			params[key] = val
		} else {
			params[key] = ""
		}
	}
	return params, nil
}

// ValidMailbox performs a conservative local-part@domain check. It is a
// syntax gate, not full RFC 5321 grammar; local fixtures stay well inside it.
func ValidMailbox(addr string) bool {
	at := strings.LastIndexByte(addr, '@')
	if at <= 0 || at == len(addr)-1 {
		return false
	}
	local, domain := addr[:at], addr[at+1:]
	return validLocalPart(local) && validDomain(domain)
}

func validLocalPart(s string) bool {
	if len(s) == 0 || len(s) > 64 {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
		case strings.IndexByte("!#$%&'*+-/=?^_`{|}~.", c) >= 0:
		default:
			return false
		}
	}
	// No dots at the edges and no consecutive dots.
	if s[0] == '.' || s[len(s)-1] == '.' || strings.Contains(s, "..") {
		return false
	}
	return true
}

func validDomain(s string) bool {
	if len(s) == 0 || len(s) > 253 {
		return false
	}
	labels := strings.Split(s, ".")
	for _, label := range labels {
		if len(label) == 0 || len(label) > 63 {
			return false
		}
		for i := 0; i < len(label); i++ {
			c := label[i]
			switch {
			case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9', c == '-':
			default:
				return false
			}
		}
		if label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
	}
	return true
}
