package server

import (
	"context"
	"errors"
	"fmt"

	"imaplite/internal/broker"
	"imaplite/internal/imapwire"
	"imaplite/internal/store"
)

// capabilities is the fixed, declared capability set. LITERAL+ is not offered:
// literals always synchronise with "+ go ahead", which the tests exercise.
var capabilities = []string{"IMAP4rev1", "UIDPLUS"}

// permanentFlags are client-persistent system flags the fixtures model.
var permanentFlags = []string{`\Answered`, `\Flagged`, `\Deleted`, `\Seen`, `\Draft`}

// route dispatches by state and verb. Unknown verbs are rejected explicitly;
// framing already consumed the whole command, so the connection stays aligned.
func (s *session) route(ctx context.Context, cmd *imapwire.FramedCommand) error {
	switch cmd.Verb {
	case "CAPABILITY":
		return s.cmdCapability(cmd)
	case "NOOP":
		return s.cmdNoop(cmd)
	case "LOGOUT":
		return s.cmdLogout(cmd)
	case "LOGIN":
		return s.cmdLogin(ctx, cmd)
	case "STARTTLS":
		return wireAuthUnsupported("STARTTLS is not enabled on this local service")
	case "AUTHENTICATE":
		return wireAuthUnsupported("use the LOGIN command; SASL AUTHENTICATE is not provided")
	case "SELECT", "EXAMINE":
		return s.cmdSelect(ctx, cmd)
	case "UNSELECT":
		return s.cmdUnselect(cmd)
	case "FETCH":
		return s.cmdFetch(ctx, cmd, false)
	case "STORE":
		return s.cmdStore(ctx, cmd, false)
	case "EXPUNGE":
		return s.cmdExpunge(ctx, cmd)
	case "UID":
		return s.cmdUID(ctx, cmd)
	case "CLOSE":
		return wireUnknown("CLOSE is not provided; EXPUNGE then UNSELECT explicitly")
	case "APPEND", "CREATE", "DELETE", "RENAME", "SUBSCRIBE", "UNSUBSCRIBE",
		"LIST", "LSUB", "STATUS", "SEARCH", "SORT", "CHECK", "ID",
		"NAMESPACE", "GETQUOTAROOT", "GETQUOTA", "ENABLE":
		return wireUnknown("%s is not implemented on this constrained query service", cmd.Verb)
	default:
		return wireUnknown("unknown command %q", cmd.Verb)
	}
}

func (s *session) cmdCapability(cmd *imapwire.FramedCommand) error {
	if len(cmd.Args) != 0 {
		return wireBad("CAPABILITY takes no arguments")
	}
	s.out.WriteCapability(capabilities)
	s.out.WriteTagged(cmd.Tag, imapwire.StatusOK, "CAPABILITY", "capabilities follow")
	return nil
}

func (s *session) cmdNoop(cmd *imapwire.FramedCommand) error {
	if len(cmd.Args) != 0 {
		return wireBad("NOOP takes no arguments")
	}
	s.out.WriteTagged(cmd.Tag, imapwire.StatusOK, "", "noop completed")
	return nil
}

func (s *session) cmdLogout(cmd *imapwire.FramedCommand) error {
	if len(cmd.Args) != 0 {
		return wireBad("LOGOUT takes no arguments")
	}
	s.closeSelected()
	s.out.WriteBye("logging out")
	s.out.WriteTagged(cmd.Tag, imapwire.StatusOK, "", "logout completed")
	s.state = stateLogout
	return nil
}

