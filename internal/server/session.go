package server

import (
	"context"
	"errors"
	"io"
	"net"
	"sync/atomic"

	"imaplite/internal/auth"
	"imaplite/internal/broker"
	"imaplite/internal/imapwire"
)

// connState is the IMAP session state.
type connState int

const (
	stateNotAuthenticated connState = iota
	stateAuthenticated
	stateSelected
	stateLogout
)

// session is one TCP connection's state machine.
type session struct {
	srv  *Server
	conn net.Conn
	out  *imapwire.Conn
	fr   *imapwire.Framer
	log  sessionLogger

	id   int64
	user auth.User

	state    connState
	selected *selectedMailbox
}

var connCounter atomic.Int64

func newSession(s *Server, c net.Conn) *session {
	id := connCounter.Add(1)
	out := imapwire.NewConn(c)
	sess := &session{
		srv:  s,
		conn: c,
		out:  out,
		fr:   imapwire.NewFramer(c, out),
		id:   id,
	}
	sess.fr.MaxLiteral = s.limits.MaxLiteral
	sess.fr.MaxLine = s.limits.MaxLine
	sess.log = newLogger(s.log, id)
	return sess
}

func (s *session) run(ctx context.Context) {
	defer quietClose(s.conn)
	s.out.WriteOK("IMAPlite ready (synthetic local service)")

	for s.state != stateLogout {
		if ctx.Err() != nil {
			s.out.WriteBye("server shutting down")
			return
		}
		cmd, err := s.fr.Frame()
		if err != nil {
			s.handleFrameError(err)
			return
		}
		s.dispatch(ctx, cmd)
	}
}

func (s *session) handleFrameError(err error) {
	if errors.Is(err, io.EOF) {
		s.log.peerClosed()
		return
	}
	if imapwire.IsIOError(err) || errors.Is(err, io.ErrUnexpectedEOF) {
		s.log.peerClosed()
		return
	}
	// A framing error after the tag would normally be tagged; by the time
	// Frame fails the tag may be only partially read, so a pre-tag BAD plus
	// disconnect is the unambiguous, stream-safe response.
	if we := asWireError(err); we != nil {
		run := s.fr.RunNo()
		s.log.frameError(run, we)
		// TOOBIG on a literal cannot be recovered: the payload bytes are
		// unread. Tell the client and drop the connection.
		if we.Class == imapwire.ClassResource {
			s.out.WriteTagged("*", imapwire.StatusBAD, we.Code, we.Msg)
			s.out.WriteBye("connection closed after unrecoverable framing error")
			return
		}
		s.out.WriteTagged("*", imapwire.StatusBAD, codeOf(we), we.Msg)
		s.out.WriteBye("connection closed after framing error")
		return
	}
	s.log.internalError(err)
	s.out.WriteBye("internal error")
}

// dispatch routes one fully framed command and maps every typed error to a
// tagged status. It never panics out of the serving loop.
func (s *session) dispatch(ctx context.Context, cmd *imapwire.FramedCommand) {
	s.log.commandStart(cmd.Tag, cmd.Verb, s.stateName())
	err := s.route(ctx, cmd)
	if err == nil {
		return
	}
	s.fail(cmd.Tag, err)
}

func (s *session) fail(tag string, err error) {
	we := asWireError(err)
	status, code := imapwire.StatusBAD, ""
	text := err.Error()
	if we != nil {
		text = we.Msg
		code = codeOf(we)
		status = statusFor(we)
	} else if errors.Is(err, errInternal) {
		status, code = imapwire.StatusNO, "SERVERBUG"
	} else {
		// Store/compute failures that are not typed: treat as compute.
		status, code = imapwire.StatusNO, "SERVERBUG"
	}
	s.log.commandFail(tag, status, code, text, classify(err))
	s.out.WriteTagged(tag, status, code, text)
}

// statusFor maps a wire error class (and its response code) onto an IMAP
// completion status:
//   - malformed/unknown/unauthorised-in-state → BAD
//   - state conflicts that carry a declared code (UIDVALIDITY, READ-ONLY,
//     NONEXISTENT), auth and resource failures → NO
func statusFor(we *imapwire.Error) imapwire.Status {
	switch we.Class {
	case imapwire.ClassInput, imapwire.ClassUnknownCommand,
		imapwire.ClassUnsupportedItem, imapwire.ClassAuthUnsupported:
		return imapwire.StatusBAD
	case imapwire.ClassState:
		if we.Code == "" {
			return imapwire.StatusBAD
		}
		return imapwire.StatusNO
	case imapwire.ClassAuth, imapwire.ClassPermission,
		imapwire.ClassResource, imapwire.ClassCompute:
		return imapwire.StatusNO
	}
	return imapwire.StatusBAD
}

func codeOf(we *imapwire.Error) string {
	if we.Code != "" {
		return we.Code
	}
	switch we.Class {
	case imapwire.ClassAuth:
		return "AUTHENTICATIONFAILED"
	case imapwire.ClassPermission:
		return "PERMISSION"
	case imapwire.ClassCompute:
		return "SERVERBUG"
	case imapwire.ClassResource:
		return "TOOBIG"
	}
	// ClassState without an explicit code is a plain BAD (wrong session
	// state); it must not carry a misleading SERVERBUG code.
	return ""
}

func classify(err error) string {
	if we := asWireError(err); we != nil {
		return string(we.Class)
	}
	return string(imapwire.ClassCompute)
}

func (s *session) stateName() string {
	switch s.state {
	case stateNotAuthenticated:
		return "not-auth"
	case stateAuthenticated:
		return "auth"
	case stateSelected:
		return "selected:" + s.selected.name
	}
	return "logout"
}

var errInternal = errors.New("internal computation failure")

var _ = broker.EvExpunge
