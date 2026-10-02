package server

import (
	"context"
	"strings"

	"imaplite/internal/broker"
	"imaplite/internal/imapwire"
	"imaplite/internal/store"
)

// storeRequest is a parsed STORE command.
type storeRequest struct {
	silent bool
	mode   store.FlagMode
	flags  []string
}

func (s *session) cmdStore(ctx context.Context, cmd *imapwire.FramedCommand, byUID bool) error {
	if s.state != stateSelected {
		return wireState("STORE requires a selected mailbox")
	}
	if len(cmd.Args) != 3 {
		return wireBad("STORE expects <set> <operation> <flags>, got %d arguments", len(cmd.Args))
	}
	set, err := imapwire.ParseSeqSet(cmd.Args[0])
	if err != nil {
		return err
	}
	req, err := parseStoreOp(cmd.Args[1], cmd.Args[2])
	if err != nil {
		return err
	}
	sel, err := s.requireWritableSelected(ctx)
	if err != nil {
		return err
	}

	// Resolve the target set to IDs. The store maps sequence positions to
	// UIDs itself for the seq path; the UID path passes UIDs straight through.
	var ids []uint32
	if byUID {
		ids, err = set.ResolveUID(sel.uidNext - 1)
		if err != nil {
			return err
		}
	} else {
		seqs, err := set.ResolveSeq(sel.exists)
		if err != nil {
			return err
		}
		ids = make([]uint32, len(seqs))
		for i, n := range seqs {
			ids[i] = uint32(n)
		}
	}

	var changes []store.FlagChange
	_, err = s.srv.broker.Mutate(sel.name, sel.sub, func() ([]broker.Event, error) {
		cs, cerr := s.srv.store.SetFlags(ctx, sel.name, byUID, ids, req.mode, req.flags)
		if cerr != nil {
			return nil, wireCompute("store failed: %v", cerr)
		}
		changes = cs
		evs := make([]broker.Event, 0, len(changes))
		for _, c := range changes {
			evs = append(evs, broker.Event{
				Kind: broker.EvFlags, Seq: c.Seq, UID: c.UID, Flags: c.Flags,
			})
		}
		return evs, nil
	})
	if err != nil {
		return err
	}

	// The acting connection renders its own flag updates unless .SILENT.
	if !req.silent {
		for _, c := range changes {
			s.out.WriteFetch(c.Seq, []imapwire.FetchItem{{
				Name:  "FLAGS",
				Kind:  imapwire.KindFlagList,
				Flags: c.Flags,
			}})
		}
		s.out.Flush()
	}
	s.log.store(cmd.Tag, byUID, req.silent, len(changes))
	s.out.WriteTagged(cmd.Tag, imapwire.StatusOK, "", "store completed")
	return nil
}

// parseStoreOp parses the STORE operation atom and flag-value list.
func parseStoreOp(opTok, flagsTok imapwire.Token) (storeRequest, error) {
	if opTok.Kind != imapwire.TokAtom {
		return storeRequest{}, wireBad("STORE operation must be FLAGS, +FLAGS or -FLAGS")
	}
	op := strings.ToUpper(string(opTok.Raw))
	req := storeRequest{}
	if strings.HasSuffix(op, ".SILENT") {
		req.silent = true
		op = op[:len(op)-len(".SILENT")]
	}
	switch op {
	case "FLAGS":
		req.mode = store.FlagReplace
	case "+FLAGS":
		req.mode = store.FlagAdd
	case "-FLAGS":
		req.mode = store.FlagRemove
	default:
		return storeRequest{}, wireBad("unsupported STORE operation %q", string(opTok.Raw))
	}
	if flagsTok.Kind != imapwire.TokList {
		return storeRequest{}, wireBad("STORE flags must be a parenthesized list")
	}
	for _, t := range flagsTok.Item {
		if t.Kind != imapwire.TokAtom {
			return storeRequest{}, wireBad("flag entries must be atoms")
		}
		req.flags = append(req.flags, string(t.Raw))
	}
	return req, nil
}
