// Manual OriginOS 17 patch, CameraDeviceClient::endConfigureLocked call site.
// x20=validated BasicClient, x23=sessionParams, x8=original virtual function.
.text
.global origin_package_hook
.type origin_package_hook,%function
origin_package_hook:
    sub sp, sp, #96
    stp x0, x1, [sp, #0]
    stp x2, x3, [sp, #16]
    stp x4, x5, [sp, #32]
    stp x6, x7, [sp, #48]
    stp x8, x30, [sp, #64]
    // BasicClient getter points to optional<string> at +0x58.
    ldrb w9, [x20, #0x70]
    cbz w9, pass_through
    add x10, x20, #0x58
    ldrb w9, [x10]
    tbnz w9, #0, long_name
    lsr w11, w9, #1
    add x0, x10, #1
    b check_name
long_name:
    ldr x11, [x10, #8]
    ldr x0, [x10, #16]
check_name:
    cmp x11, #16
    b.ne pass_through
    cbz x0, pass_through
    adr x1, camera_name
    mov x2, #16
    bl origin_memcmp
    cbnz w0, pass_through
    mov x0, x23
    ldr w1, package_tag
    adr x2, camera_name
    mov x3, #17
    bl origin_update_bytes
    mov w5, w0
    mov w0, #4
    adr x1, log_tag
    adr x2, log_format
    ldr w3, [x20, #0x4c]
    ldr w4, package_tag
    bl origin_log
pass_through:
    ldp x0, x1, [sp, #0]
    ldp x2, x3, [sp, #16]
    ldp x4, x5, [sp, #32]
    ldp x6, x7, [sp, #48]
    ldp x8, x30, [sp, #64]
    add sp, sp, #96
    // x16 is an ABI inter-procedure register: BR x16 remains a call-compatible
    // landing for BTI/PACIASP targets. BR x8 is a jump and trips BTI.
    mov x16, x8
    br x16
.size origin_package_hook, .-origin_package_hook
// RAW streams include OPlus private metadata buffers (1280x720 RAW16).
// Keep their dimensions; do not round them against public picture-size tables.
.global origin_raw_dimensions
.type origin_raw_dimensions,%function
origin_raw_dimensions:
    cmp w2, #32
    b.eq raw_dimensions
    cmp w2, #37
    b.eq raw_dimensions
    cmp w2, #38
    b.eq raw_dimensions
    // Generated from the chosen stock config, not a hard-coded phone model.
    adr x9, xpan_sizes
    mov w10, #MIO_XPAN_COUNT
1:
    cbz w10, normal_dimensions
    ldp w11, w12, [x9], #8
    cmp w0, w11
    b.ne 2f
    cmp w1, w12
    b.eq raw_dimensions
2:
    sub w10, w10, #1
    b 1b
normal_dimensions:
    paciasp
    b origin_round_resume
raw_dimensions:
    cmp w0, #0
    b.le normal_dimensions
    cmp w1, #0
    b.le normal_dimensions
    cbz x6, normal_dimensions
    cbz x7, normal_dimensions
    str w0, [x6]
    str w1, [x7]
    mov w0, #1
    ret
.size origin_raw_dimensions, .-origin_raw_dimensions
.global origin_filter_package
.type origin_filter_package,%function
origin_filter_package:
    stp x29, x30, [sp, #-80]!
    stp x19, x20, [sp, #16]
    mov x19, x0
    mov x20, x4
    bl origin_filter_parameters
    mov x0, x19
    ldr w1, package_tag
    add x8, sp, #32
    bl origin_find_const
    ldr x9, [sp, #48]
    cmp x9, #17
    b.ne filter_done
    ldr x0, [sp, #56]
    cbz x0, filter_done
    adr x1, camera_name
    mov x2, #17
    bl origin_memcmp
    cbnz w0, filter_done
    mov x0, x20
    ldr w1, package_tag
    adr x2, camera_name
    mov x3, #17
    bl origin_update_bytes
    mov w3, w0
    mov w0, #6
    adr x1, log_tag
    adr x2, filter_format
    bl origin_log
filter_done:
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #80
    ret
.size origin_filter_package, .-origin_filter_package
// No fall-through to this island: the preceding instruction is BR x8.
.balign 4
package_tag: .word MIO_PACKAGE_TAG
xpan_sizes:
#include "xpan_sizes.inc"
camera_name: .asciz "com.oplus.camera"
log_tag: .asciz "MIOOriginCamera"
log_format: .asciz "OnePlus session uid=%d packageTag=0x%08x update=%d"
filter_format: .asciz "preserve OnePlus package after session filter update=%d"
