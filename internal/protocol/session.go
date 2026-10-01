package protocol

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"log"
	"strings"

	"smtpsink/internal/lineio"
	"smtpsink/internal/storage"
)

// State is the session position in the SMTP command sequence.
type State int

const (
	StateConnected State = iota // greeting sent, awaiting EHLO/HELO
	StateReady                  // greeted, no transaction in progress
	StateMail                   // MAIL accepted, awaiting RCPT
	StateRcpt                   // at least one RCPT accepted
)

func (s State) String() string {
	switch s {
	case StateConnected:
		return "connected"
	case StateReady:
		return "ready"
	case StateMail:
		return "mail"
	case StateRcpt:
		return "rcpt"
	}
	return "unknown"
}

// Limits bound a single session.
type Limits struct {
	MaxLineBytes    int   // per command line
	MaxMessageBytes int64 // per DATA body after dot-unstuffing
	MaxRecipients   int   // accepted RCPT commands per transaction
}

// Session serves one SMTP connection. It is safe to run many sessions
// concurrently; each owns its connection and transaction state.
type Session struct {
	ID       string          // correlates log lines for this connection
	Hostname string          // used in greeting and EHLO replies
	Domains  map[string]bool // lower-cased local domains accepted at RCPT
	Limits   Limits
	Store    storage.Store
	Logger   *log.Logger // may be nil; logging is then disabled

	state    State
	mailFrom string
	rcpts    []string
}

// transaction resets the per-message state (RSET, EHLO, completed DATA).
func (s *Session) reset() {
	s.mailFrom = ""
	s.rcpts = nil
}

func (s *Session) logf(format string, args ...any) {
	if s.Logger != nil {
		s.Logger.Printf("sid=%s "+format, append([]any{s.ID}, args...)...)
	}
}

type replyFunc func(format string, args ...any) error

// Serve runs the session to completion: QUIT, a read/write failure, or a
// protocol condition that cannot be resynced. A nil error means the
// session ended cleanly.
func (s *Session) Serve(rw io.ReadWriter, remote string) error {
	rd := lineio.NewReader(rw, s.Limits.MaxLineBytes)
	w := bufio.NewWriter(rw)
	reply := func(format string, args ...any) error {
		if _, err := fmt.Fprintf(w, format+"\r\n", args...); err != nil {
			return err
		}
		return w.Flush()
	}

	s.state = StateConnected
	s.logf("state=%s event=greet remote=%s", s.state, remote)
	if err := reply("220 %s Service ready", s.Hostname); err != nil {
		return err
	}

	for {
		line, err := rd.ReadLine()
		if errors.Is(err, lineio.ErrLineTooLong) {
			s.logf("state=%s decision=reject reason=line-too-long limit=%d", s.state, s.Limits.MaxLineBytes)
			if rerr := reply("500 5.5.2 Line too long"); rerr != nil {
				return rerr
			}
			continue
		}
		if err != nil {
			// Client went away (io.EOF) or the connection failed.
			s.logf("state=%s event=disconnect reason=%v", s.state, err)
			return err
		}

		verb, arg := parseCommand(line)
		s.logf("state=%s cmd=%s", s.state, verb)

		switch verb {
		case "EHLO", "HELO":
			if arg == "" {
				if err := reply("501 5.5.4 %s requires domain argument", verb); err != nil {
					return err
				}
				continue
			}
			s.reset()
			s.state = StateReady
			if verb == "EHLO" {
				if err := reply("250-%s greets %s\r\n250-SIZE %d\r\n250 8BITMIME",
					s.Hostname, arg, s.Limits.MaxMessageBytes); err != nil {
					return err
				}
			} else if err := reply("250 %s greets %s", s.Hostname, arg); err != nil {
				return err
			}

		case "MAIL":
			if s.state != StateReady {
				s.logf("state=%s decision=reject cmd=MAIL reason=bad-sequence", s.state)
				if err := reply("503 5.5.1 Bad sequence of commands"); err != nil {
					return err
				}
				continue
			}
			from, perr := parsePath(arg, "FROM:")
			if perr != nil {
				if err := reply("501 5.5.4 Malformed MAIL argument"); err != nil {
					return err
				}
				continue
			}
			if from != "" {
				if _, _, aerr := splitAddress(from); aerr != nil {
					if err := reply("501 5.1.7 Malformed sender address"); err != nil {
						return err
					}
					continue
				}
			}
			s.reset()
			s.mailFrom = from
			s.state = StateMail
			s.logf("state=%s decision=accept cmd=MAIL from=%s", s.state, MaskAddress(from))
			if err := reply("250 2.1.0 Sender OK"); err != nil {
				return err
			}

		case "RCPT":
			if s.state != StateMail && s.state != StateRcpt {
				s.logf("state=%s decision=reject cmd=RCPT reason=bad-sequence", s.state)
				if err := reply("503 5.5.1 Bad sequence of commands"); err != nil {
					return err
				}
				continue
			}
			to, perr := parsePath(arg, "TO:")
			if perr != nil {
				if err := reply("501 5.5.4 Malformed RCPT argument"); err != nil {
					return err
				}
				continue
			}
			_, domain, aerr := splitAddress(to)
			if aerr != nil {
				if err := reply("501 5.1.3 Malformed recipient address"); err != nil {
					return err
				}
				continue
			}
			if !s.Domains[domain] {
				s.logf("state=%s decision=reject cmd=RCPT to=%s reason=non-local-domain", s.state, MaskAddress(to))
				if err := reply("550 5.7.1 Relay denied: not a local domain"); err != nil {
					return err
				}
				continue
			}
			if len(s.rcpts) >= s.Limits.MaxRecipients {
				s.logf("state=%s decision=reject cmd=RCPT reason=too-many-recipients limit=%d", s.state, s.Limits.MaxRecipients)
				if err := reply("452 4.5.3 Too many recipients"); err != nil {
					return err
				}
				continue
			}
			s.rcpts = append(s.rcpts, to)
			s.state = StateRcpt
			s.logf("state=%s decision=accept cmd=RCPT to=%s accepted=%d", s.state, MaskAddress(to), len(s.rcpts))
			if err := reply("250 2.1.5 Recipient OK"); err != nil {
				return err
			}

		case "DATA":
			if s.state != StateRcpt {
				s.logf("state=%s decision=reject cmd=DATA reason=bad-sequence", s.state)
				if err := reply("503 5.5.1 Bad sequence of commands"); err != nil {
					return err
				}
				continue
			}
			if err := s.receiveData(rd, reply); err != nil {
				return err
			}
			s.reset()
			s.state = StateReady

		case "RSET":
			s.reset()
			if s.state != StateConnected {
				s.state = StateReady
			}
			s.logf("state=%s decision=reset cmd=RSET", s.state)
			if err := reply("250 2.0.0 Reset state"); err != nil {
				return err
			}

		case "NOOP":
			if err := reply("250 2.0.0 OK"); err != nil {
				return err
			}

		case "VRFY":
			if err := reply("252 2.1.5 Cannot VRFY user, send some mail and see"); err != nil {
				return err
			}

		case "QUIT":
			if err := reply("221 2.0.0 %s closing connection", s.Hostname); err != nil {
				return err
			}
			s.logf("state=%s event=quit", s.state)
			return nil

		case "":
			if err := reply("500 5.5.2 Command unrecognized"); err != nil {
				return err
			}

		default:
			if err := reply("500 5.5.2 Command unrecognized"); err != nil {
				return err
			}
		}
	}
}