func (s *session) cmdLogin(ctx context.Context, cmd *imapwire.FramedCommand) error {
	if s.state != stateNotAuthenticated {
		return wireState("LOGIN received in authenticated state; already logged in")
	}
	if len(cmd.Args) != 2 {
		return wireBad("LOGIN expects username and password, got %d arguments", len(cmd.Args))
	}
	user := quoteToken(cmd.Args[0])
	pass := quoteToken(cmd.Args[1])
	if user == nil || pass == nil {
		return wireBad("LOGIN credentials must be strings")
	}
	u, err := s.srv.users.Login(user, pass)
	if err != nil {
		s.log.authFail(user)
		return wireAuth("invalid credentials")
	}
	s.user = u
	s.state = stateAuthenticated
	s.log.authOK(user)
	s.out.WriteTagged(cmd.Tag, imapwire.StatusOK, "CAPABILITY", "logged in")
	return nil
}

func (s *session) cmdSelect(ctx context.Context, cmd *imapwire.FramedCommand) error {
	if s.state == stateNotAuthenticated {
		return wireState("not authenticated")
	}
	if len(cmd.Args) != 1 {
		return wireBad("%s expects one mailbox name", cmd.Verb)
	}
	name := string(quoteToken(cmd.Args[0]))
	if name == "" {
		return wireBad("missing mailbox name")
	}
	if !s.srv.users.CanSelect(s.user, name) {
		return wirePermission("account may not SELECT %s", name)
	}
	info, err := s.srv.store.MailboxInfo(ctx, name)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			return wireStateCode("NONEXISTENT", "no such mailbox: %s (local fixtures only)", name)
		}
		return wireCompute("mailbox lookup failed: %v", err)
	}

	s.closeSelected()
	sel := snapshot(info)
	sel.sub = s.srv.broker.Subscribe(name)
	s.selected = sel
	s.state = stateSelected
	s.startPump(sel)
	s.log.selected(name, info)

	readOnly := cmd.Verb == "EXAMINE"
	sel.readOnly = readOnly
	s.out.WriteFlags(permanentFlags)
	s.out.WriteExists(info.Exists)
	s.out.WriteRecent(0)
	if info.FirstUnseen > 0 {
		seq, err := s.srv.store.SequenceOfUID(ctx, name, info.FirstUnseen)
		if err != nil {
			return wireCompute("unseen lookup failed: %v", err)
		}
		s.out.WriteUntaggedOK(fmt.Sprintf("UNSEEN %d", seq), "")
	}
	s.out.WriteUntaggedOK(fmt.Sprintf("UIDVALIDITY %d", info.UIDValidity), "")
	s.out.WriteUntaggedOK(fmt.Sprintf("UIDNEXT %d", info.UIDNext), "")
	s.out.WriteUntaggedOK(fmt.Sprintf("PERMANENTFLAGS (%s)", joinFlags(permanentFlags)), "")
	mode := "READ-WRITE"
	if readOnly {
		mode = "READ-ONLY"
	}
	s.out.WriteTaggedf(cmd.Tag, imapwire.StatusOK, mode, "%s selected", name)
	return nil
}

func (s *session) cmdUnselect(cmd *imapwire.FramedCommand) error {
	if s.state != stateSelected {
		return wireState("no mailbox selected")
	}
	s.closeSelected()
	s.state = stateAuthenticated
	s.out.WriteTagged(cmd.Tag, imapwire.StatusOK, "", "mailbox unselected")
	return nil
}

func (s *session) cmdUID(ctx context.Context, cmd *imapwire.FramedCommand) error {
	if s.state != stateSelected {
		return wireState("UID requires a selected mailbox")
	}
	if len(cmd.Args) < 1 || cmd.Args[0].Kind != imapwire.TokAtom {
		return wireBad("UID expects a sub-command (FETCH, STORE, EXPUNGE)")
	}
	sub := toUpper(string(cmd.Args[0].Raw))
	inner := &imapwire.FramedCommand{
		Tag:  cmd.Tag,
		Verb: sub,
		Args: cmd.Args[1:],
	}
	switch sub {
	case "FETCH":
		return s.cmdFetch(ctx, inner, true)
	case "STORE":
		return s.cmdStore(ctx, inner, true)
	case "EXPUNGE":
		return s.cmdUIDExpunge(ctx, inner)
	case "COPY", "MOVE", "SEARCH":
		return wireUnknown("UID %s is not implemented", sub)
	default:
		return wireUnknown("unknown UID sub-command %q", sub)
	}
}

