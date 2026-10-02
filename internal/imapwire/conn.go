package imapwire

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"strconv"
	"sync"
)

// Conn is the server-side response encoder for one TCP connection. All bytes
// leave the server through a Conn so framing (CRLF, literals, quoting) has a
// single implementation and a single mutex. Each public response method is
// atomic: the complete logical record is assembled under the lock, so the
// event-pump goroutine can never splice an unsolicited line into the middle
// of a multi-item FETCH record.
type Conn struct {
	mu   sync.Mutex
	w    *bufio.Writer
	dead bool // set once the transport write fails
}

// NewConn wraps an outbound connection (the accepted TCP conn).
func NewConn(w io.Writer) *Conn {
	return &Conn{w: bufio.NewWriterSize(w, 64*1024)}
}

// Closed reports whether a prior write failed and the connection is dead.
func (c *Conn) Closed() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.dead
}

// emit runs fn against a buffer while holding the lock, then writes and
// flushes. A transport write failure marks the connection dead instead of
// panicking: a vanished peer must never crash the serving goroutine.
func (c *Conn) emit(fn func(b *bytes.Buffer)) {
	var buf bytes.Buffer
	fn(&buf)
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.dead {
		return
	}
	if _, err := c.w.Write(buf.Bytes()); err != nil {
		c.dead = true
		return
	}
	if err := c.w.Flush(); err != nil {
		c.dead = true
	}
}

// WriteContinue sends a continuation request "+ <text>\r\n".
func (c *Conn) WriteContinue(text string) {
	c.emit(func(b *bytes.Buffer) { b.WriteString("+ " + text + "\r\n") })
}

// WriteOK sends an unsolicited "* OK ..." greeting/status line.
func (c *Conn) WriteOK(text string) {
	c.emit(func(b *bytes.Buffer) { b.WriteString("* OK " + text + "\r\n") })
}

// WriteBye sends "* BYE ...".
func (c *Conn) WriteBye(text string) {
	c.emit(func(b *bytes.Buffer) { b.WriteString("* BYE " + text + "\r\n") })
}

// Status is a tagged completion status.
type Status string

const (
	StatusOK  Status = "OK"
	StatusNO  Status = "NO"
	StatusBAD Status = "BAD"
)

// WriteTagged sends "<tag> <STATUS> [CODE] <text>\r\n".
func (c *Conn) WriteTagged(tag string, st Status, code, text string) {
	c.emit(func(b *bytes.Buffer) {
		b.WriteString(tag)
		b.WriteByte(' ')
		b.WriteString(string(st))
		if code != "" {
			b.WriteString(" [" + code + "]")
		}
		b.WriteByte(' ')
		b.WriteString(text)
		b.WriteString("\r\n")
	})
}

// WriteTaggedf is the printf form of WriteTagged.
func (c *Conn) WriteTaggedf(tag string, st Status, code, format string, args ...any) {
	c.WriteTagged(tag, st, code, fmt.Sprintf(format, args...))
}

// WriteCapability sends "* CAPABILITY (...)".
func (c *Conn) WriteCapability(caps []string) {
	c.emit(func(b *bytes.Buffer) {
		b.WriteString("* CAPABILITY (")
		for i, cap := range caps {
			if i > 0 {
				b.WriteByte(' ')
			}
			b.WriteString(cap)
		}
		b.WriteString(")\r\n")
	})
}

// WriteExists sends "* <n> EXISTS".
func (c *Conn) WriteExists(n int) {
	c.emit(func(b *bytes.Buffer) {
		b.WriteString("* ")
		b.WriteString(strconv.Itoa(n))
		b.WriteString(" EXISTS\r\n")
	})
}

// WriteRecent sends "* <n> RECENT". This server never assigns \Recent, so 0.
func (c *Conn) WriteRecent(n int) {
	c.emit(func(b *bytes.Buffer) {
		b.WriteString("* ")
		b.WriteString(strconv.Itoa(n))
		b.WriteString(" RECENT\r\n")
	})
}

// WriteExpunge sends "* <seq> EXPUNGE" for one removed message.
func (c *Conn) WriteExpunge(seq int) {
	c.emit(func(b *bytes.Buffer) {
		b.WriteString("* ")
		b.WriteString(strconv.Itoa(seq))
		b.WriteString(" EXPUNGE\r\n")
	})
}

// WriteFlags sends "* FLAGS (...)".
func (c *Conn) WriteFlags(flags []string) {
	c.emit(func(b *bytes.Buffer) {
		b.WriteString("* FLAGS (")
		for i, f := range flags {
			if i > 0 {
				b.WriteByte(' ')
			}
			b.WriteString(f)
		}
		b.WriteString(")\r\n")
	})
}

// WriteUntaggedOK sends "* OK [<token>] <text>" where token is the FULL
// response-code content (e.g. "UIDVALIDITY 1001" or `PERMANENTFLAGS (\Seen)`),
// per RFC 3501's resp-code grammar.
func (c *Conn) WriteUntaggedOK(token, text string) {
	c.emit(func(b *bytes.Buffer) {
		b.WriteString("* OK")
		if token != "" {
			b.WriteString(" [" + token + "]")
		}
		if text != "" {
			b.WriteByte(' ')
			b.WriteString(text)
		}
		b.WriteString("\r\n")
	})
}

