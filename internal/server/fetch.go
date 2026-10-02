package server

import (
	"context"
	"strings"

	"imaplite/internal/imapwire"
	"imaplite/internal/mimeparse"
	"imaplite/internal/store"
)

// fetchRequest is the parsed data-item group for one FETCH.
type fetchRequest struct {
	items []fetchItemSpec
}

type fetchItemSpec struct {
	name        string // canonical wire item name
	bracketText string // text inside BODY[..], canonical case
	kind        fetchKind
	section     *mimeparse.SectionSpec // BODY[..]
	partial     *mimeparse.Partial
}

type fetchKind int

const (
	fkUID fetchKind = iota
	fkFlags
	fkInternalDate
	fkRFC822Size
	fkEnvelope
	fkBodyStructure
	fkBody
	fkSection // BODY[..] / RFC822(.HEADER/.TEXT)
)

func (s *session) cmdFetch(ctx context.Context, cmd *imapwire.FramedCommand, byUID bool) error {
	if s.state != stateSelected {
		return wireState("FETCH requires a selected mailbox")
	}
	if len(cmd.Args) != 2 {
		return wireBad("FETCH expects <sequence set> <data items>, got %d arguments", len(cmd.Args))
	}
	set, err := imapwire.ParseSeqSet(cmd.Args[0])
	if err != nil {
		return err
	}
	req, err := parseFetchItems(cmd.Args[1])
	if err != nil {
		return err
	}
	sel, err := s.checkEpoch(ctx)
	if err != nil {
		return err
	}

	var msgs []store.Message
	var seqOf = map[uint32]int{}
	if byUID {
		uids, err := set.ResolveUID(sel.uidNext - 1)
		if err != nil {
			return err
		}
		msgs, err = s.srv.store.FetchByUID(ctx, sel.name, uids)
		if err != nil {
			return wireCompute("fetch failed: %v", err)
		}
	} else {
		seqs, err := set.ResolveSeq(sel.exists)
		if err != nil {
			return err
		}
		msgs, err = s.srv.store.FetchBySeq(ctx, sel.name, seqs)
		if err != nil {
			return wireCompute("fetch failed: %v", err)
		}
	}
	for _, m := range msgs {
		pos, err := s.srv.store.SequenceOfUID(ctx, sel.name, m.UID)
		if err != nil {
			return wireCompute("sequence lookup failed: %v", err)
		}
		seqOf[m.UID] = pos
	}

	for _, m := range msgs {
		items, err := renderFetchItems(m, req, byUID)
		if err != nil {
			return err
		}
		s.out.WriteFetch(seqOf[m.UID], items)
	}
	s.out.Flush()
	s.log.fetch(cmd.Tag, byUID, len(msgs), req.itemNames())
	s.out.WriteTagged(cmd.Tag, imapwire.StatusOK, "", "fetch completed")
	return nil
}

func parseFetchItems(tok imapwire.Token) (fetchRequest, error) {
	var raw []imapwire.Token
	switch tok.Kind {
	case imapwire.TokList:
		raw = tok.Item
	case imapwire.TokAtom:
		raw = []imapwire.Token{tok}
	default:
		return fetchRequest{}, wireBad("FETCH data items must be an atom or a parenthesized list")
	}
	req := fetchRequest{}
	for _, t := range raw {
		if t.Kind != imapwire.TokAtom {
			return fetchRequest{}, wireUnsupported("unsupported FETCH data item token")
		}
		spec, err := parseFetchItem(string(t.Raw))
		if err != nil {
			return fetchRequest{}, err
		}
		req.items = append(req.items, spec)
	}
	if len(req.items) == 0 {
		return fetchRequest{}, wireBad("empty FETCH data item list")
	}
	return req, nil
}

func (r fetchRequest) itemNames() []string {
	out := make([]string, 0, len(r.items))
	for _, it := range r.items {
		out = append(out, it.name)
	}
	return out
}

func parseFetchItem(raw string) (fetchItemSpec, error) {
	up := strings.ToUpper(raw)
	spec := fetchItemSpec{name: up}
	switch {
	case up == "UID":
		spec.kind = fkUID
	case up == "FLAGS":
		spec.kind = fkFlags
	case up == "INTERNALDATE":
		spec.kind = fkInternalDate
	case up == "RFC822.SIZE":
		spec.kind = fkRFC822Size
	case up == "ENVELOPE":
		spec.kind = fkEnvelope
	case up == "BODYSTRUCTURE":
		spec.kind = fkBodyStructure
	case up == "BODY":
		spec.kind = fkBody
	case up == "RFC822":
		spec.kind = fkSection
		spec.section = &mimeparse.SectionSpec{Kind: mimeparse.SecWhole}
	case up == "RFC822.HEADER":
		spec.kind = fkSection
		spec.section = &mimeparse.SectionSpec{Kind: mimeparse.SecHeader}
	case up == "RFC822.TEXT":
		spec.kind = fkSection
		spec.section = &mimeparse.SectionSpec{Kind: mimeparse.SecText}
	case strings.HasPrefix(up, "BODY[") || strings.HasPrefix(up, "BODY.PEEK["):
		sec, partial, bracket, err := parseBodyBracket(raw, up)
		if err != nil {
			return fetchItemSpec{}, err
		}
		spec.kind = fkSection
		spec.section = sec
		spec.partial = partial
		spec.bracketText = bracket
		spec.name = "BODY[" + bracket + "]"
		if partial != nil {
			spec.name += "<" + partial.String() + ">"
		}
	default:
		return fetchItemSpec{}, wireUnsupported("FETCH data item %q is not supported", raw)
	}
	return spec, nil
}

