package hpack

import (
	"errors"
	"fmt"

	"hpacklab.local/codec"
)

// Limits configures a Decoder's post-decompression budgets.
//
// All three caps exist independently: a single malicious string, a single
// header block, and the sum across blocks must each be bounded.
type Limits struct {
	// MaxStringLen caps one decompressed name or value (0 = unlimited).
	MaxStringLen int
	// MaxHeaderBlockEmitted caps total emitted bytes (name+value) per block.
	MaxHeaderBlockEmitted uint64
	// MaxHeaderFields caps the number of emitted fields per block.
	MaxHeaderFields int
}

// DefaultLimits are conservative caps suitable for a public server: strings
// up to 64 KiB (HTTP/2 field value bound), 1 MiB per header list, 256 fields.
func DefaultLimits() Limits {
	return Limits{
		MaxStringLen:          64 * 1024,
		MaxHeaderBlockEmitted: 1 << 20,
		MaxHeaderFields:       256,
	}
}

// EventKind enumerates observer events.
type EventKind int

const (
	EvBlockStart EventKind = iota
	EvSizeUpdate
	EvIndexed
	EvLiteralIndexed
	EvLiteral
	EvLiteralNever
	EvBlockEnd
	EvEviction
)

// Event is one observable decoder step. It is the audit trail the service
// layer turns into structured, request-correlated logs.
type Event struct {
	Kind   EventKind
	Offset int
	// Detail fields, populated where applicable:
	Index          int    // for indexed representations
	OldMax, NewMax uint32 // for size updates
	Evicted        int    // evicted entries after an add/update
	EmittedName    string // header field, present for literal/indexed emits
	EmittedValue   string
	Sensitive      bool
	TableSize      uint32
	BlockBytes     uint64
}

// Observer receives every decoder step. Implementations must not retain
// references to name/value byte content beyond the call; the decoder does
// not hold observer state.
type Observer interface {
	OnEvent(ev Event)
}

// DecoderOptions configures NewDecoder.
type DecoderOptions struct {
	// MaxTableSize is the decoder's initial dynamic table maximum and must
	// equal the value the local side advertised via
	// SETTINGS_HEADER_TABLE_SIZE.
	MaxTableSize uint32
	Limits       Limits
	// ValidatePseudo enforces HTTP/2 pseudo-header ordering/uniqueness.
	ValidatePseudo bool
	Observer       Observer
}

// Decoder is one direction of one connection's HPACK state. A Decoder is
// not safe for concurrent use (HTTP/2 header blocks on one stream family
// are processed serially anyway); give every connection its own.
type Decoder struct {
	dt       *dynamicTable
	limits   Limits
	validate bool
	obs      Observer

	// block-local state:
	offset     int
	emitted    uint64
	fieldCount int
	firstField bool
	sawRegular bool
	pseudoSeen map[string]bool
	poisoned   bool
}

// NewDecoder builds a connection-scoped decoder.
func NewDecoder(opts DecoderOptions) *Decoder {
	return &Decoder{
		dt:       newDynamicTable(opts.MaxTableSize),
		limits:   opts.Limits,
		validate: opts.ValidatePseudo,
		obs:      opts.Observer,
	}
}

// SetAllowedMaxTableSize raises or lowers the SETTINGS-negotiated ceiling.
// It never changes the current maximum by itself; peer size updates are
// capped by this value.
func (d *Decoder) SetAllowedMaxTableSize(v uint32) { d.dt.allowedMaxSize = v }

// DynamicTableSize reports the live byte occupancy of the dynamic table.
func (d *Decoder) DynamicTableSize() uint32 { return d.dt.size }

// DynamicTableMax reports the current maximum.
func (d *Decoder) DynamicTableMax() uint32 { return d.dt.maxSize }

// DynamicTableLen reports the number of live dynamic entries.
func (d *Decoder) DynamicTableLen() int { return d.dt.len() }

// Poisoned reports whether a prior block failed and this decoder must no
// longer be used.
func (d *Decoder) Poisoned() bool { return d.poisoned }

func (d *Decoder) emit(ev Event) {
	if d.obs != nil {
		ev.Offset = d.offset
		ev.TableSize = d.dt.size
		ev.BlockBytes = d.emitted
		d.obs.OnEvent(ev)
	}
}

