.object dead_main.o
.section .text.main
.globl main
.export main
main:
pushi 3
pushi 4
add
halt
