package mimeparse

import (
	"bytes"
	"fmt"
	"strconv"
	"strings"
)

// SectionSpec names a BODY[..] section. Empty means the whole message.
type SectionSpec struct {
	// Path is the MIME part path, e.g. [1,2] for BODY[1.2]; empty = message.
	Path []int
	// Kind selects the sub-section of the resolved entity.
	Kind SectionKind
	// Fields are header field names for HEADER.FIELDS / .NOT.
	Fields []string
}

// SectionKind enumerates supported BODY section sub-selectors.
type SectionKind int

const (
	SecWhole SectionKind = iota
	SecHeader
	SecHeaderFields
	SecHeaderFieldsNot
	SecText
	SecMIME
)

// Partial is an optional <offset.count> byte range applied to a section.
type Partial struct {
	Offset int
	Count  int
}

// String renders "<offset.count>".
func (p Partial) String() string {
	return strconv.Itoa(p.Offset) + "." + strconv.Itoa(p.Count)
}

// ParseSection parses the inside of BODY[ ... ] and any trailing <o.c>.
// text is the section text (e.g. "HEADER.FIELDS (SUBJECT DATE)"), partials
// are the angled bytes if present.
func ParseSection(text string, partial []byte) (SectionSpec, *Partial, error) {
	var spec SectionSpec
	s := strings.TrimSpace(text)
	// "BINARY.PEEK" etc. never reach here; PEEK is stripped by the caller.
	if s != "" {
		// Leading dotted numbers are the part path, e.g. "1.2.MIME".
		path, rest, err := consumePath(s)
		if err != nil {
			return spec, nil, err
		}
		spec.Path = path
		s = strings.TrimPrefix(rest, ".")
		switch {
		case s == "":
			if len(path) == 0 {
				spec.Kind = SecWhole
			} else {
				spec.Kind = SecWhole // a part itself
			}
		case strings.EqualFold(s, "HEADER"):
			spec.Kind = SecHeader
		case strings.EqualFold(s, "TEXT"):
			spec.Kind = SecText
		case strings.EqualFold(s, "MIME"):
			spec.Kind = SecMIME
		case len(s) >= len("HEADER.FIELDS") &&
			strings.EqualFold(s[:len("HEADER.FIELDS")], "HEADER.FIELDS"):
			tail := strings.TrimSpace(s[len("HEADER.FIELDS"):])
			neg := false
			if strings.HasPrefix(tail, ".NOT") {
				neg, tail = true, strings.TrimSpace(tail[len(".NOT"):])
			}
			fields, err := parseFieldList(tail)
			if err != nil {
				return spec, nil, err
			}
			spec.Fields = fields
			if neg {
				spec.Kind = SecHeaderFieldsNot
			} else {
				spec.Kind = SecHeaderFields
			}
		default:
			return spec, nil, errf("unknown BODY section %q", text)
		}
	}
	p, err := parsePartial(partial)
	if err != nil {
		return spec, nil, err
	}
	return spec, p, nil
}

func consumePath(s string) ([]int, string, error) {
	var path []int
	i := 0
	for i < len(s) && s[i] >= '0' && s[i] <= '9' {
		j := i
		for j < len(s) && s[j] >= '0' && s[j] <= '9' {
			j++
		}
		n, err := strconv.Atoi(s[i:j])
		if err != nil || n <= 0 {
			return nil, "", errf("bad part number")
		}
		path = append(path, n)
		i = j
		if i < len(s) && s[i] == '.' {
			// A dot may continue the path ("1.2") or start a keyword
			// ("1.HEADER"); peek the next byte.
			if i+1 < len(s) && s[i+1] >= '0' && s[i+1] <= '9' {
				i++
				continue
			}
			break
		}
		break
	}
	return path, s[i:], nil
}

func parseFieldList(s string) ([]string, error) {
	s = strings.TrimSpace(s)
	if len(s) < 2 || s[0] != '(' || s[len(s)-1] != ')' {
		return nil, errf("HEADER.FIELDS requires a parenthesized list")
	}
	var fields []string
	for _, f := range strings.Fields(s[1 : len(s)-1]) {
		fields = append(fields, f)
	}
	if len(fields) == 0 {
		return nil, errf("HEADER.FIELDS list is empty")
	}
	return fields, nil
}