// parseBodyBracket extracts the text inside [...] and any trailing <o.c>.
// It returns the section spec, optional partial and a canonical bracket text
// (keywords upper-cased; HEADER.FIELDS names echoed with original spelling).
func parseBodyBracket(raw, up string) (*mimeparse.SectionSpec, *mimeparse.Partial, string, error) {
	open := strings.IndexByte(raw, '[')
	closeIdx := strings.LastIndexByte(raw, ']')
	if open < 0 || closeIdx < open {
		return nil, nil, "", wireBad("malformed BODY section %q", raw)
	}
	inside := raw[open+1 : closeIdx]
	var partial []byte
	rest := raw[closeIdx+1:]
	if strings.HasPrefix(rest, "<") {
		gt := strings.IndexByte(rest, '>')
		if gt < 0 {
			return nil, nil, "", wireBad("malformed BODY partial in %q", raw)
		}
		partial = []byte(rest[:gt+1])
	} else if rest != "" {
		return nil, nil, "", wireBad("unexpected text after BODY section: %q", rest)
	}
	sec, p, err := mimeparse.ParseSection(inside, partial)
	if err != nil {
		return nil, nil, "", wireBad("%v", err)
	}
	return &sec, p, canonicalBracket(inside), nil
}

// canonicalBracket upper-cases section keywords but preserves the original
// spelling inside a HEADER.FIELDS parenthesized list.
func canonicalBracket(inside string) string {
	if i := strings.IndexByte(inside, '('); i >= 0 {
		return strings.ToUpper(inside[:i]) + inside[i:]
	}
	return strings.ToUpper(inside)
}

// renderFetchItems turns one stored message into wire data items. Message
// parse failures are reported as [PARSE] compute errors for that FETCH.
func renderFetchItems(m store.Message, req fetchRequest, byUID bool) ([]imapwire.FetchItem, error) {
	var entity *mimeparse.Entity
	ensureEntity := func() (*mimeparse.Entity, error) {
		if entity != nil {
			return entity, nil
		}
		e, err := mimeparse.Parse(m.Raw)
		if err != nil {
			return nil, imapwire.NewCodedError(imapwire.ClassCompute, "PARSE",
				"message UID %d is malformed: %v", m.UID, err)
		}
		entity = e
		return entity, nil
	}

	out := make([]imapwire.FetchItem, 0, len(req.items)+1)
	for _, it := range req.items {
		switch it.kind {
		case fkUID:
			out = append(out, imapwire.FetchItem{Name: "UID", Kind: imapwire.KindNumber, Num: m.UID})
		case fkFlags:
			out = append(out, imapwire.FetchItem{Name: "FLAGS", Kind: imapwire.KindFlagList, Flags: m.Flags})
		case fkInternalDate:
			out = append(out, imapwire.FetchItem{
				Name: "INTERNALDATE", Kind: imapwire.KindQuoted,
				Text: m.InternalDate.Format("02-Jan-2006 15:04:05 -0700"),
			})
		case fkRFC822Size:
			out = append(out, imapwire.FetchItem{Name: "RFC822.SIZE", Kind: imapwire.KindNumber, Num: uint32(len(m.Raw))})
		case fkEnvelope:
			e, err := ensureEntity()
			if err != nil {
				return nil, err
			}
			out = append(out, imapwire.FetchItem{Name: "ENVELOPE", Kind: imapwire.KindVerbatim, Raw: e.Envelope().Encode()})
		case fkBodyStructure:
			e, err := ensureEntity()
			if err != nil {
				return nil, err
			}
			out = append(out, imapwire.FetchItem{Name: "BODYSTRUCTURE", Kind: imapwire.KindVerbatim, Raw: e.BodyStructure()})
		case fkBody:
			e, err := ensureEntity()
			if err != nil {
				return nil, err
			}
			out = append(out, imapwire.FetchItem{Name: "BODY", Kind: imapwire.KindVerbatim, Raw: e.Body()})
		case fkSection:
			e, err := ensureEntity()
			if err != nil {
				return nil, err
			}
			data, err := e.Extract(*it.section, it.partial)
			if err != nil {
				return nil, imapwire.NewCodedError(imapwire.ClassCompute, "PARSE", "%v", err)
			}
			if data == nil {
				// Nonexistent part: RFC says the item is omitted entirely.
				continue
			}
			name := sectionItemName(it)
			out = append(out, imapwire.FetchItem{Name: name, Kind: imapwire.KindLiteral, Raw: data})
		}
	}
	// RFC 9051: UID FETCH responses always carry the UID, even if not asked.
	if byUID && !containsUID(req) {
		out = append(out, imapwire.FetchItem{Name: "UID", Kind: imapwire.KindNumber, Num: m.UID})
	}
	return out, nil
}

func containsUID(req fetchRequest) bool {
	for _, it := range req.items {
		if it.kind == fkUID {
			return true
		}
	}
	return false
}

// sectionItemName renders the response item name for a BODY[..] request. The
// RFC822.* macros echo their own macro name; BODY[..] echoes canonical casing.
func sectionItemName(it fetchItemSpec) string {
	if it.name == "RFC822" || it.name == "RFC822.HEADER" || it.name == "RFC822.TEXT" {
		return it.name
	}
	name := "BODY[" + it.bracketText + "]"
	if it.partial != nil {
		name += "<" + it.partial.String() + ">"
	}
	return name
}
