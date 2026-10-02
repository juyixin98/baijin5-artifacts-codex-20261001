package mimeparse

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"strconv"
	"strings"
	"time"

	"imaplite/internal/imapwire"
)

// Address is a minimal parsed mailbox address.
type Address struct {
	Name    string
	Mailbox string
	Host    string
}

// Envelope is the FETCH ENVELOPE data item content.
type Envelope struct {
	Date      string
	Subject   string
	From      []Address
	Sender    []Address
	ReplyTo   []Address
	To        []Address
	Cc        []Address
	Bcc       []Address
	InReplyTo string
	MessageID string
}

// Envelope builds the envelope from the entity's headers.
func (e *Entity) Envelope() Envelope {
	from := parseAddressList(headerGet(e.Header, "From"))
	return Envelope{
		Date:      headerGet(e.Header, "Date"),
		Subject:   decode2047(headerGet(e.Header, "Subject")),
		From:      from,
		Sender:    firstNonEmpty(headerGet(e.Header, "Sender"), from),
		ReplyTo:   firstNonEmptyList(headerGet(e.Header, "Reply-To"), from),
		To:        parseAddressList(headerGet(e.Header, "To")),
		Cc:        parseAddressList(headerGet(e.Header, "Cc")),
		Bcc:       parseAddressList(headerGet(e.Header, "Bcc")),
		InReplyTo: headerGet(e.Header, "In-Reply-To"),
		MessageID: headerGet(e.Header, "Message-ID"),
	}
}

func firstNonEmpty(raw string, fallback []Address) []Address {
	if strings.TrimSpace(raw) == "" {
		return fallback
	}
	return parseAddressList(raw)
}

func firstNonEmptyList(raw string, fallback []Address) []Address {
	return firstNonEmpty(raw, fallback)
}

// Encode renders the envelope as the IMAP parenthesized response fragment.
func (v Envelope) Encode() []byte {
	return imapwire.EncodeList(
		imapwire.EncodeString(emptyToNil(v.Date)),
		imapwire.EncodeString(emptyToNil(v.Subject)),
		encodeAddresses(v.From),
		encodeAddresses(v.Sender),
		encodeAddresses(v.ReplyTo),
		encodeAddresses(v.To),
		encodeAddresses(v.Cc),
		encodeAddresses(v.Bcc),
		imapwire.EncodeString(emptyToNil(trimAngle(v.InReplyTo))),
		imapwire.EncodeString(emptyToNil(trimAngle(v.MessageID))),
	)
}

func emptyToNil(s string) []byte {
	if s == "" {
		return nil
	}
	return []byte(s)
}

func trimAngle(s string) string {
	s = strings.TrimSpace(s)
	s = strings.TrimPrefix(s, "<")
	s = strings.TrimSuffix(s, ">")
	if s == "" {
		return ""
	}
	return "<" + s + ">"
}

func encodeAddresses(addrs []Address) []byte {
	if len(addrs) == 0 {
		return imapwire.EncodeAtom("NIL")
	}
	parts := make([][]byte, 0, len(addrs))
	for _, a := range addrs {
		mb := emptyToNil(a.Mailbox)
		host := emptyToNil(a.Host)
		parts = append(parts, imapwire.EncodeList(
			imapwire.EncodeString(emptyToNil(a.Name)),
			imapwire.EncodeAtom("NIL"), // adl / route
			imapwire.EncodeString(mb),
			imapwire.EncodeString(host),
		))
	}
	return imapwire.EncodeList(parts...)
}

// parseAddressList is a deliberately small RFC 5322 mailbox-list parser: it
// understands comma-separated "Display Name <local@host>" and bare
// local@host, which covers the synthetic fixtures and ordinary mail.
func parseAddressList(s string) []Address {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	var out []Address
	for _, raw := range splitTopCommas(s) {
		raw = strings.TrimSpace(raw)
		raw = strings.Trim(raw, "()")
		if raw == "" {
			continue
		}
		if lt := strings.LastIndex(raw, "<"); lt >= 0 {
			gt := strings.Index(raw[lt:], ">")
			addr := raw
			if gt >= 0 {
				addr = raw[lt+1 : lt+gt]
			} else {
				addr = strings.TrimPrefix(raw[lt:], "<")
			}
			name := strings.TrimSpace(raw[:lt])
			name = strings.Trim(name, "\"")
			out = append(out, addrParts(decode2047(name), addr))
			continue
		}
		if strings.Contains(raw, "@") {
			out = append(out, addrParts("", raw))
		}
	}
	return out
}

func addrParts(name, addr string) Address {
	addr = strings.TrimSpace(addr)
	at := strings.LastIndex(addr, "@")
	if at < 0 {
		return Address{Name: name, Mailbox: addr}
	}
	return Address{Name: name, Mailbox: addr[:at], Host: addr[at+1:]}
}

// splitTopCommas splits on commas not nested inside quotes/angle brackets.
func splitTopCommas(s string) []string {
	var out []string
	depth := 0
	inQuote := false
	start := 0
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c == '"':
			inQuote = !inQuote
		case inQuote:
		case c == '<':
			depth++
		case c == '>':
			depth--
		case c == ',' && depth == 0:
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	return append(out, s[start:])
}

