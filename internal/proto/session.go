// Package proto is the protocol state machine: it frames commands through
// the wire codec, enforces the selected/not-selected state split, executes
// the declared command subset (SELECT, FETCH, UID FETCH, STORE, UID STORE,
// EXPUNGE, NOOP, CAPABILITY, LOGOUT) against the store, and maps the
// shared error taxonomy onto IMAP statuses. Unknown commands and
// undeclared data items are explicitly rejected, never silently ignored.
package proto

import (
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"strings"

	"imapd/internal/errs"
	"imapd/internal/store"
	"imapd/internal/wire"
)

// Session is one client connection's protocol state.
type Session struct {
	Store *store.Store
	Hub   *Hub
	Log   *log.Logger // shared, run-id prefixed; may be nil

	w       *wire.Writer
	mailbox string // "" while not selected
}

// Serve runs the session loop until LOGOUT, hang-up, or a resource error.
func (s *Session) Serve(conn net.Conn) {
	defer conn.Close()
	defer s.Hub.UnsubscribeAll(s)
	w := wire.NewWriter(conn)
	s.w = w
	r := wire.NewReader(conn)
	if err := w.Untaggedf("OK imapd fixture service ready"); err != nil {
		return
	}
	for {
		cmd, err := r.ReadCommand(func() error { return w.Continuation("Ready for literal") })
		if err != nil {
			if errors.Is(err, io.EOF) {
				return
			}
			cat := errs.CategoryOf(err)
			s.logf("read failure category=%s err=%q", cat, err)
			if cat == errs.CatInput {
				// The bad line was fully consumed; the stream is still
				// framed, so reject and continue.
				w.Untaggedf("BAD %s", msgOf(err))
				continue
			}
			// Resource exhaustion and internal read failures end the
			// connection: we cannot trust framing from here on.
			w.Untaggedf("BYE %s", msgOf(err))
			return
		}
		s.logf("command tag=%s name=%s", cmd.Tag, cmd.Name)
		if s.dispatch(cmd) {
			return
		}
	}
}

// dispatch executes one command and emits its tagged response. It returns
// true when the session should close (LOGOUT).
func (s *Session) dispatch(cmd *wire.Command) (logout bool) {
	res := s.execute(cmd)
	if res.status != "" {
		if err := s.w.Tagged(cmd.Tag, res.status, res.text); err != nil {
			return true
		}
	}
	return res.logout
}

type result struct {
	status string // "OK", "NO", "BAD"
	text   string
	logout bool
}

func (s *Session) execute(cmd *wire.Command) (res result) {
	defer func() {
		// A panic is a computation failure: report an opaque NO, keep the
		// session alive, and log the detail server-side.
		if r := recover(); r != nil {
			s.logf("panic during %s: %v", cmd.Name, r)
			res = result{status: "NO", text: "internal error"}
		}
	}()
	var okText string
	var err error
	switch cmd.Name {
	case "CAPABILITY":
		s.w.Untaggedf("CAPABILITY IMAP4rev1")
		okText = "CAPABILITY completed"
	case "NOOP":
		okText = "NOOP completed"
	case "LOGOUT":
		s.w.Untaggedf("BYE imapd logging out")
		return result{status: "OK", text: "LOGOUT completed", logout: true}
	case "SELECT":
		okText, err = s.cmdSelect(cmd)
	case "FETCH":
		okText, err = s.cmdFetch(cmd, false)
	case "STORE":
		okText, err = s.cmdStore(cmd, false)
	case "EXPUNGE":
		okText, err = s.cmdExpunge(cmd)
	case "UID":
		okText, err = s.cmdUID(cmd)
	default:
		return result{status: "BAD", text: "unknown command " + cmd.Name}
	}
	if err != nil {
		s.logf("command tag=%s name=%s failed category=%s err=%q",
			cmd.Tag, cmd.Name, errs.CategoryOf(err), err)
		return errorResult(err)
	}
	return result{status: "OK", text: okText}
}

// errorResult maps the shared taxonomy onto IMAP statuses: input -> BAD,
// state/resource -> NO, internal -> opaque NO.
func errorResult(err error) result {
	switch errs.CategoryOf(err) {
	case errs.CatInput:
		return result{status: "BAD", text: msgOf(err)}
	case errs.CatState, errs.CatResource:
		return result{status: "NO", text: msgOf(err)}
	default:
		return result{status: "NO", text: "internal error"}
	}
}

