// Package mimeparse is the constrained RFC 5322/MIME reader behind FETCH
// BODY[]/BODYSTRUCTURE/ENVELOPE. It parses just enough to locate MIME parts
// and render the declared data items; BODY sections are returned as *raw*
// wire bytes (content-transfer-encoding is NOT removed), per RFC 3501.
package mimeparse

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"io"
	"strings"
)

// ParseError marks malformed message bytes. It carries the PARSE response
// code at the protocol boundary.
var ParseError = errors.New("mime parse error")

// parseErrorf builds an error wrapping ParseError.
func parseErrorf(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ParseError, fmt.Sprintf(format, args...))
}

// HeaderField is one unfolded raw header line (Value still RFC 2047-encoded).
type HeaderField struct {
	Name  string
	Value string
}

// Entity is one message or MIME part.
type Entity struct {
	Header   []HeaderField
	raw      []byte // this entity's own raw bytes (header + body)
	headerLF int    // length of raw header block including terminating blank line
	body     []byte // raw body bytes (still content-transfer-encoded)

	Type        string            // lowercased media type ("text")
	Subtype     string            // lowercased ("plain")
	Params      map[string]string // content-type parameters (boundary etc.)
	ID          string
	Desc        string
	Encoding    string
	Disposition string // lowercased disposition type or ""
	DispParams  map[string]string
	Lines       int       // body line count for text/*
	Children    []*Entity // multipart children in order
}

// IsMultipart reports whether the entity is multipart/*.
func (e *Entity) IsMultipart() bool { return len(e.Children) > 0 }

// Size returns the raw byte count of this entity.
func (e *Entity) Size() int { return len(e.raw) }

// Parse parses a full RFC 5322 message into an entity tree.
func Parse(raw []byte) (*Entity, error) {
	return parseEntity(raw)
}

func parseEntity(raw []byte) (*Entity, error) {
	headerBlock, body, split, err := splitHeaderBody(raw)
	if err != nil {
		return nil, err
	}
	// NUL is forbidden in RFC 5322 header fields; its presence makes the
	// message unparseable. Bodies may still be binary, hence the header-only
	// check (reported as the PARSE response code at the protocol boundary).
	if bytes.IndexByte(headerBlock, 0x00) >= 0 {
		return nil, parseErrorf("NUL byte in message header")
	}
	fields, err := parseHeaderFields(headerBlock)
	if err != nil {
		return nil, err
	}
	e := &Entity{
		Header:     fields,
		raw:        raw,
		headerLF:   split,
		body:       body,
		Params:     map[string]string{},
		DispParams: map[string]string{},
		Type:       "text",
		Subtype:    "plain",
	}
	if ct := headerGet(fields, "Content-Type"); ct != "" {
		if t, st, params, ok := parseContentType(ct); ok {
			e.Type, e.Subtype, e.Params = t, st, params
		}
	}
	e.ID = decode2047(headerGet(fields, "Content-ID"))
	e.Desc = decode2047(headerGet(fields, "Content-Description"))
	e.Encoding = strings.ToLower(strings.TrimSpace(firstParam(headerGet(fields, "Content-Transfer-Encoding"))))
	if disp := headerGet(fields, "Content-Disposition"); disp != "" {
		e.Disposition, e.DispParams = parseDisposition(disp)
	}
	if e.Type == "text" {
		e.Lines = countLines(body)
	}
	if e.Type == "multipart" {
		boundary := e.Params["boundary"]
		e.Children = parseMultipart(body, boundary)
	}
	return e, nil
}

// splitHeaderBody returns header block, body and the byte offset of body.
func splitHeaderBody(raw []byte) (header, body []byte, splitAt int, err error) {
	if i := bytes.Index(raw, []byte("\r\n\r\n")); i >= 0 {
		return raw[:i], raw[i+4:], i + 4, nil
	}
	if i := bytes.Index(raw, []byte("\n\n")); i >= 0 {
		return raw[:i], raw[i+2:], i + 2, nil
	}
	// Header-only message: treat everything as headers, empty body.
	return raw, nil, len(raw), nil
}