// WriteUntaggedOKf is the printf form of WriteUntaggedOK.
func (c *Conn) WriteUntaggedOKf(token, format string, args ...any) {
	c.WriteUntaggedOK(token, fmt.Sprintf(format, args...))
}

// Flush forces buffered bytes out. Individual records already flush, so this
// is a no-op-ish barrier used at batch boundaries.
func (c *Conn) Flush() {
	c.mu.Lock()
	defer c.mu.Unlock()
	_ = c.w.Flush()
}

// WriteFetch sends one "* <seq> FETCH (<items>)" record, assembled atomically.
func (c *Conn) WriteFetch(seq int, items []FetchItem) {
	c.emit(func(b *bytes.Buffer) {
		b.WriteString("* ")
		b.WriteString(strconv.Itoa(seq))
		b.WriteString(" FETCH (")
		for i, it := range items {
			if i > 0 {
				b.WriteByte(' ')
			}
			b.WriteString(it.Name)
			b.WriteByte(' ')
			switch it.Kind {
			case KindNumber:
				b.WriteString(strconv.FormatUint(uint64(it.Num), 10))
			case KindQuoted:
				renderQuoted(b, it.Text)
			case KindAtom:
				b.WriteString(it.Text)
			case KindFlagList:
				b.WriteByte('(')
				for j, f := range it.Flags {
					if j > 0 {
						b.WriteByte(' ')
					}
					b.WriteString(f)
				}
				b.WriteByte(')')
			case KindLiteral:
				renderLiteral(b, it.Raw)
			case KindVerbatim:
				b.Write(it.Raw)
			}
		}
		b.WriteString(")\r\n")
	})
}

// FetchItemKind selects the wire rendering of one FETCH data item.
type FetchItemKind int

const (
	KindNumber FetchItemKind = iota
	KindQuoted
	KindAtom
	KindFlagList
	KindLiteral
	// KindVerbatim emits Raw as an already-encoded IMAP response fragment
	// (used for nested structures such as ENVELOPE/BODYSTRUCTURE that the
	// MIME layer builds with EncodeList/EncodeNIL helpers).
	KindVerbatim
)

// FetchItem is one almost-rendered data item; the store decides values,
// imapwire decides bytes.
type FetchItem struct {
	Name  string
	Kind  FetchItemKind
	Num   uint32
	Text  string
	Flags []string
	Raw   []byte
}

// renderQuoted emits a quoted string, falling back to a literal for bytes
// that cannot live inside quotes (CR/LF/NUL/control chars).
func renderQuoted(b *bytes.Buffer, s string) {
	for i := 0; i < len(s); i++ {
		if isCtl(s[i]) {
			renderLiteral(b, []byte(s))
			return
		}
	}
	b.WriteByte('"')
	for i := 0; i < len(s); i++ {
		ch := s[i]
		if ch == '"' || ch == '\\' {
			b.WriteByte('\\')
		}
		b.WriteByte(ch)
	}
	b.WriteByte('"')
}

// renderLiteral emits a non-synchronizing literal "{n}\r\n" + exactly n bytes.
func renderLiteral(b *bytes.Buffer, raw []byte) {
	b.WriteByte('{')
	b.WriteString(strconv.Itoa(len(raw)))
	b.WriteString("}\r\n")
	b.Write(raw)
}

// ---- fragment encoders for nested response structures (ENVELOPE etc.) ----

// EncodeString renders s as a response string: NIL for nil, quoted string
// when safe, or a literal otherwise.
func EncodeString(s []byte) []byte {
	if s == nil {
		return []byte("NIL")
	}
	for i := 0; i < len(s); i++ {
		if isCtl(s[i]) {
			return encodeLiteralBytes(s)
		}
	}
	var out bytes.Buffer
	out.WriteByte('"')
	for i := 0; i < len(s); i++ {
		b := s[i]
		if b == '"' || b == '\\' {
			out.WriteByte('\\')
		}
		out.WriteByte(b)
	}
	out.WriteByte('"')
	return out.Bytes()
}

// EncodeList wraps the already-encoded fragments in "(...)".
func EncodeList(parts ...[]byte) []byte {
	var out bytes.Buffer
	out.WriteByte('(')
	for i, p := range parts {
		if i > 0 {
			out.WriteByte(' ')
		}
		out.Write(p)
	}
	out.WriteByte(')')
	return out.Bytes()
}

// EncodeAtom renders an unquoted atom.
func EncodeAtom(s string) []byte { return []byte(s) }

func encodeLiteralBytes(b []byte) []byte {
	var out bytes.Buffer
	out.WriteString("{" + strconv.Itoa(len(b)) + "}\r\n")
	out.Write(b)
	return out.Bytes()
}
