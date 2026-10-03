package service

import (
	"errors"

	"hpacklab/codec"
	"hpacklab/state"
	"hpacklab/table"
)

// classify maps a decode error to a stable, loggable failure category.
// Categories are deliberately coarse; the wrapped error carries detail.
func classify(err error) string {
	switch {
	case errors.Is(err, state.ErrDesync):
		return "desync"
	case errors.Is(err, state.ErrSizeUpdatePlacement):
		return "size-update-placement"
	case errors.Is(err, state.ErrSizeUpdateTooLarge):
		return "size-update-too-large"
	case errors.Is(err, state.ErrHeaderListTooLarge):
		return "header-list-too-large"
	case errors.Is(err, state.ErrTooManyHeaders):
		return "too-many-headers"
	case errors.Is(err, table.ErrIndexZero):
		return "index-zero"
	case errors.Is(err, table.ErrIndexOutOfRange):
		return "index-out-of-range"
	case errors.Is(err, codec.ErrHuffmanEOS):
		return "huffman-eos"
	case errors.Is(err, codec.ErrHuffmanPadding):
		return "huffman-padding"
	case errors.Is(err, codec.ErrIntegerOverflow):
		return "integer-overflow"
	case errors.Is(err, codec.ErrStringTooLong):
		return "string-too-long"
	case errors.Is(err, codec.ErrTruncated):
		return "truncated"
	default:
		return "unknown"
	}
}