// ---- RFC 2047 encoded-word decoding (UTF-8, B and Q) ----

func decode2047(s string) string {
	if !strings.Contains(s, "=?") {
		return s
	}
	var out bytes.Buffer
	for i := 0; i < len(s); {
		idx := strings.Index(s[i:], "=?")
		if idx < 0 {
			out.WriteString(s[i:])
			break
		}
		out.WriteString(s[i : i+idx])
		i += idx
		end := strings.Index(s[i:], "?=")
		if end < 0 {
			out.WriteString(s[i:])
			break
		}
		token := s[i+2 : i+end]
		i += end + 2
		fields := strings.SplitN(token, "?", 3)
		if len(fields) != 3 || len(fields[2]) == 0 {
			out.WriteString("=?" + token + "?=")
			continue
		}
		enc := strings.ToUpper(fields[1])
		payload := fields[2]
		switch enc {
		case "B":
			if dec, err := base64.StdEncoding.DecodeString(payload); err == nil {
				out.Write(dec)
			} else {
				out.WriteString("=?" + token + "?=")
			}
		case "Q":
			out.WriteString(decodeQ(payload))
		default:
			out.WriteString("=?" + token + "?=")
		}
		// Collapse the whitespace that separated adjacent encoded words.
		if i < len(s) && (s[i] == ' ' || s[i] == '\t') {
			if next := strings.Index(s[i:], "=?"); next == 1 {
				i++
			}
		}
	}
	return out.String()
}

func decodeQ(s string) string {
	var out bytes.Buffer
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case '_':
			out.WriteByte(' ')
		case '=':
			if i+2 < len(s) {
				if v, err := strconv.ParseUint(s[i+1:i+3], 16, 8); err == nil {
					out.WriteByte(byte(v))
					i += 2
					continue
				}
			}
			out.WriteByte(s[i])
		default:
			out.WriteByte(s[i])
		}
	}
	return out.String()
}

// ---- BODYSTRUCTURE ----

// BodyStructure renders the BODYSTRUCTURE response fragment for the entity.
func (e *Entity) BodyStructure() []byte {
	return e.bodyStructure(true)
}

// Body renders the BODY response (same shape, no extension data).
func (e *Entity) Body() []byte { return e.bodyStructure(false) }

func (e *Entity) bodyStructure(ext bool) []byte {
	if e.IsMultipart() {
		parts := make([][]byte, 0, len(e.Children)+1)
		for _, c := range e.Children {
			parts = append(parts, c.bodyStructure(ext))
		}
		parts = append(parts, imapwire.EncodeString([]byte(e.Subtype)))
		if ext {
			// Extension: no extra parameters/ disposition beyond subtype for
			// the synthetic fixtures beyond content-type params of the parent.
			parts = append(parts, encodeParams(e.Params), imapwire.EncodeAtom("NIL"),
				imapwire.EncodeAtom("NIL"))
		}
		return imapwire.EncodeList(parts...)
	}
	parts := [][]byte{
		imapwire.EncodeString([]byte(e.Type)),
		imapwire.EncodeString([]byte(e.Subtype)),
		encodeParams(e.Params),
		imapwire.EncodeAtom("NIL"), // Content-ID
		imapwire.EncodeAtom("NIL"), // Content-Description
		encodingOr7bit(e.Encoding),
		imapwire.EncodeAtom(strconv.Itoa(len(e.body))),
	}
	if e.Type == "text" {
		parts = append(parts, imapwire.EncodeAtom(strconv.Itoa(e.Lines)))
	}
	if ext {
		parts = append(parts,
			encodeDisposition(e.Disposition, e.DispParams),
		)
	}
	return imapwire.EncodeList(parts...)
}

func encodingOr7bit(enc string) []byte {
	if enc == "" {
		enc = "7bit"
	}
	return imapwire.EncodeString([]byte(enc))
}

func encodeParams(p map[string]string) []byte {
	if len(p) == 0 {
		return imapwire.EncodeAtom("NIL")
	}
	list := make([][]byte, 0, len(p)*2)
	for k, v := range p {
		list = append(list, imapwire.EncodeString([]byte(strings.ToLower(k))))
		list = append(list, imapwire.EncodeString([]byte(v)))
	}
	return imapwire.EncodeList(list...)
}

func encodeDisposition(kind string, p map[string]string) []byte {
	if kind == "" {
		return imapwire.EncodeAtom("NIL")
	}
	return imapwire.EncodeList(
		imapwire.EncodeString([]byte(kind)),
		encodeParams(p),
	)
}

// ParseDate parses common RFC 5322 date forms for INTERNALDATE fallback.
func ParseDate(s string) (time.Time, error) {
	layouts := []string{
		time.RFC1123Z,
		"Mon, 2 Jan 2006 15:04:05 -0700",
		"Mon, 2 Jan 2006 15:04:05 MST",
		time.RFC1123,
		"2 Jan 2006 15:04:05 -0700",
	}
	var last error
	for _, l := range layouts {
		if t, err := time.Parse(l, s); err == nil {
			return t, nil
		} else {
			last = err
		}
	}
	return time.Time{}, fmt.Errorf("unparseable date %q: %w", s, last)
}