// msgOf extracts the client-safe message of a classified error.
func msgOf(err error) string {
	var e *errs.Error
	if errors.As(err, &e) {
		return e.Msg
	}
	return "internal error"
}

func (s *Session) requireSelected() error {
	if s.mailbox == "" {
		return errs.New(errs.CatState, "proto.state", "no mailbox selected")
	}
	return nil
}

// --- SELECT ---

func (s *Session) cmdSelect(cmd *wire.Command) (string, error) {
	name, err := mailboxName(cmd.Args)
	if err != nil {
		return "", err
	}
	info, err := s.Store.Select(name)
	if err != nil {
		return "", err
	}
	if s.mailbox != "" {
		s.Hub.Unsubscribe(s.mailbox, s)
	}
	s.mailbox = name
	s.Hub.Subscribe(name, s)
	s.w.Untaggedf("%d EXISTS", info.Exists)
	s.w.Untaggedf("0 RECENT")
	s.w.Untaggedf("FLAGS (\\Deleted \\Seen)")
	s.w.Untaggedf("OK [UIDVALIDITY %d] UIDs valid", info.UIDValidity)
	s.w.Untaggedf("OK [UIDNEXT %d] predicted next UID", info.UIDNext)
	s.w.Untaggedf("OK [PERMANENTFLAGS (\\Deleted \\Seen)] flags permitted")
	return "[READ-WRITE] SELECT completed", nil
}

// mailboxName accepts an atom or a quoted string (no escape sequences —
// only declared forms). Literal-delivered names arrive as raw bytes and
// are treated as atoms; they may contain arbitrary binary.
func mailboxName(args []byte) (string, error) {
	if len(args) == 0 {
		return "", errs.New(errs.CatInput, "proto.select", "missing mailbox name")
	}
	var name string
	if args[0] == '"' {
		if len(args) < 2 || args[len(args)-1] != '"' {
			return "", errs.New(errs.CatInput, "proto.select", "unterminated quoted mailbox name")
		}
		inner := args[1 : len(args)-1]
		if strings.ContainsAny(string(inner), "\"\\") {
			return "", errs.New(errs.CatInput, "proto.select", "quoted escapes are not supported")
		}
		name = string(inner)
	} else {
		if strings.Contains(string(args), " ") {
			return "", errs.New(errs.CatInput, "proto.select", "mailbox name with spaces must be quoted")
		}
		name = string(args)
	}
	if name == "" {
		return "", errs.New(errs.CatInput, "proto.select", "empty mailbox name")
	}
	if len(name) > 255 {
		return "", errs.New(errs.CatInput, "proto.select", "mailbox name exceeds 255 bytes")
	}
	return name, nil
}

// --- FETCH ---

type fetchItemKind int

const (
	itemFlags fetchItemKind = iota
	itemUID
	itemSize
	itemBody
)

type fetchItem struct {
	kind fetchItemKind
	peek bool
}

// parseFetchItems accepts exactly the declared data items:
// FLAGS, UID, RFC822.SIZE, BODY[], BODY.PEEK[]. Everything else —
// including macros (ALL/FAST/FULL), ENVELOPE and BODY[section] — is
// rejected as an input error.
func parseFetchItems(s string) ([]fetchItem, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil, errs.New(errs.CatInput, "proto.fetch", "missing fetch items")
	}
	var fields []string
	if s[0] == '(' {
		if len(s) < 2 || s[len(s)-1] != ')' {
			return nil, errs.New(errs.CatInput, "proto.fetch", "unterminated fetch item list")
		}
		fields = strings.Fields(s[1 : len(s)-1])
	} else {
		fields = []string{s}
	}
	if len(fields) == 0 {
		return nil, errs.New(errs.CatInput, "proto.fetch", "empty fetch item list")
	}
	items := make([]fetchItem, 0, len(fields))
	for _, f := range fields {
		switch strings.ToUpper(f) {
		case "FLAGS":
			items = append(items, fetchItem{kind: itemFlags})
		case "UID":
			items = append(items, fetchItem{kind: itemUID})
		case "RFC822.SIZE":
			items = append(items, fetchItem{kind: itemSize})
		case "BODY[]":
			items = append(items, fetchItem{kind: itemBody})
		case "BODY.PEEK[]":
			items = append(items, fetchItem{kind: itemBody, peek: true})
		default:
			return nil, errs.New(errs.CatInput, "proto.fetch", "unsupported fetch item "+f)
		}
	}
	return items, nil
}

