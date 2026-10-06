module pmd/tests

go 1.22

require (
	pmd/diff v0.0.0
	pmd/frontend v0.0.0
	pmd/ir v0.0.0
	pmd/runtime v0.0.0
	pmd/service v0.0.0
)

replace pmd/diff => ../diff

replace pmd/frontend => ../frontend

replace pmd/ir => ../ir

replace pmd/runtime => ../runtime

replace pmd/service => ../service