func (d *Decoder) fail(k Kind, detail string) error {
	d.poisoned = true
	return &Error{Kind: k, Offset: d.offset, Detail: detail}
}

func (d *Decoder) mapCodecErr(err error) error {
	switch {
	case err == nil:
		return nil
	case errors.Is(err, codec.ErrTruncatedInteger):
		return d.fail(KindIntegerTruncated, err.Error())
	case errors.Is(err, codec.ErrIntegerOverflow):
		return d.fail(KindIntegerOverflow, err.Error())
	case errors.Is(err, codec.ErrTruncatedString):
		return d.fail(KindStringTruncated, err.Error())
	case errors.Is(err, codec.ErrStringLengthLimit):
		return d.fail(KindStringTooLong, err.Error())
	case errors.Is(err, codec.ErrInvalidHuffman):
		return d.fail(KindHuffmanInvalid, err.Error())
	default:
		return d.fail(KindTruncatedBlock, err.Error())
	}
}

// DecodeBlock decodes one complete HPACK header block (the payload of one
// HEADERS/CONTINUATION sequence) and returns its fields in wire order. On
// any error the decoder is poisoned and the returned slice is whatever was
// emitted before the failure; callers MUST discard both.
func (d *Decoder) DecodeBlock(block []byte) ([]HeaderField, error) {
	if d.poisoned {
		return nil, &Error{Kind: KindPoisoned, Offset: 0, Detail: "prior block already failed"}
	}
	d.offset = 0
	d.emitted = 0
	d.fieldCount = 0
	d.firstField = true
	d.sawRegular = false
	d.pseudoSeen = make(map[string]bool, 8)
	d.emit(Event{Kind: EvBlockStart})

	rest := block
	out := make([]HeaderField, 0, 16)
	for len(rest) > 0 {
		start := len(block) - len(rest)
		lead := rest[0]
		var (
			f       HeaderField
			err     error
			isField = true
		)
		switch {
		case lead&0x80 != 0:
			rest, f, err = d.parseIndexed(rest)
		case lead&0xc0 == 0x40:
			rest, f, err = d.parseLiteralIncremental(rest)
		case lead&0xe0 == 0x20:
			rest, err = d.parseSizeUpdate(rest)
			isField = false
		case lead&0xf0 == 0x10:
			rest, f, err = d.parseLiteralNeverIndexed(rest)
		default:
			rest, f, err = d.parseLiteralWithoutIndexing(rest)
		}
		if err != nil {
			d.offset = start
			return out, err
		}
		if isField {
			if err := d.acceptField(f, &out); err != nil {
				d.offset = start
				return out, err
			}
		}
	}
	d.emit(Event{Kind: EvBlockEnd})
	d.firstField = true
	return out, nil
}

// acceptField applies block-level limits and HTTP/2 ordering rules, then
// appends the field to the block output.
func (d *Decoder) acceptField(f HeaderField, out *[]HeaderField) error {
	if d.validate {
		if f.IsPseudo() {
			if d.sawRegular {
				return d.fail(KindIllegalPseudo, fmt.Sprintf("pseudo-header %q after regular header", f.Name))
			}
			if d.pseudoSeen[f.Name] {
				return d.fail(KindIllegalPseudo, fmt.Sprintf("duplicate pseudo-header %q", f.Name))
			}
			d.pseudoSeen[f.Name] = true
		} else {
			d.sawRegular = true
		}
	}
	if d.limits.MaxHeaderFields > 0 && d.fieldCount >= d.limits.MaxHeaderFields {
		return d.fail(KindHeaderListTooLarge, fmt.Sprintf("more than %d fields in block", d.limits.MaxHeaderFields))
	}
	added := uint64(len(f.Name)) + uint64(len(f.Value))
	if d.limits.MaxHeaderBlockEmitted > 0 && d.emitted+added > d.limits.MaxHeaderBlockEmitted {
		return d.fail(KindHeaderListTooLarge, fmt.Sprintf("header block exceeds %d emitted bytes", d.limits.MaxHeaderBlockEmitted))
	}
	d.emitted += added
	d.fieldCount++
	// From the first accepted field onward, a size update is illegal.
	d.firstField = false
	*out = append(*out, f)
	return nil
}
