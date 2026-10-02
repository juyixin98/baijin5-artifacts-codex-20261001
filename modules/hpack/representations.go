package hpack

import (
	"fmt"

	"hpacklab.local/codec"
)

// parseIndexed decodes section 6.1: 1xxxxxxx.
func (d *Decoder) parseIndexed(rest []byte) ([]byte, HeaderField, error) {
	lead := rest[0]
	idx, tail, err := codec.ConsumeInteger(lead, rest[1:], 7)
	if err != nil {
		return nil, HeaderField{}, d.mapCodecErr(err)
	}
	if idx == 0 {
		return nil, HeaderField{}, d.fail(KindIndexZero, "indexed header field index is 0")
	}
	f, ok := d.tableAt(idx)
	if !ok {
		return nil, HeaderField{}, d.fail(KindIndexOutOfRange, fmt.Sprintf("index %d exceeds table size %d", idx, staticLen+d.dt.len()))
	}
	d.emit(Event{Kind: EvIndexed, Index: int(idx), EmittedName: f.Name, EmittedValue: f.Value})
	return tail, f, nil
}

// parseLiteralIncremental decodes section 6.2.1: 01xxxxxx.
func (d *Decoder) parseLiteralIncremental(rest []byte) ([]byte, HeaderField, error) {
	tail, f, _, err := d.parseLiteralCommon(rest, 6)
	if err != nil {
		return nil, HeaderField{}, err
	}
	// RFC 7541 section 4.4: insert first, then evict until the table fits.
	// An entry larger than the maximum evicts itself as well, leaving the
	// table empty; the field is still emitted.
	n := d.dt.add(f)
	if n > 0 {
		d.emit(Event{Kind: EvEviction, Evicted: n})
	}
	d.emit(Event{Kind: EvLiteralIndexed, EmittedName: f.Name, EmittedValue: f.Value})
	return tail, f, nil
}

// parseLiteralWithoutIndexing decodes section 6.2.2: 0000xxxx.
func (d *Decoder) parseLiteralWithoutIndexing(rest []byte) ([]byte, HeaderField, error) {
	tail, f, _, err := d.parseLiteralCommon(rest, 4)
	if err != nil {
		return nil, HeaderField{}, err
	}
	d.emit(Event{Kind: EvLiteral, EmittedName: f.Name, EmittedValue: f.Value})
	return tail, f, nil
}

// parseLiteralNeverIndexed decodes section 6.2.3: 0001xxxx.
func (d *Decoder) parseLiteralNeverIndexed(rest []byte) ([]byte, HeaderField, error) {
	tail, f, _, err := d.parseLiteralCommon(rest, 4)
	if err != nil {
		return nil, HeaderField{}, err
	}
	f.Sensitive = true
	d.emit(Event{Kind: EvLiteralNever, EmittedName: f.Name, EmittedValue: f.Value, Sensitive: true})
	return tail, f, nil
}

// parseLiteralCommon shares the string-reading logic for the three literal
// forms. It returns the unconsumed tail and the assembled field.
func (d *Decoder) parseLiteralCommon(rest []byte, prefix uint) (tail []byte, f HeaderField, indexed bool, err error) {
	lead := rest[0]
	nameIdx, t, ierr := codec.ConsumeInteger(lead, rest[1:], prefix)
	if ierr != nil {
		return nil, HeaderField{}, false, d.mapCodecErr(ierr)
	}
	if nameIdx != 0 {
		ref, ok := d.tableAt(nameIdx)
		if !ok {
			return nil, HeaderField{}, false, d.fail(KindIndexOutOfRange, fmt.Sprintf("name index %d exceeds table size %d", nameIdx, staticLen+d.dt.len()))
		}
		f.Name = ref.Name
		indexed = true
	}
	if len(t) == 0 {
		return nil, HeaderField{}, indexed, d.fail(KindTruncatedBlock, "missing value string")
	}
	if nameIdx == 0 {
		var ns codec.String
		ns, t, ierr = codec.ConsumeString(t[0], t[1:], d.limits.MaxStringLen)
		if ierr != nil {
			return nil, HeaderField{}, false, d.mapCodecErr(ierr)
		}
		f.Name = string(ns.Data)
	}
	if len(t) == 0 {
		return nil, HeaderField{}, indexed, d.fail(KindTruncatedBlock, "missing value string")
	}
	vs, t2, ierr := codec.ConsumeString(t[0], t[1:], d.limits.MaxStringLen)
	if ierr != nil {
		return nil, HeaderField{}, indexed, d.mapCodecErr(ierr)
	}
	f.Value = string(vs.Data)
	return t2, f, indexed, nil
}

// parseSizeUpdate decodes section 6.3: 001xxxxx. Such a representation is
// legal only before the first header field representation of the block.
func (d *Decoder) parseSizeUpdate(rest []byte) ([]byte, error) {
	if !d.firstField {
		return nil, d.fail(KindSizeUpdatePosition, "dynamic table size update appears after a header field")
	}
	lead := rest[0]
	size, tail, err := codec.ConsumeInteger(lead, rest[1:], 5)
	if err != nil {
		return nil, d.mapCodecErr(err)
	}
	if size > uint64(d.dt.allowedMaxSize) {
		return nil, d.fail(KindSizeUpdateTooLarge, fmt.Sprintf("requested table size %d exceeds allowed maximum %d", size, d.dt.allowedMaxSize))
	}
	old := d.dt.maxSize
	n := d.dt.setMaxSize(uint32(size))
	d.emit(Event{Kind: EvSizeUpdate, OldMax: old, NewMax: uint32(size), Evicted: n})
	return tail, nil
}

// tableAt resolves a 1-based protocol index across both tables.
func (d *Decoder) tableAt(idx uint64) (HeaderField, bool) {
	if idx == 0 {
		return HeaderField{}, false
	}
	if idx <= uint64(staticLen) {
		return staticEntries[idx-1], true
	}
	return d.dt.at(int(idx) - staticLen)
}
