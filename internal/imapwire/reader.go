package imapwire

import (
	"bufio"
	"bytes"
	"io"
	"strconv"
)

// MaxLiteralDefault is the default cap on one literal (5 MiB): a
// resource-exhaustion guard, not a protocol limit.
const MaxLiteralDefault = 5 << 20

// TokenKind enumerates the framed shapes of one command argument.
type TokenKind int

const (
	TokAtom    TokenKind = iota // unquoted run of bytes
	TokString                   // quoted string, escapes already resolved
	TokLiteral                  // {n}\r\n + exactly n payload bytes
	TokList                     // parenthesized token list
)

// Token is one framed argument. Payload bytes are exact: literals are counted
// by bytes, so binary content (NUL, 0xFF, embedded CRLF) survives untouched.
type Token struct {
	Kind TokenKind
	Raw  []byte
	Item []Token
}

// FramedCommand is one fully buffered command: framing consumed everything up
// to and including its CRLF, so a semantic parse failure never desynchronises
// the byte stream and the next command can still be served.
type FramedCommand struct {
	Tag  string
	Verb string
	Args []Token
}

// Framer frames commands off a connection. One framer is used by exactly one
// goroutine (a connection has a single serving goroutine).
type Framer struct {
	br         *bufio.Reader
	out        *Conn
	run        int
	lineBytes  int
	MaxLiteral int
	MaxLine    int
}

// NewFramer wraps r. Literal continuations are written to out.
func NewFramer(r io.Reader, out *Conn) *Framer {
	return &Framer{
		br:         bufio.NewReaderSize(r, 64*1024),
		out:        out,
		MaxLiteral: MaxLiteralDefault,
		MaxLine:    1 << 20,
	}
}

// RunNo returns the 1-based number of the command currently being framed.
func (f *Framer) RunNo() int { return f.run }

type fs = byte // alias to keep byte tables compact

const (
	cSp     fs = ' '
	cCR     fs = '\r'
	cLF     fs = '\n'
	cDquote fs = '"'
	cLBrace fs = '{'
	cRBrace fs = '}'
	cLParen fs = '('
	cRParen fs = ')'
	cLBrack fs = '['
	cRBrack fs = ']'
	cLT     fs = '<'
	cGT     fs = '>'
)

func isCtl(b byte) bool { return b < 0x20 || b == 0x7f }

func badf(format string, args ...any) *Error {
	return NewError(ClassInput, format, args...)
}

func (f *Framer) rb() (byte, error) {
	b, err := f.br.ReadByte()
	if err != nil {
		return 0, err
	}
	f.lineBytes++
	if f.lineBytes > f.MaxLine {
		return 0, NewCodedError(ClassResource, "TOOBIG",
			"command exceeds %d non-literal bytes", f.MaxLine)
	}
	return b, nil
}

func (f *Framer) must() byte {
	b, err := f.rb()
	if err != nil {
		if we, ok := err.(*Error); ok {
			panic(we)
		}
		panic(ioErr{err})
	}
	return b
}

func (f *Framer) unread() {
	if err := f.br.UnreadByte(); err != nil {
		panic(ioErr{err})
	}
}

// ioErr marks transport failures (client disconnect, broken pipe).
type ioErr struct{ err error }

func (e ioErr) Error() string { return "io: " + e.err.Error() }
func (e ioErr) Unwrap() error { return e.err }

// IsIOError reports whether err is a transport-level failure.
func IsIOError(err error) bool {
	_, ok := err.(ioErr)
	return ok
}

