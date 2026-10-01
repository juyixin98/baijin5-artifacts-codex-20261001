package runtime

import (
	"genstatemachine/internal/gerr"
	"genstatemachine/internal/ir"
)

// settlePending produces the terminal outcome for an abrupt that no handler
// frame can consume.
func (m *Machine) settlePending() Outcome {
	p := m.pending
	m.pending = nil
	logs := m.logs
	switch p.kind {
	case akRaised:
		m.setState(Failed)
		return Outcome{Kind: OutFailed, State: Failed, Logs: logs,
			ErrCode: string(gerr.CUserRaised),
			ErrMsg:  gerr.New(gerr.CUserRaised, "uncaught exception %q", p.value.S).Error(),
		}
	case akFailure:
		m.setState(Failed)
		return Outcome{Kind: OutFailed, State: Failed, Logs: logs,
			ErrCode: string(p.code), ErrMsg: p.msg}
	case akBudget:
		m.setState(Failed)
		return Outcome{Kind: OutFailed, State: Failed, Logs: logs,
			ErrCode: string(gerr.RStepBudget),
			ErrMsg:  gerr.New(gerr.RStepBudget, "step budget %d exhausted", m.budget).Error()}
	case akReturn:
		v := p.value
		if (v == Value{}) && m.unwindVal.Kind != 0 {
			v = m.unwindVal
		}
		m.setState(Done)
		return Outcome{Kind: OutExhausted, Value: nil, State: Done, Logs: logs}
	case akClosing:
		m.setState(Closed)
		return Outcome{Kind: OutClosedOK, State: Closed, Logs: logs}
	}
	m.setState(Failed)
	return Outcome{Kind: OutFailed, State: Failed, Logs: logs,
		ErrCode: string(gerr.CUncaughtExit), ErrMsg: "orphan control flow"}
}

func (m *Machine) setState(s State) {
	m.mu.Lock()
	m.state = s
	m.mu.Unlock()
}

func (m *Machine) settleInternal(code gerr.Code, format string, args ...any) Outcome {
	ge := gerr.New(code, format, args...)
	m.setState(Failed)
	return Outcome{Kind: OutFailed, State: Failed, Logs: m.logs,
		ErrCode: string(ge.Code), ErrMsg: ge.Error()}
}

func (m *Machine) settleType(format string, args ...any) Outcome {
	return m.settleInternal(gerr.CTypeMismatch, format, args...)
}

func (m *Machine) settleYield(t ir.Term) Outcome {
	// Yield during close is illegal: cleanup must not suspend.
	if m.pending != nil && m.pending.kind == akClosing {
		m.setState(Failed)
		return Outcome{Kind: OutFailed, State: Failed, Logs: m.logs,
			ErrCode: string(gerr.CYieldClosing),
			ErrMsg:  gerr.New(gerr.CYieldClosing, "yield executed while generator is closing").Error()}
	}
	var v *Value
	if t.Reg >= 0 && t.Reg < len(m.regs) {
		val := m.regs[t.Reg]
		v = &val
	}
	m.setState(Suspended)
	return Outcome{Kind: OutYielded, Value: v, State: Suspended, Logs: m.logs}
}
