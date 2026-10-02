package engine

import (
	"context"
	"fmt"
	"net"

	"coaplab/internal/blocks"
	"coaplab/internal/wire"
)

// Exchanger is the message-layer subset BlockwiseClient needs. *transport.Client
// satisfies it; tests substitute a scripted exchanger (see test/oracle).
type Exchanger interface {
	Exchange(ctx context.Context, remote *net.UDPAddr, req *wire.Message) (*wire.Message, error)
}

// BlockwiseClient performs block-wise transfers over a message-layer client.
// Each block is its own CON exchange with a FRESH Message ID per transmission
// and a FRESH Token per exchange, while the server identifies one Block1
// sequence by (endpoint, URI). This keeps the MID and Token layers separate
// (RFC 7252 §5.3.1 vs §4.4; RFC 7959 §2 intro).
type BlockwiseClient struct {
	ex Exchanger
}

// NewBlockwiseClient wraps a transport client or test double.
func NewBlockwiseClient(ex Exchanger) *BlockwiseClient { return &BlockwiseClient{ex: ex} }

// UploadResult summarizes a Block1 upload.
type UploadResult struct {
	FinalCode     wire.Code
	Exchanges     int
	NegotiatedSZX uint8
}

// Upload sends body via Block1 PUT/POST and adapts to a smaller server SZX.
//
// Negotiation (RFC 7959 §2.3/§2.5): block 0 goes out at proposeSZX. A 2.31
// reply may carry a smaller SZX; the remaining body is then re-chunked at
// the negotiated size and block numbers scale accordingly. The server tracks
// contiguity in byte offsets, so renumbering never creates a gap.
func (bc *BlockwiseClient) Upload(ctx context.Context, remote *net.UDPAddr, path string, method wire.Code, contentFormat uint16, body []byte, proposeSZX uint8) (UploadResult, error) {
	if proposeSZX > 6 {
		return UploadResult{}, fmt.Errorf("engine: illegal proposed SZX %d", proposeSZX)
	}
	if method != wire.PUT && method != wire.POST {
		return UploadResult{}, fmt.Errorf("engine: Block1 upload requires PUT or POST, got %s", method)
	}
	szx := proposeSZX
	offset := 0
	exchanges := 0

	for {
		size := 1 << (szx + 4)
		end := offset + size
		if end > len(body) {
			end = len(body)
		}
		payload := body[offset:end]
		num := uint32(offset >> (szx + 4))
		more := end < len(body)

		req := &wire.Message{
			Code: method,
			Options: []wire.Option{
				{Number: wire.OpURIPath, Value: []byte(path)},
				{Number: wire.OpContentFormat, Value: wire.EncodeUint(uint64(contentFormat))},
				{Number: wire.OpBlock1, Value: wire.Block{NUM: num, M: more, SZX: szx}.Encode()},
			},
			Payload: append([]byte(nil), payload...),
		}
		resp, err := bc.ex.Exchange(ctx, remote, req)
		if err != nil {
			return UploadResult{}, err
		}
		exchanges++
		if resp.Code.Class() == 4 || resp.Code.Class() == 5 {
			return UploadResult{}, &TransferError{Code: resp.Code, Body: append([]byte(nil), resp.Payload...)}
		}

		b1, has, perr := resp.Block1()
		if perr != nil {
			return UploadResult{}, fmt.Errorf("engine: bad Block1 in response: %w", perr)
		}

		if more {
			if resp.Code != wire.Continue || !has {
				return UploadResult{}, fmt.Errorf("engine: interim block %d got %s, want 2.31+Block1", num, resp.Code)
			}
			if b1.SZX < szx {
				szx = b1.SZX // server capped the size; rescale from current offset
			}
			offset = end
			continue
		}

		if resp.Code.Class() != 2 {
			return UploadResult{}, fmt.Errorf("engine: final block answered %s", resp.Code)
		}
		return UploadResult{FinalCode: resp.Code, Exchanges: exchanges, NegotiatedSZX: szx}, nil
	}
}

// DownloadResult summarizes a Block2 download.
type DownloadResult struct {
	Body          []byte
	ETag          []byte
	ContentFormat uint16
	Exchanges     int
	SZX           uint8
}

// Download fetches a whole representation block by block, binding every
// block to the block-0 ETag. A representation change mid-transfer aborts with
// a classified TransferError (etag_changed); blocks are never spliced across
// versions.
func (bc *BlockwiseClient) Download(ctx context.Context, remote *net.UDPAddr, path string, proposeSZX uint8) (DownloadResult, error) {
	if proposeSZX > 6 {
		return DownloadResult{}, fmt.Errorf("engine: illegal proposed SZX %d", proposeSZX)
	}
	re := blocks.NewBlock2Reassembler(path)
	szx := proposeSZX
	exchanges := 0
	var cf uint16

	for {
		nextNum := uint32(0)
		if exchanges > 0 {
			nextNum = re.LowestGap()
		}
		req := &wire.Message{
			Code: wire.GET,
			Options: []wire.Option{
				{Number: wire.OpURIPath, Value: []byte(path)},
				{Number: wire.OpBlock2, Value: wire.Block{NUM: nextNum, M: false, SZX: szx}.Encode()},
			},
		}
		resp, err := bc.ex.Exchange(ctx, remote, req)
		if err != nil {
			return DownloadResult{}, err
		}
		exchanges++
		if resp.Code.Class() == 4 || resp.Code.Class() == 5 {
			return DownloadResult{}, &TransferError{Code: resp.Code, Body: append([]byte(nil), resp.Payload...)}
		}
		if resp.Code != wire.Content {
			return DownloadResult{}, fmt.Errorf("engine: expected 2.05 Content, got %s", resp.Code)
		}
		b2, has, perr := resp.Block2()
		if perr != nil || !has {
			return DownloadResult{}, fmt.Errorf("engine: 2.05 without valid Block2 option: %v", perr)
		}
		tag, _ := resp.FirstOption(wire.OpETag)
		var haveCF bool
		var cfRaw []byte
		if cfRaw, haveCF = resp.FirstOption(wire.OpContentFormat); haveCF {
			cf = uint16(wire.DecodeUint(cfRaw))
		}
		out, ferr := re.Offer(b2, resp.Payload, append([]byte(nil), tag...), cf, haveCF)
		if ferr != nil {
			return DownloadResult{}, &TransferError{Code: ferr.Code, Category: ferr.Category, Detail: ferr.Detail}
		}
		if exchanges == 1 {
			szx = b2.SZX // converge on the exponent the server actually used
		}
		if out.Complete {
			return DownloadResult{
				Body:          out.Body,
				ETag:          re.ETag(),
				ContentFormat: cf,
				Exchanges:     exchanges,
				SZX:           szx,
			}, nil
		}
	}
}
