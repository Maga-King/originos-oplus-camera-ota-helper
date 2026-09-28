.text
.macro private_formats reg, yes
    mov w9, #0x0c00
    movk w9, #0x7fa3, lsl #16
    cmp \reg, w9
    b.eq \yes
    add w9, w9, #1
    cmp \reg, w9
    b.eq \yes
    add w9, w9, #2
    cmp \reg, w9
    b.eq \yes
    add w9, w9, #1
    cmp \reg, w9
    b.eq \yes
    add w9, w9, #2
    cmp \reg, w9
    b.eq \yes
    add w9, w9, #1
    cmp \reg, w9
    b.eq \yes
    add w9, w9, #2
    cmp \reg, w9
    b.eq \yes
    add w9, w9, #1
    cmp \reg, w9
    b.eq \yes
    cmp \reg, #0x113
    b.eq \yes
    cmp \reg, #0x114
    b.eq \yes
    cmp \reg, #0x116
    b.eq \yes
    cmp \reg, #0x124
    b.eq \yes
.endm
.global plane_hook
plane_hook:
    private_formats w21, ycbcr_resume
    cmp w21, w8
    b.gt upper_resume
    b lower_resume
.global classify_hook
classify_hook:
    private_formats w0, classify_yes
    mov w9, #0x3858
    b classify_resume
classify_yes:
    mov w0, #1
    ret
