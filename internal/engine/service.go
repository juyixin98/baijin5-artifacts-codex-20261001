// Package engine binds the protocol state machines to the resource store:
// it is the controlled CoAP service (GET/Block2 download, PUT/POST/Block1
// atomic upload, DELETE), including option validation, block-size
// negotiation and ETag version binding.
package engine

import (
	"net"
	"sync"

	"coaplab/internal/blocks"
	"coaplab/internal/diag"
	"coaplab/internal/etag"
	"coaplab/internal/ids"
	"coaplab/internal/store"
	"coaplab/internal/wire"
)

// Service is the request handler.
type Service struct {
	store     *store.Store
	assembler *blocks.Block1Assembler
	prefSZX   uint8
	maxMsg    int
	rec       *diag.Recorder

	obsMu    sync.RWMutex
	onCommit func(path string, version int64)
}

// SetCommitObserver registers a callback fired exactly once per successful
// resource mutation (Block1 final commit or single-datagram PUT). Retransmit
// replays never fire it. Test/diagnostic hook.
func (s *Service) SetCommitObserver(f func(path string, version int64)) {
	s.obsMu.Lock()
	s.onCommit = f
	s.obsMu.Unlock()
}

func (s *Service) fireCommit(path string, version int64) {
	s.obsMu.RLock()
	f := s.onCommit
	s.obsMu.RUnlock()
	if f != nil {
		f(path, version)
	}
}

// NewService wires store + block1 assembler.
func NewService(st *store.Store, asm *blocks.Block1Assembler, preferredSZX uint8, maxMessageSize int, rec *diag.Recorder) *Service {
	s := &Service{
		store:     st,
		assembler: asm,
		prefSZX:   preferredSZX,
		maxMsg:    maxMessageSize,
		rec:       rec,
	}
	return s
}

// supportedCriticalOptions is the whitelist; an unknown CRITICAL option
// (odd option number, RFC 7252 §5.4.1) MUST yield 4.02 Bad Option.
var supportedOptions = map[int]bool{
	wire.OpIfMatch: true, wire.OpURIHost: true, wire.OpETag: true,
	wire.OpIfNoneMatch: true, wire.OpURIPort: true, wire.OpLocationPath: true,
	wire.OpURIPath: true, wire.OpContentFormat: true, wire.OpMaxAge: true,
	wire.OpURIQuery: true, wire.OpBlock2: true, wire.OpBlock1: true,
	wire.OpSize2: true, wire.OpSize1: true,
}

// ServeCoAP implements transport.Handler.
func (s *Service) ServeCoAP(remote *net.UDPAddr, req *wire.Message) *wire.Message {
	if err := checkOptions(req); err != nil {
		return errorResp(req, wire.BadOption, err.Error())
	}
	path := joinPath(req.Path())

	switch req.Code {
	case wire.GET:
		return s.handleGet(remote, req, path)
	case wire.PUT, wire.POST:
		return s.handlePut(remote, req, path)
	case wire.DELETE:
		return s.handleDelete(req, path)
	default:
		return errorResp(req, wire.MethodNotAllowed, "method not supported")
	}
}

func checkOptions(req *wire.Message) *blocks.Failure {
	seen := map[int]bool{}
	for _, o := range req.Options {
		if !supportedOptions[o.Number] && o.Number%2 == 1 {
			return &blocks.Failure{
				Category: diag.CatUnknownCriticalOpt,
				Code:     wire.BadOption,
				Op:       "engine",
				Detail:   "unknown critical option " + itoa(o.Number),
			}
		}
		if (o.Number == wire.OpBlock1 || o.Number == wire.OpBlock2) && seen[o.Number] {
			return &blocks.Failure{Category: diag.CatParseError, Code: wire.BadRequest, Op: "engine", Detail: "repeated block option"}
		}
		seen[o.Number] = true
	}
	return nil
}

func (s *Service) handleGet(remote *net.UDPAddr, req *wire.Message, path string) *wire.Message {
	res, err := s.store.Get(path)
	if err != nil {
		return errorResp(req, wire.NotFound, "no such resource: /"+path)
	}

	b2, hasB2, berr := req.Block2()
	if berr != nil {
		return errorResp(req, wire.BadRequest, berr.Error())
	}

	// Negotiated exponent: client proposal (or 1024) capped by preference.
	proposed := uint8(6)
	num := uint32(0)
	if hasB2 {
		if b2.M {
			// M MUST be zero in a Block2 control request (RFC 7959 §2.4).
			return errorResp(req, wire.BadRequest, "Block2 request M bit MUST be 0")
		}
		proposed, num = b2.SZX, b2.NUM
	}
	szx := blocks.Negotiate(proposed, s.prefSZX)

	// No Block2 option and body fits one datagram: plain 2.05.
	if !hasB2 && len(res.Body) <= (1<<(szx+4)) {
		return &wire.Message{
			Code:    wire.Content,
			Options: bodyOptions(res),
			Payload: append([]byte(nil), res.Body...),
		}
	}

	payload, more, ferr := blocks.Slice(res.Body, num, szx)
	if ferr != nil {
		return errorResp(req, ferr.Code, ferr.Detail)
	}
	opts := bodyOptions(res)
	opts = append(opts, wire.Option{Number: wire.OpBlock2, Value: wire.Block{
		NUM: num, M: more, SZX: szx,
	}.Encode()})
	s.diag(remote, req, diag.Accept, "",
		"GET /%s block %d szx=%d (%d bytes) etag=%s version=%d m=%d",
		path, num, szx, len(payload), etag.MaskedHex(res.ETag), res.Version, b2m(more))
	return &wire.Message{Code: wire.Content, Options: opts, Payload: payload}
}