// errTooLarge is returned by the capped writer when the decoded DATA body
// exceeds Limits.MaxMessageBytes.
var errTooLarge = errors.New("protocol: message exceeds size limit")

// receiveData streams one DATA body to the store. The 250 acceptance is
// sent only after the store reports the message durable; any failure
// yields a 4xx/5xx reply and no acceptance.
func (s *Session) receiveData(rd *lineio.Reader, reply replyFunc) error {
	pend, err := s.Store.Begin(storage.Envelope{MailFrom: s.mailFrom, RcptTo: s.rcpts})
	if err != nil {
		s.logf("state=%s decision=tempfail cmd=DATA reason=store-begin-failed err=%q", s.state, err)
		return reply("451 4.3.0 Local storage error, try again later")
	}
	if err := reply("354 End data with <CR><LF>.<CR><LF>"); err != nil {
		pend.Abort()
		return err
	}

	dr := lineio.NewDotReader(rd.Buffered())
	cw := &cappedWriter{w: pend, limit: s.Limits.MaxMessageBytes}
	_, copyErr := io.Copy(cw, dr)

	switch {
	case copyErr == nil:
		// Clean terminator: body fully received.
	case errors.Is(copyErr, errTooLarge):
		// Drain to the terminator so the stream resyncs, then reject.
		if _, derr := io.Copy(io.Discard, dr); derr != nil {
			pend.Abort()
			s.logf("state=%s event=disconnect phase=data-drain reason=%v", s.state, derr)
			return derr
		}
		pend.Abort()
		s.logf("state=%s decision=reject cmd=DATA reason=message-too-large limit=%d", s.state, s.Limits.MaxMessageBytes)
		return reply("552 5.3.4 Message exceeds maximum size of %d bytes", s.Limits.MaxMessageBytes)
	default:
		// Connection dropped mid-body or malformed dot sequence: the
		// stream cannot be trusted, abort and hang up without a reply.
		pend.Abort()
		s.logf("state=%s event=abort phase=data reason=%v", s.state, copyErr)
		return copyErr
	}

	id, err := pend.Commit()
	if err != nil {
		s.logf("state=%s decision=tempfail cmd=DATA reason=commit-failed err=%q", s.state, err)
		return reply("451 4.3.0 Local storage error, message not accepted")
	}
	s.logf("state=%s decision=accept cmd=DATA id=%s from=%s rcpts=%d",
		s.state, id, MaskAddress(s.mailFrom), len(s.rcpts))
	return reply("250 2.0.0 Queued as %s", id)
}

// cappedWriter fails with errTooLarge once more than limit bytes have
// been written.
type cappedWriter struct {
	w     io.Writer
	limit int64
	n     int64
}

func (c *cappedWriter) Write(p []byte) (int, error) {
	if c.n+int64(len(p)) > c.limit {
		// Write only up to the cap so the spool never holds more than
		// the limit, then report the overflow.
		remain := c.limit - c.n
		if remain > 0 {
			n, err := c.w.Write(p[:remain])
			c.n += int64(n)
			if err != nil {
				return n, err
			}
		}
		return 0, errTooLarge
	}
	n, err := c.w.Write(p)
	c.n += int64(n)
	return n, err
}

// DomainsFromList builds the lookup set expected by Session.Domains.
func DomainsFromList(domains []string) map[string]bool {
	set := make(map[string]bool, len(domains))
	for _, d := range domains {
		set[strings.ToLower(strings.TrimSpace(d))] = true
	}
	return set
}