// Frame reads one complete command. io.EOF is returned at a clean end of
// stream before any byte of the next command.
func (f *Framer) Frame() (cmd *FramedCommand, err error) {
	// Transport panics (ioErr) propagate; protocol panics are caught here
	// only when they occur before the tag is known.
	// Transport and resource panics are recovered at Frame's boundary.
	defer func() {
		if p := recover(); p != nil {
			switch v := p.(type) {
			case *Error:
				cmd, err = nil, v
			case ioErr:
				cmd, err = nil, v
			default:
				panic(p)
			}
		}
	}()
	f.lineBytes = 0
	b, ioerr := f.rb()
	if ioerr != nil {
		return nil, ioerr
	}
	f.run++
	tag, e := f.readWordFrom(b)
	if e != nil {
		return nil, e
	}
	if len(tag) == 0 {
		return nil, badf("missing command tag")
	}
	if len(tag) > 32 {
		return nil, badf("command tag too long (%d bytes, max 32)", len(tag))
	}
	if err := f.expect(cSp); err != nil {
		return nil, err
	}
	verb, err := f.readWord()
	if err != nil {
		return nil, err
	}
	if len(verb) == 0 {
		return nil, badf("missing command verb")
	}
	cmd = &FramedCommand{Tag: string(tag), Verb: string(bytes.ToUpper(verb))}
	for {
		ch, err := f.rb()
		if err != nil {
			return nil, ioErr{err}
		}
		switch ch {
		case cCR:
			next, err := f.rb()
			if err != nil {
				return nil, ioErr{err}
			}
			if next != cLF {
				return nil, badf("expected LF after CR, got 0x%02x", next)
			}
			return cmd, nil
		case cLF: // tolerate bare LF
			return cmd, nil
		case cSp:
			tok, err := f.readToken()
			if err != nil {
				return nil, err
			}
			cmd.Args = append(cmd.Args, tok)
		default:
			return nil, badf("expected SP or CRLF, got 0x%02x", ch)
		}
	}
}

func (f *Framer) expect(want byte) error {
	b, err := f.rb()
	if err != nil {
		return ioErr{err}
	}
	if b != want {
		return badf("expected 0x%02x, got 0x%02x", want, b)
	}
	return nil
}

// readWord reads one atom-like run starting at the current byte.
func (f *Framer) readWord() ([]byte, error) {
	b, err := f.rb()
	if err != nil {
		return nil, ioErr{err}
	}
	return f.readWordFrom(b)
}

func (f *Framer) readWordFrom(first byte) ([]byte, error) {
	var buf bytes.Buffer
	b := first
	for {
		switch {
		case b == cSp || b == cCR || b == cLF || b == cLParen || b == cRParen:
			f.unread()
			return buf.Bytes(), nil
		case b == cLBrack:
			// BODY[...]: keep section spec attached to the atom, tracking
			// nested parens/quotes so the closing ']' is unambiguous.
			buf.WriteByte(b)
			if err := f.copyBracketed(&buf); err != nil {
				return nil, err
			}
		case b == cLT:
			// Partial <start.end>; frame it, semantic layer rejects/uses it.
			buf.WriteByte(b)
			if err := f.copyAngled(&buf); err != nil {
				return nil, err
			}
		case isCtl(b):
			return nil, badf("control byte 0x%02x inside atom", b)
		default:
			buf.WriteByte(b)
		}
		b = f.must()
	}
}

func (f *Framer) copyBracketed(buf *bytes.Buffer) error {
	depth := 1
	for depth > 0 {
		b := f.must()
		buf.WriteByte(b)
		switch b {
		case cLBrack:
			depth++
		case cRBrack:
			depth--
		case cDquote:
			if err := f.copyQuotedInto(buf); err != nil {
				return err
			}
		case cLParen:
			depth++
		case cRParen:
			depth--
		case cCR, cLF:
			return badf("CR/LF inside bracket section")
		}
	}
	return nil
}

func (f *Framer) copyAngled(buf *bytes.Buffer) error {
	for {
		b := f.must()
		buf.WriteByte(b)
		if b == cGT {
			return nil
		}
		if b == cCR || b == cLF || b == cSp {
			return badf("malformed partial spec")
		}
	}
}

func (f *Framer) readToken() (Token, error) {
	b := f.must()
	switch b {
	case cLParen:
		return f.readList()
	case cDquote:
		s, err := f.readQuoted()
		return Token{Kind: TokString, Raw: s}, err
	case cLBrace:
		raw, err := f.readLiteral()
		return Token{Kind: TokLiteral, Raw: raw}, err
	default:
		raw, err := f.readWordFrom(b)
		if err != nil {
			return Token{}, err
		}
		return Token{Kind: TokAtom, Raw: raw}, nil
	}
}

