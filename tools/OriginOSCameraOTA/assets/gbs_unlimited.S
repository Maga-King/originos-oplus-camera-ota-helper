.text
.global origin_gbs_unlimited
origin_gbs_unlimited:
    stp x29, x30, [sp, #-16]!
    ldr x0, [x19, #0xf0]
    cbz x0, 1f
    ldr x8, [x0]
    ldr x8, [x8, #0x78]
    mov w1, #1
    blr x8
1:
    ldp x29, x30, [sp], #16
    mov w0, #0x28
    ret
