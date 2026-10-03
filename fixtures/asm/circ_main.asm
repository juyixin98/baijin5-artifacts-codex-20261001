# computes iseven(10)
.object circ_main.o
.section .text.main
.globl main
.export main
main:
pushi 10
call iseven
halt
