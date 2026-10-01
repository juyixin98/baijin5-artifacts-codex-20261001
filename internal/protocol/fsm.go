package protocol

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"strconv"
	"strings"
	"time"

	"smtpsink/internal/wire"
)

// Reply is a structured SMTP response produced by the state machine. The
// server renders it with wire.Reply. Close asks the server to hang up after
// flushing the reply (421 / 221); Silent closes without sending anything.
type Reply struct {
	Code   int
	Text   []string
	Close  bool
	Silent bool
}

// Policy decides which recipient mailboxes are local deliverable addresses.
type Policy struct {
	// LocalDomains lists domains (lowercase) this sink accepts mail for.
	LocalDomains []string
}

func (p Policy) allows(addr string) bool {
	_, domain, ok := strings.Cut(addr, "@")
	if !ok {
		return false
	}
	for _, d := range p.LocalDomains {
		if strings.EqualFold(domain, d) {
			return true
		}
	}
	return false
}

// Config configures a Session.
type Config struct {
	Hostname string
	Policy   Policy
	Limits   Limits
	Sink     Sink
	Logger   *slog.Logger
	Now      func() time.Time
	NewID    func() string
}

// Session is one SMTP connection's state machine.
type Session struct {
	cfg Config
	log *slog.Logger

	id string

	phase       Phase
	greeted     bool
	from        string
	rcpts       []string
	rcptSeen    map[string]struct{}
	messageSize int64

	messagesAccepted int
	commandsUsed     int
}

