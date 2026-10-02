// Package sem contains execution types shared by the independent scalar
// reference evaluator and the masked-SIMD interpreter.
package sem

// FaultCode classifies the reason an active lane trapped.
type FaultCode string

const (
	FaultDivZero      FaultCode = "DIV_BY_ZERO"
	FaultIndexOOB     FaultCode = "INDEX_OUT_OF_BOUNDS"
	FaultModZero      FaultCode = "DIV_BY_ZERO"
	FaultReduceOOB    FaultCode = "INDEX_OUT_OF_BOUNDS"
	FaultMissingInput FaultCode = "MISSING_INPUT"
	FaultOutputOOB    FaultCode = "OUTPUT_INDEX_OUT_OF_BOUNDS"
)

// Stage identifies where the fault occurred.
type Stage string

const (
	StageBody   Stage = "BODY"
	StageReduce Stage = "REDUCE"
)

// Fault is the first trap in scalar execution order, if any.
type Fault struct {
	Code      FaultCode `json:"code"`
	GlobalIdx int       `json:"global_idx"`
	StmtID    int       `json:"stmt_id"`
	Stage     Stage     `json:"stage"`
	Detail    string    `json:"detail,omitempty"`
}

// Outputs are the final values of declared outputs.
type Outputs struct {
	Scalars map[string]int64   `json:"scalars"`
	Arrays  map[string][]int64 `json:"arrays"`
}

// Result is one execution's observable result.
type Result struct {
	Halted  bool    `json:"halted"`
	Fault   *Fault  `json:"fault,omitempty"`
	Outputs Outputs `json:"outputs"`
}