func (s *Service) handlePut(remote *net.UDPAddr, req *wire.Message, path string) *wire.Message {
	b1, hasB1, berr := req.Block1()
	if berr != nil {
		return errorResp(req, wire.BadRequest, berr.Error())
	}
	cf, haveCF := contentFormat(req)

	commit := func(p string, contentFormat uint16, body []byte) (wire.Code, *blocks.Failure) {
		existed := true
		if _, err := s.store.Get(p); err != nil {
			existed = false
		}
		code := wire.Changed
		if req.Code == wire.POST || !existed {
			code = wire.Created
		}
		saved, err := s.store.Put(p, contentFormat, body)
		if err != nil {
			return 0, &blocks.Failure{Category: diag.CatMessageLayer, Code: wire.InternalServerError, Op: "block1", Detail: err.Error()}
		}
		s.fireCommit(p, saved.Version)
		return code, nil
	}

	if !hasB1 {
		// Whole-body single datagram.
		if len(req.Payload) > s.maxMsg {
			return withBlock1Hint(errorResp(req, wire.RequestEntityTooLarge, "send block-wise with Block1"), s.prefSZX)
		}
		code, ferr := commit(path, cf, req.Payload)
		if ferr != nil {
			return errorResp(req, ferr.Code, ferr.Detail)
		}
		s.diag(remote, req, diag.Accept, "", "%s /%s single-datagram %d bytes -> %s", req.Code, path, len(req.Payload), code)
		return &wire.Message{Code: code}
	}

	res, ferr := s.assembler.Offer(remote.String(), b1, req.Payload, cf, haveCF, path, commit,
		blocks.ReqMeta{MID: req.MID, HaveMID: true, Token: tokenHex(req.Token)})
	if ferr != nil {
		resp := errorResp(req, ferr.Code, ferr.Detail)
		if ferr.Code == wire.RequestEntityTooLarge {
			resp = withBlock1Hint(resp, s.prefSZX)
		}
		s.diag(remote, req, diag.Reject, ferr.Category, "PUT /%s block %d rejected: %s", path, b1.NUM, ferr.Detail)
		return resp
	}
	opts := []wire.Option{{Number: wire.OpBlock1, Value: res.Reply.Encode()}}
	resp := &wire.Message{Code: res.ReplyCode, Options: opts}
	s.diag(remote, req, res.Verdict, "", "PUT /%s block %d -> %s (reply 1:%d/%d/%d)",
		path, b1.NUM, res.ReplyCode, res.Reply.NUM, b2m(res.Reply.M), res.Reply.SZX)
	return resp
}

func (s *Service) handleDelete(req *wire.Message, path string) *wire.Message {
	ok, err := s.store.Delete(path)
	if err != nil {
		return errorResp(req, wire.InternalServerError, err.Error())
	}
	if !ok {
		return errorResp(req, wire.NotFound, "no such resource: /"+path)
	}
	return &wire.Message{Code: wire.Deleted}
}

// ---- helpers ----

func bodyOptions(res store.Resource) []wire.Option {
	return []wire.Option{
		{Number: wire.OpETag, Value: append([]byte(nil), res.ETag...)},
		{Number: wire.OpContentFormat, Value: wire.EncodeUint(uint64(res.ContentFormat))},
	}
}

func contentFormat(req *wire.Message) (uint16, bool) {
	raw, ok := req.FirstOption(wire.OpContentFormat)
	if !ok {
		return 0, false
	}
	var v uint16
	for _, b := range raw {
		v = v<<8 | uint16(b)
	}
	return v, true
}

func errorResp(req *wire.Message, code wire.Code, detail string) *wire.Message {
	return &wire.Message{
		Code:    code,
		Payload: []byte(detail),
	}
}

func withBlock1Hint(resp *wire.Message, szx uint8) *wire.Message {
	resp.Options = append(resp.Options, wire.Option{
		Number: wire.OpBlock1,
		Value:  wire.Block{NUM: 0, M: false, SZX: szx}.Encode(),
	})
	return resp
}

func joinPath(segs []string) string {
	out := ""
	for i, s := range segs {
		if i > 0 {
			out += "/"
		}
		out += s
	}
	return out
}

func b2m(m bool) int {
	if m {
		return 1
	}
	return 0
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

func (s *Service) diag(remote *net.UDPAddr, req *wire.Message, v diag.Verdict, c diag.Category, format string, args ...any) {
	if s.rec == nil {
		return
	}
	s.rec.Log(remote.String(), req.MID, true, tokenHex(req.Token), v, c, format, args...)
}

func tokenHex(t []byte) string {
	if len(t) == 0 {
		return ""
	}
	return ids.Token(t).Hex()
}
