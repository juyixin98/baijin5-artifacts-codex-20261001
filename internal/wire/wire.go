// Package wire is the byte-level codec boundary of the service: it frames
// IMAP command lines and literals on the read side, and renders untagged,
// tagged and continuation responses on the write side. It owns all byte
// limits; exceeding them yields errs.CatResource, malformed bytes yield
// errs.CatInput. No protocol state is kept here.
package wire

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"strconv"
	"sync"

	"imapd/internal/errs"
)

// Byte limits enforced at the codec boundary. They are the resource
// exhaustion guards of the service.
const (
	DefaultMaxLine    = 8192    // max command line excluding literals
	DefaultMaxLiteral = 1 << 20 // 1 MiB max literal payload
	MaxTagLen         = 64      // tag length cap
	maxLiteralDigits  = 8       // {n} with more digits is rejected early
)

// Command is one fully framed client command. Literal bytes are inlined
// into Args at the position they appeared; Args may therefore contain
// arbitrary binary data (NUL, CR, LF).
type Command struct {
	Tag  string
	Name string // upper-cased verb, e.g. "SELECT", "UID"
	Args []byte // raw remainder after the verb, literals inlined
}

// Reader frames commands from a client connection.
type Reader struct {
	r          *bufio.Reader
	MaxLine    int
	MaxLiteral int
}

func NewReader(r io.Reader) *Reader {
	return &Reader{r: bufio.NewReaderSize(r, 4096), MaxLine: DefaultMaxLine, MaxLiteral: DefaultMaxLiteral}
}

// ReadCommand reads one command, transparently absorbing literals. prompt
// is invoked to emit the "+ Ready for literal" continuation before each
// literal payload is consumed; it may be nil in tests that pre-buffer input.
func (rd *Reader) ReadCommand(prompt func() error) (*Command, error) {
	var buf []byte
	line, err := rd.readLine()
	if err != nil {
		return nil, err
	}
	buf = append(buf, line...)
	for {
		n, markerLen, ok, err := trailingLiteral(line)
		if err != nil {
			return nil, err
		}
		if !ok {
			break
		}
		if n > rd.MaxLiteral {
			return nil, errs.New(errs.CatResource, "wire.read",
				fmt.Sprintf("literal of %d bytes exceeds limit of %d", n, rd.MaxLiteral))
		}
		if prompt != nil {
			if err := prompt(); err != nil {
				return nil, errs.Wrap(errs.CatInput, "wire.read", err, "continuation write failed")
			}
		}
		// The "{n}" marker is framing, not data: the literal payload
		// replaces it in the accumulated command.
		buf = buf[:len(buf)-markerLen]
		lit := make([]byte, n)
		if _, err := io.ReadFull(rd.r, lit); err != nil {
			return nil, errs.Wrap(errs.CatInput, "wire.read", err, "literal payload truncated")
		}
		buf = append(buf, lit...)
		line, err = rd.readLine()
		if err != nil {
			return nil, err
		}
		buf = append(buf, line...)
	}
	return splitCommand(buf)
}

// readLine reads one CRLF (or bare LF) terminated line, enforcing MaxLine.
// A clean EOF before any byte is returned as io.EOF so the session layer
// can distinguish a client hang-up from a malformed stream.
func (rd *Reader) readLine() ([]byte, error) {
	var out []byte
	for {
		frag, err := rd.r.ReadSlice('\n')
		out = append(out, frag...)
		if err == bufio.ErrBufferFull {
			if len(out) > rd.MaxLine {
				return nil, errs.New(errs.CatResource, "wire.read",
					fmt.Sprintf("command line exceeds %d bytes", rd.MaxLine))
			}
			continue
		}
		if err != nil {
			if err == io.EOF && len(out) == 0 {
				return nil, io.EOF
			}
			return nil, errs.Wrap(errs.CatInput, "wire.read", err, "connection read failed")
		}
		break
	}
	if len(out) > rd.MaxLine {
		return nil, errs.New(errs.CatResource, "wire.read",
			fmt.Sprintf("command line exceeds %d bytes", rd.MaxLine))
	}
	out = out[:len(out)-1] // drop '\n'
	if n := len(out); n > 0 && out[n-1] == '\r' {
		out = out[:n-1]
	}
	return out, nil
}

