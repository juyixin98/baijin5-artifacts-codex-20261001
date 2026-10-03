package frontend

// VM instruction set executed by the runtime interpreter.
// Every opcode is a single byte; operands are little-endian int32.
// The machine is a stack machine with a separate call stack.
const (
	OpPush    byte = 0x01 // pushi <imm>       | push imm32
	OpAdd     byte = 0x02 // add               | a+b
	OpSub     byte = 0x03 // sub               | a-b
	OpMul     byte = 0x04 // mul               | a*b
	OpCall    byte = 0x05 // call <rel32>      | call within same section (PC-relative)
	OpRet     byte = 0x06 // ret               | return top of data stack
	OpLoad    byte = 0x07 // load <rel32>      | push word at PC+rel32 (same section data word)
	OpCallInd byte = 0x08 // callind           | call address popped from stack
	OpJz      byte = 0x09 // jz <rel32>        | pop cond; if zero jump PC+rel32
	OpPop     byte = 0x0A // pop               | discard top
	OpHalt    byte = 0x0F // halt              | stop with top of stack
)