func (s *Session) cmdFetch(cmd *wire.Command, uidMode bool) (string, error) {
	if err := s.requireSelected(); err != nil {
		return "", err
	}
	seqPart, itemsPart, err := splitFirstToken(string(cmd.Args))
	if err != nil {
		return "", err
	}
	items, err := parseFetchItems(itemsPart)
	if err != nil {
		return "", err
	}
	msgs, err := s.Store.List(s.mailbox)
	if err != nil {
		return "", err
	}
	max := uint32(len(msgs))
	if uidMode {
		max = maxUID(msgs)
	}
	set, err := wire.ParseSeqSet(seqPart, max)
	if err != nil {
		return "", err
	}
	match := make(map[uint32]bool, len(set))
	for _, n := range set {
		if !uidMode && n > uint32(len(msgs)) {
			return "", errs.New(errs.CatState, "proto.fetch",
				fmt.Sprintf("sequence number %d out of range (EXISTS %d)", n, len(msgs)))
		}
		match[n] = true
	}
	uidRequested := false
	for _, it := range items {
		if it.kind == itemUID {
			uidRequested = true
		}
	}
	var markSeen []uint32
	for _, m := range msgs {
		key := m.Seq
		if uidMode {
			key = m.UID
		}
		if !match[key] {
			continue
		}
		parts := make([]wire.Part, 0, len(items)+1)
		for _, it := range items {
			switch it.kind {
			case itemFlags:
				parts = append(parts, wire.Part{Text: "FLAGS (" + strings.Join(m.Flags, " ") + ")"})
			case itemUID:
				parts = append(parts, wire.Part{Text: fmt.Sprintf("UID %d", m.UID)})
			case itemSize:
				parts = append(parts, wire.Part{Text: fmt.Sprintf("RFC822.SIZE %d", m.Size())})
			case itemBody:
				parts = append(parts, wire.Part{Text: "BODY[]", Literal: m.Content})
				if !it.peek {
					markSeen = append(markSeen, m.UID)
				}
			}
		}
		if uidMode && !uidRequested {
			// RFC 3501: UID FETCH responses always carry the UID.
			parts = append(parts, wire.Part{Text: fmt.Sprintf("UID %d", m.UID)})
		}
		if err := s.w.UntaggedFetch(m.Seq, parts); err != nil {
			return "", errs.Wrap(errs.CatInternal, "proto.fetch", err, "response write failed")
		}
	}
	if len(markSeen) > 0 {
		// Non-peek BODY[] implicitly sets \Seen (RFC 3501 §6.4.5).
		if _, err := s.Store.UpdateFlags(s.mailbox, markSeen, true, []string{"\\Seen"}); err != nil {
			return "", err
		}
	}
	if uidMode {
		return "UID FETCH completed", nil
	}
	return "FETCH completed", nil
}

// --- STORE (declared because the EXPUNGE model needs \\Deleted) ---