func parsePartial(b []byte) (*Partial, error) {
	if len(b) == 0 {
		return nil, nil
	}
	s := strings.TrimSpace(string(b))
	s = strings.TrimPrefix(s, "<")
	s = strings.TrimSuffix(s, ">")
	dot := strings.IndexByte(s, '.')
	if dot < 0 {
		return nil, errf("malformed partial %q", string(b))
	}
	o, err1 := strconv.Atoi(s[:dot])
	c, err2 := strconv.Atoi(s[dot+1:])
	if err1 != nil || err2 != nil || o < 0 || c < 0 {
		return nil, errf("malformed partial %q", string(b))
	}
	return &Partial{Offset: o, Count: c}, nil
}

// Extract renders the requested section's raw bytes. Sections are raw
// wire bytes with content-transfer-encoding intact (RFC 3501 BODY[]).
func (e *Entity) Extract(spec SectionSpec, partial *Partial) ([]byte, error) {
	target := e
	if len(spec.Path) > 0 {
		cur := e
		for _, n := range spec.Path {
			if !cur.IsMultipart() || n > len(cur.Children) {
				return nil, nil // nonexistent part: FETCH omits the item
			}
			cur = cur.Children[n-1]
		}
		target = cur
	}
	var out []byte
	switch spec.Kind {
	case SecWhole:
		out = target.raw
	case SecHeader:
		out = target.headerBlock()
	case SecText:
		out = target.body
	case SecMIME:
		if len(spec.Path) == 0 {
			return nil, errf("MIME section requires a part path")
		}
		out = target.headerBlock()
	case SecHeaderFields, SecHeaderFieldsNot:
		out = selectHeaders(target.headerBlock(), spec.Fields, spec.Kind == SecHeaderFieldsNot)
	}
	if partial != nil {
		out = applyPartial(out, partial.Offset, partial.Count)
	}
	return out, nil
}

func (e *Entity) headerBlock() []byte {
	return e.raw[:e.headerLF]
}

func applyPartial(b []byte, offset, count int) []byte {
	if offset >= len(b) {
		return nil
	}
	end := offset + count
	if end > len(b) {
		end = len(b)
	}
	return b[offset:end]
}

// selectHeaders returns the unfolded? no — RAW header lines (kept folded)
// matching (or excluding) the named fields, terminated by CRLF, preserving
// original order. Comparison is case-insensitive.
func selectHeaders(headerBlock []byte, fields []string, exclude bool) []byte {
	want := map[string]bool{}
	for _, f := range fields {
		want[strings.ToLower(f)] = true
	}
	lines := splitRawHeaderLines(headerBlock)
	var out bytes.Buffer
	for _, line := range lines {
		name := rawHeaderName(line)
		match := want[strings.ToLower(name)]
		if match != exclude {
			out.Write(line)
			if !bytes.HasSuffix(line, []byte("\n")) {
				out.WriteString("\r\n")
			}
		}
	}
	return out.Bytes()
}

// splitRawHeaderLines keeps folded continuations attached to their field.
func splitRawHeaderLines(block []byte) [][]byte {
	var lines [][]byte
	for _, l := range bytes.Split(block, []byte("\n")) {
		if len(l) == 0 {
			continue
		}
		l = append(l, '\n')
		if len(lines) > 0 && (l[0] == ' ' || l[0] == '\t') {
			lines[len(lines)-1] = append(lines[len(lines)-1], l...)
			continue
		}
		lines = append(lines, l)
	}
	return lines
}

func rawHeaderName(line []byte) string {
	i := bytes.IndexByte(line, ':')
	if i < 0 {
		return ""
	}
	return strings.TrimSpace(string(line[:i]))
}

type parseErr struct{ msg string }

func (e parseErr) Error() string { return ParseError.Error() + ": " + e.msg }
func (e parseErr) Unwrap() error { return ParseError }

func errf(format string, args ...any) error {
	return parseErr{msg: fmt.Sprintf(format, args...)}
}
