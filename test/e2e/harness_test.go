// Package e2e contains black-box compatibility tests driven through a raw
// TCP client whose IMAP codec is written independently in this test package:
// it imports none of the server's protocol packages, so expected bytes are
// asserted against a separate implementation rather than the code under test.
package e2e

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"log"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"imaplite/internal/auth"
	"imaplite/internal/fixture"
	"imaplite/internal/server"
	"imaplite/internal/store"
)

// ---- test world boot ------------------------------------------------------

type world struct {
	store *store.Store
	srv   *server.Server
	users *auth.UserStore
	data  *fixture.Loaded
}

func startWorld(t *testing.T) *world {
	t.Helper()
	return startWorldOpt(t, func(*server.Config) {})
}

func startWorldOpt(t *testing.T, tweak func(*server.Config)) *world {
	t.Helper()
	dir := t.TempDir()
	ctx := context.Background()
	st, err := store.Open(ctx, filepath.Join(dir, "test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })

	loaded, err := fixture.Load(fixture.DefaultDataDir())
	if err != nil {
		t.Fatalf("load fixtures: %v", err)
	}
	if err := loaded.Provision(st); err != nil {
		t.Fatalf("seed: %v", err)
	}
	users := auth.NewUserStore()
	if err := loaded.ProvisionAccounts(users); err != nil {
		t.Fatalf("accounts: %v", err)
	}
	cfg := server.Config{
		Addr: "127.0.0.1:0", Store: st, Users: users, Logger: discardSlog(),
	}
	tweak(&cfg)
	srv := server.New(cfg)
	if err := srv.Listen(); err != nil {
		t.Fatalf("listen: %v", err)
	}
	go func() { _ = srv.Serve(ctx) }()
	t.Cleanup(func() { _ = srv.Close() })
	return &world{store: st, srv: srv, users: users, data: loaded}
}

// ---- independent raw IMAP client -----------------------------------------

// record is one decoded server response. Literals are pulled into Literals in
// order of appearance and replaced in Text by the placeholder "\x00LIT<n>\x00".
type record struct {
	raw      string
	literals [][]byte

	Tag       string // empty for untagged/continuation
	Status    string // OK/NO/BAD for tagged
	Code      string // response code token
	Text      string // completion text
	IsEOF     bool   // connection closed (safe mode)
	IsUntag   bool
	IsCont    bool
	UntagKey  string // EXISTS / EXPUNGE / FETCH / FLAGS / OK ...
	Seq       int    // leading number for "* n KEY"
	Fetch     map[string]string
	FetchLit  map[string][]byte // item name -> literal payload
	FetchSeen []string          // item names in response order
}

type client struct {
	t    *testing.T
	conn net.Conn
	br   *bufio.Reader
	mu   sync.Mutex
	runs int
	log  *log.Logger
	// safe, when set, turns an EOF/reset into an EOF record instead of
	// failing the test (used when the server is expected to close).
	safe bool
}

// errEOFRecord marks a clean connection close in safe mode.
var errEOFRecord = record{raw: "", Tag: "", IsEOF: true}

func dial(t *testing.T, w *world, label string) *client {
	t.Helper()
	c, err := net.Dial("tcp", w.srv.Addr())
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	cl := &client{
		t:    t,
		conn: c,
		br:   bufio.NewReaderSize(c, 1<<20),
		log:  log.New(os.Stderr, "  ["+label+"] ", log.LstdFlags|log.Lmicroseconds),
	}
	greet := cl.readOne()
	if !greet.IsUntag || greet.UntagKey != "OK" {
		t.Fatalf("bad greeting: %q", greet.raw)
	}
	cl.log.Printf("run=0 greeting %q", abbreviate(greet.raw))
	return cl
}

func (c *client) close() { _ = c.conn.Close() }

// run sends one command "<tag> <cmd>"; "{n}" within cmd marks a literal whose
// bytes are supplied via lits (the client performs the "+ go ahead" handshake).
func (c *client) run(tag, cmd string, lits ...[]byte) []record {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.runs++
	run := c.runs
	c.log.Printf("run=%d >> %s %s", run, tag, abbreviate(stripMarkers(cmd)))

	remaining := cmd
	prefix := tag + " " // tag precedes the first wire chunk
	li := 0
	for {
		idx := strings.Index(remaining, "{n}")
		if idx < 0 {
			break
		}
		payload := lits[li]
		li++
		c.write(prefix + remaining[:idx] + "{" + strconv.Itoa(len(payload)) + "}\r\n")
		prefix = ""
		c.log.Printf("run=%d    literal head len=%d", run, len(payload))
		cont := c.readOne()
		if !cont.IsCont {
			c.t.Fatalf("run=%d expected '+', got %q", run, cont.raw)
		}
		if _, err := c.conn.Write(payload); err != nil {
			c.t.Fatalf("write literal: %v", err)
		}
		remaining = remaining[idx+3:]
	}
	c.write(prefix + remaining + "\r\n")

	var recs []record
	for {
		rec := c.readOne()
		c.log.Printf("run=%d << %s", run, abbreviate(rec.raw))
		recs = append(recs, rec)
		if rec.Tag == tag {
			return recs
		}
	}
}

func (c *client) write(s string) {
	if _, err := c.conn.Write([]byte(s)); err != nil {
		c.t.Fatalf("write: %v", err)
	}
}

// readOne decodes exactly one logical response, transparently consuming the
// bytes of every non-synchronizing literal "{n}\r\n" it contains. In safe
// mode a connection close returns an IsEOF record instead of failing.
func (c *client) readOne() record {
	return c.readOneDepth(0)
}

func (c *client) readOneDepth(_ int) record {
	var text bytes.Buffer
	var lits [][]byte
	putLit := func(b []byte) {
		marker := "\x00LIT" + strconv.Itoa(len(lits)) + "\x00"
		lits = append(lits, b)
		text.WriteString(marker)
	}
	for {
		b, err := c.br.ReadByte()
		if err != nil {
			if c.safe && (isEOF(err)) {
				return record{IsEOF: true}
			}
			c.t.Fatalf("read byte: %v", err)
		}
		if b == '{' {
			var num []byte
			for {
				d := c.mustByte()
				if d == '}' {
					break
				}
				num = append(num, d)
			}
			c.expectCRLF()
			n, err := strconv.Atoi(string(num))
			if err != nil {
				c.t.Fatalf("bad literal length %q", string(num))
			}
			buf := make([]byte, n)
			if _, err := io.ReadFull(c.br, buf); err != nil {
				if c.safe && isEOF(err) {
					return record{IsEOF: true}
				}
				c.t.Fatalf("literal payload: %v", err)
			}
			putLit(buf)
			continue
		}
		if b == '\r' {
			c.mustByteLF()
			return finishRecord(text.String(), lits, c.t)
		}
		if b == '\n' {
			return finishRecord(text.String(), lits, c.t)
		}
		text.WriteByte(b)
	}
}

func isEOF(err error) bool {
	return err == io.EOF || err == io.ErrUnexpectedEOF ||
		strings.Contains(err.Error(), "EOF") || strings.Contains(err.Error(), "reset") ||
		strings.Contains(err.Error(), "closed")
}

// runSafe sends a command the server is expected to possibly reject by
// closing the connection; it collects every record up to and including the
// tagged completion or the EOF marker.
func (c *client) runSafe(tag, cmd string, lits ...[]byte) []record {
	c.safe = true
	defer func() { c.safe = false }()
	c.mu.Lock()
	defer c.mu.Unlock()
	c.runs++
	run := c.runs
	c.log.Printf("run=%d >> (safe) %s %s", run, tag, abbreviate(stripMarkers(cmd)))

	remaining := cmd
	prefix := tag + " "
	li := 0
	var recs []record
	for {
		idx := strings.Index(remaining, "{n}")
		if idx < 0 {
			break
		}
		payload := lits[li]
		li++
		c.write(prefix + remaining[:idx] + "{" + strconv.Itoa(len(payload)) + "}\r\n")
		prefix = ""
		cont := c.readOne()
		if cont.IsEOF {
			return append(recs, cont)
		}
		if !cont.IsCont {
			// Server rejected the literal before the continuation (e.g.
			// TOOBIG): collect this and any following records through EOF.
			recs = append(recs, cont)
			for {
				r := c.readOne()
				recs = append(recs, r)
				if r.IsEOF || r.Tag == tag {
					return recs
				}
			}
		}
		if _, err := c.conn.Write(payload); err != nil {
			return append(recs, record{IsEOF: true})
		}
		remaining = remaining[idx+3:]
	}
	c.write(prefix + remaining + "\r\n")

	for {
		rec := c.readOne()
		c.log.Printf("run=%d << %s", run, abbreviate(rec.raw))
		recs = append(recs, rec)
		if rec.IsEOF || rec.Tag == tag {
			return recs
		}
	}
}

func (c *client) mustByte() byte {
	b, err := c.br.ReadByte()
	if err != nil {
		c.t.Fatalf("read: %v", err)
	}
	return b
}
func (c *client) mustByteLF() {
	b, err := c.br.ReadByte()
	if err != nil {
		c.t.Fatalf("read LF: %v", err)
	}
	if b != '\n' {
		c.t.Fatalf("expected LF, got 0x%02x", b)
	}
}
func (c *client) expectCRLF() {
	b := c.mustByte()
	if b == '\n' {
		return
	}
	if b != '\r' {
		c.t.Fatalf("expected CRLF after literal, got 0x%02x", b)
	}
	c.mustByteLF()
}

func finishRecord(raw string, lits [][]byte, t *testing.T) record {
	rec := record{raw: raw, literals: lits}
	switch {
	case strings.HasPrefix(raw, "+"):
		rec.IsCont = true
		return rec
	case strings.HasPrefix(raw, "* "):
		rec.IsUntag = true
		decodeUntagged(&rec, strings.TrimPrefix(raw, "* "), lits)
		return rec
	default:
		parts := strings.SplitN(raw, " ", 3)
		if len(parts) >= 2 {
			rec.Tag, rec.Status = parts[0], parts[1]
			rest := ""
			if len(parts) == 3 {
				rest = parts[2]
			}
			if strings.HasPrefix(rest, "[") {
				if end := strings.IndexByte(rest, ']'); end >= 0 {
					rec.Code = rest[1:end]
					rec.Text = strings.TrimSpace(rest[end+1:])
					return rec
				}
			}
			rec.Text = rest
		}
		return rec
	}
}

func decodeUntagged(rec *record, body string, lits [][]byte) {
	fields := strings.SplitN(body, " ", 2)
	if len(fields) >= 2 {
		if n, err := strconv.Atoi(fields[0]); err == nil {
			rec.Seq = n
			rest := fields[1]
			key := rest
			if sp := strings.IndexByte(rest, ' '); sp >= 0 {
				key = rest[:sp]
			}
			rec.UntagKey = key
			if key == "FETCH" {
				parseFetch(rec, rest, lits)
			}
			return
		}
	}
	// "* OK [CODE] text" / "* BYE text"
	rec.UntagKey = fields[0]
	rest := ""
	if len(fields) == 2 {
		rest = fields[1]
	}
	if strings.HasPrefix(rest, "[") {
		if end := strings.IndexByte(rest, ']'); end >= 0 {
			// Full content, e.g. "UIDVALIDITY 1001"; Code keeps the first
			// token and Text keeps the value portion for assertions.
			content := rest[1:end]
			if sp := strings.IndexByte(content, ' '); sp >= 0 {
				rec.Code = content[:sp]
				rec.Text = strings.TrimSpace(content[sp+1:])
			} else {
				rec.Code = content
			}
			return
		}
	}
	rec.Text = rest
}

// parseFetch parses "FETCH (item value ...)" where values may be numbers,
// quoted strings, nested lists, NIL or literal placeholders. It walks the
// structure rather than splitting on spaces.
func parseFetch(rec *record, rest string, lits [][]byte) {
	open := strings.IndexByte(rest, '(')
	if open < 0 {
		return
	}
	p := &cursor{s: rest[open+1:], lits: lits}
	rec.Fetch = map[string]string{}
	rec.FetchLit = map[string][]byte{}
	for {
		p.skipSpaces()
		if p.atEnd() || p.peek() == ')' {
			break
		}
		name := strings.ToUpper(p.readAtom())
		if name == "" {
			break
		}
		p.skipSpaces()
		val, lit := p.readValue()
		rec.FetchSeen = append(rec.FetchSeen, name)
		rec.Fetch[name] = val
		if lit != nil {
			rec.FetchLit[name] = lit
		}
	}
}

type cursor struct {
	s    string
	pos  int
	lits [][]byte
}

func (p *cursor) atEnd() bool { return p.pos >= len(p.s) }
func (p *cursor) peek() byte  { return p.s[p.pos] }
func (p *cursor) skipSpaces() {
	for p.pos < len(p.s) && p.s[p.pos] == ' ' {
		p.pos++
	}
}

func (p *cursor) readAtom() string {
	start := p.pos
	for p.pos < len(p.s) {
		ch := p.s[p.pos]
		switch ch {
		case '[':
			// BODY[HEADER.FIELDS (SUBJECT)]: the whole bracketed section is
			// part of the item name, spaces and parens inside do not count.
			p.pos++
			depth := 1
			for p.pos < len(p.s) && depth > 0 {
				switch p.s[p.pos] {
				case '[':
					depth++
				case ']':
					depth--
				}
				p.pos++
			}
		case '<':
			p.pos++
			for p.pos < len(p.s) && p.s[p.pos] != '>' {
				p.pos++
			}
			if p.pos < len(p.s) {
				p.pos++
			}
		case ' ', ')', '(':
			return p.s[start:p.pos]
		default:
			p.pos++
		}
	}
	return p.s[start:p.pos]
}

// readValue consumes one value: quoted string, parenthesized list, NIL/atom,
// number, or a literal placeholder (returning the payload separately).
func (p *cursor) readValue() (string, []byte) {
	switch ch := p.peek(); ch {
	case '"':
		return p.readQuoted(), nil
	case '(':
		return p.readNested(), nil
	case '\x00':
		// "\x00LIT<n>\x00"
		end := strings.IndexByte(p.s[p.pos+1:], '\x00')
		token := p.s[p.pos : p.pos+1+end+1]
		p.pos += len(token)
		idx := firstLitIndex(token)
		if idx >= 0 && idx < len(p.lits) {
			return token, p.lits[idx]
		}
		return token, nil
	default:
		v := p.readAtom()
		return v, nil
	}
}

func (p *cursor) readQuoted() string {
	p.pos++ // opening quote
	var b strings.Builder
	for p.pos < len(p.s) {
		ch := p.s[p.pos]
		if ch == '\\' && p.pos+1 < len(p.s) {
			p.pos++
			b.WriteByte(p.s[p.pos])
			p.pos++
			continue
		}
		if ch == '"' {
			p.pos++
			break
		}
		b.WriteByte(ch)
		p.pos++
	}
	return b.String()
}

// readNested consumes a balanced parenthesized group and returns its raw text
// (including both parentheses), recursing so ENVELOPE/BODYSTRUCTURE/FLAGS
// survive intact for assertions.
func (p *cursor) readNested() string {
	start := p.pos
	depth := 0
	inQuote := false
	for p.pos < len(p.s) {
		ch := p.s[p.pos]
		switch {
		case ch == '\\' && inQuote:
			p.pos += 2
			continue
		case ch == '"':
			inQuote = !inQuote
		case !inQuote && ch == '(':
			depth++
		case !inQuote && ch == ')':
			depth--
			p.pos++
			if depth == 0 {
				return p.s[start:p.pos]
			}
			continue
		case ch == '\x00':
			// Skip a literal placeholder inside the nested structure.
			end := strings.IndexByte(p.s[p.pos+1:], '\x00')
			if end >= 0 {
				p.pos += end + 2
				continue
			}
		}
		p.pos++
	}
	return p.s[start:p.pos]
}

func firstLitIndex(val string) int {
	const mk = "\x00LIT"
	i := strings.Index(val, mk)
	if i < 0 {
		return -1
	}
	d := val[i+len(mk):]
	end := strings.IndexByte(d, '\x00')
	if end < 0 {
		return -1
	}
	n, _ := strconv.Atoi(d[:end])
	return n
}

func stripMarkers(s string) string { return strings.ReplaceAll(s, "{n}", "") }

func abbreviate(s string) string {
	s = strings.ReplaceAll(s, "\r", "\\r")
	s = strings.ReplaceAll(s, "\n", "\\n")
	if len(s) > 140 {
		return s[:140] + "...(" + strconv.Itoa(len(s)) + "B)"
	}
	return s
}

func sleepForPump() { time.Sleep(80 * time.Millisecond) }