func (s *Session) cmdStore(cmd *wire.Command, uidMode bool) (string, error) {
	if err := s.requireSelected(); err != nil {
		return "", err
	}
	seqPart, rest, err := splitFirstToken(string(cmd.Args))
	if err != nil {
		return "", err
	}
	op, flagsPart, err := splitFirstToken(rest)
	if err != nil {
		return "", errs.New(errs.CatInput, "proto.store", "missing store operation")
	}
	var add, silent bool
	switch strings.ToUpper(op) {
	case "+FLAGS":
		add = true
	case "+FLAGS.SILENT":
		add, silent = true, true
	case "-FLAGS":
	case "-FLAGS.SILENT":
		silent = true
	default:
		return "", errs.New(errs.CatInput, "proto.store", "unsupported store operation "+op)
	}
	flags, err := parseFlagList(flagsPart)
	if err != nil {
		return "", err
	}
	msgs, err := s.Store.List(s.mailbox)
	if err != nil {
		return "", err
	}
	max := uint32(len(msgs))
	if uidMode {
		max = maxUID(msgs)
	}
	set, err := wire.ParseSeqSet(seqPart, max)
	if err != nil {
		return "", err
	}
	match := make(map[uint32]bool, len(set))
	for _, n := range set {
		if !uidMode && n > uint32(len(msgs)) {
			return "", errs.New(errs.CatState, "proto.store",
				fmt.Sprintf("sequence number %d out of range (EXISTS %d)", n, len(msgs)))
		}
		match[n] = true
	}
	var uids []uint32
	seqOf := make(map[uint32]uint32)
	for _, m := range msgs {
		key := m.Seq
		if uidMode {
			key = m.UID
		}
		if match[key] {
			uids = append(uids, m.UID)
			seqOf[m.UID] = m.Seq
		}
	}
	newFlags, err := s.Store.UpdateFlags(s.mailbox, uids, add, flags)
	if err != nil {
		return "", err
	}
	if !silent {
		for _, uid := range uids {
			fl, ok := newFlags[uid]
			if !ok {
				continue
			}
			if err := s.w.Untaggedf("%d FETCH (FLAGS (%s))", seqOf[uid], strings.Join(fl, " ")); err != nil {
				return "", errs.Wrap(errs.CatInternal, "proto.store", err, "response write failed")
			}
		}
	}
	if uidMode {
		return "UID STORE completed", nil
	}
	return "STORE completed", nil
}

// parseFlagList accepts "(\\Seen \\Deleted)" or a single bare flag, and
// only the declared flags \\Seen and \\Deleted.
func parseFlagList(s string) ([]string, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil, errs.New(errs.CatInput, "proto.store", "missing flag list")
	}
	var fields []string
	if s[0] == '(' {
		if len(s) < 2 || s[len(s)-1] != ')' {
			return nil, errs.New(errs.CatInput, "proto.store", "unterminated flag list")
		}
		fields = strings.Fields(s[1 : len(s)-1])
	} else {
		fields = []string{s}
	}
	if len(fields) == 0 {
		return nil, errs.New(errs.CatInput, "proto.store", "empty flag list")
	}
	for _, f := range fields {
		known := false
		for _, k := range store.KnownFlags {
			if f == k {
				known = true
			}
		}
		if !known {
			return nil, errs.New(errs.CatInput, "proto.store", "unsupported flag "+f)
		}
	}
	return fields, nil
}

// --- EXPUNGE ---

func (s *Session) cmdExpunge(cmd *wire.Command) (string, error) {
	if err := s.requireSelected(); err != nil {
		return "", err
	}
	// Hub.Expunge serialises the deletion and the fan-out of untagged
	// EXPUNGE events, so every observer of this mailbox — including this
	// session — sees the same events in the same order.
	_, err := s.Hub.Expunge(s.mailbox, func() ([]store.Event, error) {
		return s.Store.Expunge(s.mailbox)
	})
	if err != nil {
		return "", err
	}
	return "EXPUNGE completed", nil
}

// --- UID ---

func (s *Session) cmdUID(cmd *wire.Command) (string, error) {
	verb, rest, err := splitFirstToken(string(cmd.Args))
	if err != nil {
		return "", errs.New(errs.CatInput, "proto.uid", "missing UID subcommand")
	}
	sub := &wire.Command{Tag: cmd.Tag, Name: verb, Args: []byte(rest)}
	switch strings.ToUpper(verb) {
	case "FETCH":
		return s.cmdFetch(sub, true)
	case "STORE":
		return s.cmdStore(sub, true)
	default:
		return "", errs.New(errs.CatInput, "proto.uid", "unsupported UID subcommand "+verb)
	}
}

// --- helpers ---

func splitFirstToken(s string) (tok, rest string, err error) {
	s = strings.TrimLeft(s, " ")
	i := strings.IndexByte(s, ' ')
	if i < 0 {
		return "", "", errs.New(errs.CatInput, "proto.parse", "missing command arguments")
	}
	return s[:i], strings.TrimLeft(s[i+1:], " "), nil
}

func maxUID(msgs []store.Message) uint32 {
	if len(msgs) == 0 {
		return 0
	}
	return msgs[len(msgs)-1].UID
}

func (s *Session) logf(format string, args ...any) {
	if s.Log != nil {
		s.Log.Printf(format, args...)
	}
}