// NewSession creates a session with a generated request identifier.
func NewSession(cfg Config) *Session {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.NewID == nil {
		cfg.NewID = randomID
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	id := cfg.NewID()
	return &Session{
		cfg:      cfg,
		log:      cfg.Logger.With("session_id", id),
		id:       id,
		rcptSeen: make(map[string]struct{}),
	}
}

// ID returns the per-connection request identifier used in diagnostics.
func (s *Session) ID() string { return s.id }

// Phase exposes the current command phase.
func (s *Session) Phase() Phase { return s.phase }

// From and Rcpts report the in-flight transaction (test/diagnostic access).
func (s *Session) From() string    { return s.from }
func (s *Session) Rcpts() []string { return append([]string(nil), s.rcpts...) }

// Greeting is sent once on connect.
func (s *Session) Greeting() Reply {
	s.phase = PhaseInit
	return Reply{Code: 220, Text: []string{fmt.Sprintf("%s SMTP sink ready", s.cfg.Hostname)}}
}

// Feed processes one decoded command line and returns its reply.
func (s *Session) Feed(ctx context.Context, line string) Reply {
	if line == "" {
		// RFC 5321: an empty command line is ignored (no state change).
		return Reply{Code: 250, Text: []string{"OK"}}
	}
	s.commandsUsed++
	if s.commandsUsed > s.cfg.Limits.CommandsPerConn {
		s.log.Warn("command budget exhausted, closing",
			"state", s.stateName(), "commands", s.commandsUsed)
		return Reply{Code: 421, Text: []string{"too many commands, closing connection"}, Close: true}
	}

	cmd, arg := splitCommand(line)
	switch strings.ToUpper(cmd) {
	case "EHLO", "HELO":
		return s.handleGreet(cmd, arg)
	case "MAIL":
		return s.handleMail(stripKeyword(arg, "FROM:"))
	case "RCPT":
		return s.handleRcpt(stripKeyword(arg, "TO:"))
	case "DATA":
		return s.handleDataStart(arg)
	case "RSET":
		return s.handleRset()
	case "NOOP":
		return Reply{Code: 250, Text: []string{"OK"}}
	case "QUIT":
		return Reply{Code: 221, Text: []string{"bye"}, Close: true}
	default:
		s.log.Info("unsupported command rejected", "cmd", cmd, "state", s.stateName())
		return Reply{Code: 502, Text: []string{"command not implemented locally"}}
	}
}

func (s *Session) handleGreet(cmd, arg string) Reply {
	arg = strings.TrimSpace(arg)
	if arg == "" {
		return Reply{Code: 501, Text: []string{"domain/address required"}}
	}
	// A repeat EHLO/HELO aborts any in-flight transaction (RFC 5321 3.3).
	s.resetTransaction()
	s.greeted = true
	s.phase = PhaseReady
	s.log.Info("greeting accepted", "verb", cmd, "client", RedactToken(arg))
	if strings.ToUpper(cmd) == "EHLO" {
		return Reply{Code: 250, Text: []string{
			fmt.Sprintf("%s greets %s", s.cfg.Hostname, arg),
			"SIZE " + strconv.Itoa(s.cfg.Limits.MessageBytes),
			"8BITMIME",
		}}
	}
	return Reply{Code: 250, Text: []string{fmt.Sprintf("%s greets %s", s.cfg.Hostname, arg)}}
}

func (s *Session) handleMail(arg string) Reply {
	if !s.greeted {
		return Reply{Code: 503, Text: []string{"send EHLO first"}}
	}
	if s.phase != PhaseReady {
		s.log.Warn("MAIL out of sequence", "state", s.stateName())
		return Reply{Code: 503, Text: []string{"nested MAIL command"}}
	}
	if s.messagesAccepted >= s.cfg.Limits.MessagesPerConn {
		return Reply{Code: 421, Text: []string{"per-connection message limit reached"}, Close: true}
	}
	path, params, err := ParseReversePath(arg)
	if err != nil {
		return Reply{Code: 501, Text: []string{"bad reverse-path syntax: " + err.Error()}}
	}
	if size, ok := params["SIZE"]; ok {
		n, err := strconv.Atoi(size)
		if err != nil || n < 0 {
			return Reply{Code: 501, Text: []string{"bad SIZE parameter"}}
		}
		if n > s.cfg.Limits.MessageBytes {
			s.log.Info("message rejected by declared SIZE", "declared", n)
			return Reply{Code: 552, Text: []string{"message exceeds fixed size limit"}}
		}
	}
	s.from = path
	s.phase = PhaseMail
	s.log.Info("reverse-path accepted", "from", RedactAddress(path), "state", s.stateName())
	return Reply{Code: 250, Text: []string{"sender OK"}}
}

func (s *Session) handleRcpt(arg string) Reply {
	if s.phase != PhaseMail && s.phase != PhaseRcpt {
		return Reply{Code: 503, Text: []string{"need MAIL command first"}}
	}
	path, _, err := ParseForwardPath(arg)
	if err != nil {
		return Reply{Code: 501, Text: []string{"bad forward-path syntax: " + err.Error()}}
	}
	if _, dup := s.rcptSeen[path]; dup {
		// Idempotent acceptance; delivery is still exactly one copy.
		s.log.Info("duplicate recipient acknowledged once", "rcpt", RedactAddress(path))
		return Reply{Code: 250, Text: []string{"recipient OK (already listed)"}}
	}
	if !s.cfg.Policy.allows(path) {
		s.log.Info("recipient rejected: not local", "rcpt", RedactAddress(path))
		return Reply{Code: 550, Text: []string{"mailbox unavailable: not a local domain"}}
	}
	if len(s.rcpts) >= s.cfg.Limits.Recipients {
		return Reply{Code: 452, Text: []string{"too many recipients"}}
	}
	s.rcptSeen[path] = struct{}{}
	s.rcpts = append(s.rcpts, path)
	s.phase = PhaseRcpt
	s.log.Info("forward-path accepted", "rcpt", RedactAddress(path),
		"accepted_count", len(s.rcpts), "state", s.stateName())
	return Reply{Code: 250, Text: []string{"recipient OK"}}
}

func (s *Session) handleDataStart(arg string) Reply {
	if strings.TrimSpace(arg) != "" {
		return Reply{Code: 501, Text: []string{"DATA takes no argument"}}
	}
	if s.phase == PhaseMail {
		return Reply{Code: 503, Text: []string{"need RCPT command first"}}
	}
	if s.phase != PhaseRcpt {
		return Reply{Code: 503, Text: []string{"need MAIL and RCPT first"}}
	}
	s.phase = PhaseData
	return Reply{Code: 354, Text: []string{"send message content, end with ."}}
}

func (s *Session) handleRset() Reply {
	s.resetTransaction()
	return Reply{Code: 250, Text: []string{"OK"}}
}

func (s *Session) resetTransaction() {
	s.from = ""
	s.rcpts = s.rcpts[:0]
	s.rcptSeen = make(map[string]struct{})
	s.messageSize = 0
	if s.greeted {
		s.phase = PhaseReady
	} else {
		s.phase = PhaseInit
	}
}

// HandleData collects DATA content through the dot-terminator using r, which
// must be the connection's wire reader (its limit is switched to the DATA
// line limit and restored on exit), then performs the durable delivery.
func (s *Session) HandleData(ctx context.Context, r *wire.Reader) Reply {
	r.SetLimit(s.cfg.Limits.DataLineBytes)
	defer r.SetLimit(s.cfg.Limits.CommandLineBytes)

	var body strings.Builder
	var overload bool
	var overloadCode int
	var overloadText string

	dr := wire.NewDataLineReader(r)
	for {
		line, end, err := dr.ReadLine()
		if err != nil {
			return s.handleDataReadError(err)
		}
		if end {
			break
		}
		if !overload {
			added := int64(len(line) + 2)
			if s.messageSize+added > int64(s.cfg.Limits.MessageBytes) {
				overload = true
				overloadCode = 552
				overloadText = "message exceeds fixed size limit"
			}
		}
		if !overload {
			body.WriteString(line)
			body.WriteString("\r\n")
			s.messageSize += int64(len(line) + 2)
		}
	}

	if overload {
		s.log.Warn("message rejected: over limit, content discarded",
			"state", s.stateName(), "rcpts", len(s.rcpts))
		s.resetTransaction()
		return Reply{Code: overloadCode, Text: []string{overloadText}}
	}

	msg := Message{
		ID:         s.cfg.NewID(),
		From:       s.from,
		Recipients: append([]string(nil), s.rcpts...),
		Data:       []byte(body.String()),
		ReceivedAt: s.cfg.Now(),
	}

	// Durability rule: no 250 before the sink reports all copies durable.
	receipt, err := s.cfg.Sink.Deliver(ctx, msg)
	if err != nil {
		return s.deliveryFailure(err, msg)
	}
	s.logDeliverySuccess(msg, receipt)
	s.messagesAccepted++
	s.resetTransaction()
	return Reply{Code: 250, Text: []string{"message accepted as " + msg.ID}}
}

func (s *Session) handleDataReadError(err error) Reply {
	switch {
	case errors.Is(err, wire.ErrLineTooLong):
		s.log.Warn("over-long DATA line; draining then failing transaction",
			"state", s.stateName())
		s.resetTransaction()
		return Reply{Code: 554, Text: []string{"message line too long, transaction failed"}}
	case errors.Is(err, wire.ErrFatalOverflow):
		s.log.Error("DATA framing unrecoverable, closing", "state", s.stateName())
		return Reply{Code: 421, Text: []string{"framing error, closing connection"}, Close: true}
	case errors.Is(err, io.EOF), errors.Is(err, io.ErrUnexpectedEOF):
		// Client vanished mid-DATA: nothing was delivered and there is no one
		// to reply to.
		s.log.Warn("connection aborted during DATA, no delivery",
			"state", s.stateName(), "collected", s.messageSize)
		return Reply{Silent: true, Close: true}
	default:
		s.log.Error("DATA read error, closing", "err", err.Error())
		return Reply{Code: 421, Text: []string{"read error, closing connection"}, Close: true}
	}
}

func (s *Session) deliveryFailure(err error, msg Message) Reply {
	de := AsDeliveryError(err)
	switch de.Kind {
	case KindInsufficient:
		s.log.Error("delivery refused: insufficient storage",
			"message_id", msg.ID, "rcpt_count", len(msg.Recipients), "reason", de.Why)
		s.resetTransaction()
		return Reply{Code: 452, Text: []string{"insufficient system storage, please retry later"}}
	default:
		// Never echo the sink's internal error text to the peer.
		s.log.Error("delivery failed temporarily, no copy committed",
			"message_id", msg.ID, "rcpt_count", len(msg.Recipients),
			"reason", de.Why, "err", safeErr(de.Err))
		s.resetTransaction()
		return Reply{Code: 451, Text: []string{"local error, please retry later"}}
	}
}

func (s *Session) logDeliverySuccess(msg Message, receipt Receipt) {
	rcpts := make([]string, 0, len(receipt.DeliveredTo))
	for _, a := range receipt.DeliveredTo {
		rcpts = append(rcpts, RedactAddress(a))
	}
	s.log.Info("message delivered durably",
		"message_id", msg.ID, "from", RedactAddress(msg.From),
		"delivered", rcpts, "bytes", len(msg.Data))
}

func (s *Session) stateName() string {
	switch s.phase {
	case PhaseInit:
		return "init"
	case PhaseReady:
		return "ready"
	case PhaseMail:
		return "mail"
	case PhaseRcpt:
		return "rcpt"
	case PhaseData:
		return "data"
	default:
		return "unknown"
	}
}

func splitCommand(line string) (cmd, arg string) {
	if sp := strings.IndexAny(line, " \t"); sp >= 0 {
		return line[:sp], strings.TrimSpace(line[sp:])
	}
	return line, ""
}

// stripKeyword removes a required case-insensitive keyword such as "FROM:" or
// "TO:" that introduces a path argument. A missing keyword yields the original
// argument untouched so the parser rejects it as a syntax error.
func stripKeyword(arg, keyword string) string {
	if len(arg) < len(keyword) {
		return arg
	}
	if strings.EqualFold(arg[:len(keyword)], keyword) {
		return strings.TrimSpace(arg[len(keyword):])
	}
	return arg
}

func safeErr(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func randomID() string {
	var b [12]byte
	if _, err := rand.Read(b[:]); err != nil {
		// rand.Read failing is not recoverable in a sane runtime; fall back to
		// a time-derived id so callers still get a unique-enough value.
		return fmt.Sprintf("t%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b[:])
}