// trailingLiteral reports whether line ends with a synchronising literal
// marker "{n}", returning the payload length n and the marker's own byte
// length. Only the freshly read text line is inspected, never the
// accumulated buffer, so literal payloads that themselves end in "{n}"
// cannot be misinterpreted. Non-synchronising "{n+}" and malformed markers
// are rejected as input errors: only declared framing is accepted.
func trailingLiteral(line []byte) (n, markerLen int, ok bool, err error) {
	if len(line) == 0 || line[len(line)-1] != '}' {
		return 0, 0, false, nil
	}
	i := bytes.LastIndexByte(line, '{')
	if i < 0 {
		return 0, 0, false, nil
	}
	digits := line[i+1 : len(line)-1]
	if len(digits) == 0 {
		return 0, 0, false, errs.New(errs.CatInput, "wire.read", "empty literal length")
	}
	if len(digits) > maxLiteralDigits {
		return 0, 0, false, errs.New(errs.CatResource, "wire.read", "literal length has too many digits")
	}
	for _, c := range digits {
		if c < '0' || c > '9' {
			return 0, 0, false, errs.New(errs.CatInput, "wire.read",
				"malformed literal length (non-synchronising {n+} is not supported)")
		}
	}
	n, err = strconv.Atoi(string(digits))
	if err != nil {
		return 0, 0, false, errs.Wrap(errs.CatInput, "wire.read", err, "malformed literal length")
	}
	return n, len(line) - i, true, nil
}

// splitCommand parses "<tag> <verb> <args...>" from a fully framed buffer.
func splitCommand(buf []byte) (*Command, error) {
	tag, rest := cutToken(buf)
	if len(tag) == 0 {
		return nil, errs.New(errs.CatInput, "wire.parse", "missing command tag")
	}
	if err := validTag(tag); err != nil {
		return nil, err
	}
	rest = bytes.TrimLeft(rest, " ")
	name, args := cutToken(rest)
	if len(name) == 0 {
		return nil, errs.New(errs.CatInput, "wire.parse", "missing command verb")
	}
	if len(name) > 32 {
		return nil, errs.New(errs.CatInput, "wire.parse", "command verb too long")
	}
	return &Command{Tag: string(tag), Name: string(bytes.ToUpper(name)), Args: args}, nil
}

func cutToken(b []byte) (tok, rest []byte) {
	i := bytes.IndexByte(b, ' ')
	if i < 0 {
		return b, nil
	}
	return b[:i], b[i+1:]
}

func validTag(tag []byte) error {
	if len(tag) > MaxTagLen {
		return errs.New(errs.CatInput, "wire.parse", "tag exceeds 64 bytes")
	}
	for _, c := range tag {
		// Reject controls, space and the response-specials that would
		// corrupt our own output framing.
		if c < 0x21 || c > 0x7e || c == '+' || c == '(' || c == ')' || c == '{' || c == '%' || c == '*' || c == '"' || c == '\\' || c == ']' {
			return errs.New(errs.CatInput, "wire.parse", "tag contains forbidden character")
		}
	}
	return nil
}

// Writer renders server responses. It is safe for concurrent use: the
// session's own responses and hub-delivered asynchronous events serialise
// on the same mutex, which is what keeps each connection's event order
// consistent.
type Writer struct {
	mu sync.Mutex
	w  io.Writer
}

func NewWriter(w io.Writer) *Writer { return &Writer{w: w} }

func (w *Writer) write(p []byte) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	_, err := w.w.Write(p)
	return err
}

// Untaggedf emits "* <text>".
func (w *Writer) Untaggedf(format string, args ...any) error {
	return w.write([]byte("* " + fmt.Sprintf(format, args...) + "\r\n"))
}

// Tagged emits "<tag> <STATUS> <text>".
func (w *Writer) Tagged(tag, status, text string) error {
	return w.write([]byte(tag + " " + status + " " + text + "\r\n"))
}

// Continuation emits "+ <text>".
func (w *Writer) Continuation(text string) error {
	return w.write([]byte("+ " + text + "\r\n"))
}

// Part is one FETCH attribute. A non-nil Literal renders as
// "<Text> {<n>}\r\n<bytes>"; a nil Literal renders as bare Text.
type Part struct {
	Text    string
	Literal []byte
}

// UntaggedFetch emits "* <seq> FETCH (<parts...>)" with literals inlined
// at their declared byte length, so binary payloads cannot desynchronise
// the stream.
func (w *Writer) UntaggedFetch(seq uint32, parts []Part) error {
	var b bytes.Buffer
	fmt.Fprintf(&b, "* %d FETCH (", seq)
	for i, p := range parts {
		if i > 0 {
			b.WriteByte(' ')
		}
		if p.Literal != nil {
			fmt.Fprintf(&b, "%s {%d}\r\n", p.Text, len(p.Literal))
			b.Write(p.Literal)
		} else {
			b.WriteString(p.Text)
		}
	}
	b.WriteString(")\r\n")
	return w.write(b.Bytes())
}