func (s *session) cmdExpunge(ctx context.Context, cmd *imapwire.FramedCommand) error {
	if s.state != stateSelected {
		return wireState("EXPUNGE requires a selected mailbox")
	}
	if len(cmd.Args) != 0 {
		return wireBad("EXPUNGE takes no arguments")
	}
	return s.expunge(ctx, cmd.Tag, nil)
}

func (s *session) cmdUIDExpunge(ctx context.Context, cmd *imapwire.FramedCommand) error {
	if len(cmd.Args) != 1 {
		return wireBad("UID EXPUNGE requires a UID set")
	}
	set, err := imapwire.ParseSeqSet(cmd.Args[0])
	if err != nil {
		return err
	}
	uids, err := set.ResolveUID(s.selected.uidNext - 1)
	if err != nil {
		return err
	}
	return s.expunge(ctx, cmd.Tag, uids)
}

// expunge runs the serialised delete-and-renumber operation, publishes the
// ordered EXPUNGE/EXISTS events to observers, and renders them on the acting
// connection itself (it is excluded from fan-out as the event origin).
func (s *session) expunge(ctx context.Context, tag string, onlyUIDs []uint32) error {
	sel, err := s.requireWritableSelected(ctx)
	if err != nil {
		return err
	}
	var removed []store.Removed
	var exists int
	events, err := s.srv.broker.Mutate(sel.name, sel.sub, func() ([]broker.Event, error) {
		if onlyUIDs != nil {
			removed, exists, err = s.srv.store.ExpungeUID(ctx, sel.name, onlyUIDs)
		} else {
			removed, exists, err = s.srv.store.Expunge(ctx, sel.name)
		}
		if err != nil {
			return nil, wireCompute("expunge failed: %v", err)
		}
		evs := make([]broker.Event, 0, len(removed)+1)
		for _, r := range removed {
			evs = append(evs, broker.Event{Kind: broker.EvExpunge, Seq: r.Seq, UID: r.UID})
		}
		evs = append(evs, broker.Event{Kind: broker.EvExists, Exists: exists})
		return evs, nil
	})
	if err != nil {
		return err
	}
	// Acting connection renders its own events, in publish order.
	for _, ev := range events {
		switch ev.Kind {
		case broker.EvExpunge:
			s.out.WriteExpunge(ev.Seq)
			s.log.expunge(ev.Seq, ev.UID)
		case broker.EvExists:
			s.out.WriteExists(ev.Exists)
		}
	}
	sel.exists = exists
	s.out.Flush()
	scope := "expunged"
	if onlyUIDs != nil {
		scope = "uid expunged"
	}
	s.out.WriteTaggedf(tag, imapwire.StatusOK, "", "%s %d message(s)", scope, len(removed))
	return nil
}

// requireWritableSelected enforces selected state, epoch validity and the
// EXAMINE read-only restriction.
func (s *session) requireWritableSelected(ctx context.Context) (*selectedMailbox, error) {
	sel, err := s.checkEpoch(ctx)
	if err != nil {
		return nil, err
	}
	if sel.readOnly {
		return nil, wireStateCode("READ-ONLY", "mailbox is open read-only (EXAMINE)")
	}
	return sel, nil
}

func (s *session) closeSelected() {
	if s.selected == nil {
		return
	}
	if s.selected.stop != nil {
		close(s.selected.stop)
	}
	if s.selected.sub != nil {
		s.selected.sub.Unsubscribe()
	}
	s.selected = nil
}

func joinFlags(flags []string) string {
	out := ""
	for i, f := range flags {
		if i > 0 {
			out += " "
		}
		out += f
	}
	return out
}
