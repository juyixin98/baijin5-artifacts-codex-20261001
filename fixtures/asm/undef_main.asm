# calls a symbol nobody defines: strong undefined in a reachable section
.object undef_main.o
.section .text.main
.globl main
.export main
main:
call ghost
halt