func parseHeaderFields(block []byte) ([]HeaderField, error) {
	var fields []HeaderField
	sc := bufio.NewScanner(bytes.NewReader(block))
	sc.Buffer(make([]byte, 0, 64*1024), 64*1024*1024)
	sc.Split(bufio.ScanLines)
	var pending string
	flush := func(line string) {
		if line == "" {
			return
		}
		colon := strings.IndexByte(line, ':')
		if colon <= 0 {
			// Obsolete/malformed line: keep going, surface as PARSE only
			// when truly unusable; headers without ':' are ignored here.
			return
		}
		fields = append(fields, HeaderField{
			Name:  strings.TrimSpace(line[:colon]),
			Value: strings.TrimSpace(line[colon+1:]),
		})
	}
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t") {
			if len(fields) > 0 {
				fields[len(fields)-1].Value += " " + strings.TrimSpace(line)
			}
			continue
		}
		_ = pending
		flush(line)
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	return fields, nil
}

func headerGet(fields []HeaderField, name string) string {
	for _, f := range fields {
		if strings.EqualFold(f.Name, name) {
			return f.Value
		}
	}
	return ""
}

// firstParam returns the token before ';' (values like encodings have no params).
func firstParam(s string) string {
	if i := strings.IndexByte(s, ';'); i >= 0 {
		return s[:i]
	}
	return s
}

// parseContentType parses `type/subtype; k=v; k="v"`.
func parseContentType(s string) (typ, sub string, params map[string]string, ok bool) {
	parts := splitSemicolon(s)
	if len(parts) == 0 {
		return "", "", nil, false
	}
	mts := strings.SplitN(strings.TrimSpace(parts[0]), "/", 2)
	if len(mts) != 2 || mts[0] == "" || mts[1] == "" {
		return "", "", nil, false
	}
	params = map[string]string{}
	for _, p := range parts[1:] {
		if k, v, ok := parseParam(p); ok {
			params[k] = v
		}
	}
	return strings.ToLower(mts[0]), strings.ToLower(mts[1]), params, true
}

func parseDisposition(s string) (string, map[string]string) {
	parts := splitSemicolon(s)
	params := map[string]string{}
	for _, p := range parts[1:] {
		if k, v, ok := parseParam(p); ok {
			params[k] = v
		}
	}
	return strings.ToLower(strings.TrimSpace(parts[0])), params
}

func splitSemicolon(s string) []string {
	var out []string
	depth := 0
	inQuote := false
	start := 0
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case '"':
			inQuote = !inQuote
		case '(':
			if !inQuote {
				depth++
			}
		case ')':
			if !inQuote {
				depth--
			}
		case ';':
			if !inQuote && depth == 0 {
				out = append(out, s[start:i])
				start = i + 1
			}
		}
	}
	return append(out, s[start:])
}

func parseParam(p string) (string, string, bool) {
	eq := strings.IndexByte(p, '=')
	if eq <= 0 {
		return "", "", false
	}
	k := strings.ToLower(strings.TrimSpace(p[:eq]))
	v := strings.TrimSpace(p[eq+1:])
	v = strings.Trim(v, `"`)
	return k, decode2047(v), true
}

func countLines(b []byte) int {
	if len(b) == 0 {
		return 0
	}
	n := bytes.Count(b, []byte("\n"))
	if !bytes.HasSuffix(b, []byte("\n")) {
		n++
	}
	return n
}

// parseMultipart splits a multipart body into child entities.
func parseMultipart(body []byte, boundary string) []*Entity {
	if boundary == "" {
		return nil
	}
	delim := []byte("--" + boundary)
	// Normalise CRLF splitting; operate on the raw body.
	parts := bytes.Split(body, delim)
	if len(parts) < 3 {
		return nil
	}
	var children []*Entity
	for i, part := range parts[1:] {
		// Closing delimiter carries "--"; epilogue follows it.
		if bytes.HasPrefix(part, []byte("--")) {
			break
		}
		// Skip transport padding and the CRLF following the boundary.
		part = skipBoundaryEOL(part)
		// Drop trailing CRLF before next boundary.
		part = bytes.TrimSuffix(part, []byte("\r\n"))
		part = bytes.TrimSuffix(part, []byte("\n"))
		if len(bytes.TrimSpace(part)) == 0 {
			continue
		}
		child, err := parseEntity(part)
		if err != nil {
			continue
		}
		_ = i
		children = append(children, child)
	}
	return children
}

func skipBoundaryEOL(b []byte) []byte {
	if bytes.HasPrefix(b, []byte("\r\n")) {
		return b[2:]
	}
	if bytes.HasPrefix(b, []byte("\n")) {
		return b[1:]
	}
	return b
}

// ReadAll is a small helper retained for tests/fixture loaders.
func ReadAll(r io.Reader) ([]byte, error) { return io.ReadAll(r) }