func (f *Framer) readList() (Token, error) {
	list := Token{Kind: TokList}
	for {
		b := f.must()
		switch b {
		case cRParen:
			return list, nil
		case cSp:
			continue
		default:
			tok, err := f.readTokenFrom(b)
			if err != nil {
				return Token{}, err
			}
			list.Item = append(list.Item, tok)
			// Next must be SP or ')' (SP is optional in practice).
			c := f.must()
			if c == cRParen {
				return list, nil
			}
			if c != cSp {
				return Token{}, badf("expected SP or ')' in list, got 0x%02x", c)
			}
		}
	}
}

func (f *Framer) readTokenFrom(b byte) (Token, error) {
	switch b {
	case cLParen:
		return f.readList()
	case cDquote:
		s, err := f.readQuoted()
		return Token{Kind: TokString, Raw: s}, err
	case cLBrace:
		raw, err := f.readLiteral()
		return Token{Kind: TokLiteral, Raw: raw}, err
	default:
		raw, err := f.readWordFrom(b)
		if err != nil {
			return Token{}, err
		}
		return Token{Kind: TokAtom, Raw: raw}, nil
	}
}

// readQuoted is entered with the opening quote already consumed.
func (f *Framer) readQuoted() ([]byte, error) {
	var buf bytes.Buffer
	for {
		b := f.must()
		switch b {
		case cDquote:
			return buf.Bytes(), nil
		case cCR, cLF:
			return nil, badf("bare CR/LF inside quoted string")
		case '\\':
			c := f.must()
			if c != cDquote && c != '\\' {
				return nil, badf("bad quoted escape \\%c", c)
			}
			buf.WriteByte(c)
		case 0:
			return nil, badf("NUL inside quoted string")
		default:
			if isCtl(b) {
				return nil, badf("control byte 0x%02x inside quoted string", b)
			}
			buf.WriteByte(b)
		}
	}
}

// copyQuotedInto copies a quoted run (including closing quote) into buf;
// used when quotes appear inside a BODY[..] section spec.
func (f *Framer) copyQuotedInto(buf *bytes.Buffer) error {
	for {
		b := f.must()
		buf.WriteByte(b)
		switch b {
		case cDquote:
			return nil
		case '\\':
			c := f.must()
			buf.WriteByte(c)
		case cCR, cLF:
			return badf("CR/LF inside bracket-embedded quoted string")
		}
	}
}

// readLiteral is entered with '{' already consumed: digits, '}', CRLF, then
// the "+ go ahead" continuation and exactly n payload bytes.
func (f *Framer) readLiteral() ([]byte, error) {
	var digits []byte
	for {
		b := f.must()
		if b >= '0' && b <= '9' {
			digits = append(digits, b)
			continue
		}
		if b != cRBrace {
			return nil, badf("malformed literal length, byte 0x%02x", b)
		}
		break
	}
	if len(digits) == 0 {
		return nil, badf("empty literal length")
	}
	n, err := strconv.Atoi(string(digits))
	if err != nil || n < 0 {
		return nil, badf("invalid literal length %q", string(digits))
	}
	if err := f.expect(cCR); err != nil {
		return nil, err
	}
	if err := f.expect(cLF); err != nil {
		return nil, err
	}
	if n > f.MaxLiteral {
		// The n payload bytes are still unread and the stream cannot be
		// realigned safely, so this is fatal to the connection.
		return nil, NewCodedError(ClassResource, "TOOBIG",
			"literal %d bytes exceeds limit %d", n, f.MaxLiteral)
	}
	f.out.WriteContinue("go ahead")
	buf := make([]byte, n)
	if _, err := io.ReadFull(f.br, buf); err != nil {
		panic(ioErr{err})
	}
	return buf, nil
}
