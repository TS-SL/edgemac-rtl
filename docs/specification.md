# EdgeMAC specification

EdgeMAC computes `clamp32(bias + sum(a[i] * w[i]))` for 1–1024 signed INT8 pairs and a signed INT32 bias. Saturation applies once, after the complete mathematical sum, to [-2,147,483,648, 2,147,483,647].

A product is in [-16,256, 16,384]. The endpoints come from -128 × 127 and -128 × -128. A signed 16-bit product is sufficient. At 1024 elements the unbiased sum ranges from -16,646,144 to 16,777,216, including all prefixes. With bias the combined range is [-2,164,129,792, 2,164,260,863]. Both endpoints exceed the signed 32-bit range, and both fit signed 33 bits. The RTL uses 33 bits for the running total, initialized directly from sign-extended bias. Each product is sign-extended before addition. Prefixes including the bias also fit this width, so sequential accumulation and adding the bias last are equivalent.

## Streams and clock

One clock domain. Active-low synchronous reset has highest priority at a rising edge, cancels any operation, drops a stalled result, and clears configuration/status. Handshake outputs are gated while reset is asserted.

The input has `in_valid`, `in_ready`, packed `activations` and `weights`, and `in_mask`. Lane zero occupies bits 7:0. The producer holds valid and payload stable until a rising edge accepts the beat. The required mask is exactly the contiguous low `min(remaining, LANES)` bits. Unused payload lanes have no effect. A malformed accepted beat aborts the transaction, sets the mask error, and emits no result.

The output has `result_valid`, `result_ready`, and signed 32-bit `result_data`. The last accepted operand beat produces a result visible after that edge. The block holds result and valid stable until an output handshake, then returns idle. Input consumption cannot occur on the start edge. The three states are idle, receiving, and holding a result. Busy includes a pending output. A new transaction needs a successful start from idle.

## Register interface

This single-cycle synchronous word interface uses `cfg_valid`, `cfg_write`, `cfg_addr[7:0]`, `cfg_wdata[31:0]`, `cfg_ready`, `cfg_rdata[31:0]`, and `cfg_error`. Each rising-edge handshake is one full-word access. There are no byte strobes. Invalid or unaligned addresses, unauthorized writes, and reserved nonzero write bits return error and have no register-write side effects. Read data is zero on errors. A well-formed START rejected for an invalid length sets the sticky length error. This interface uses its own ready/valid protocol.

| Address | Register | Access | Reset | Semantics |
| --- | --- | --- | --- | --- |
| 0x00 | CONTROL | WO | — | Exact data 1 requests start. Idle with an illegal length rejects start and sets length error. Start while busy returns error without modifying the operation. |
| 0x04 | STATUS | RO | 0 | Bit 0 busy, bit 1 result pending, bit 2 completion sticky, remaining bits zero. Completion clears on successful start. |
| 0x08 | VECTOR_LENGTH | RW | 0 | Full 32-bit value. Writes only while idle. Validated at start. |
| 0x0C | BIAS | RW | 0 | Signed INT32 bit pattern. Writes only while idle. |
| 0x10 | RESULT | RO | 0 | Most recent completed result, independent of stream acknowledgement. |
| 0x14 | ERROR_STATUS | RW1C | 0 | Bit 0 invalid start length, bit 1 malformed mask. Other bits must be zero on writes. A same-edge new hardware error wins over clearing it. |

Reads and error clears are permitted while busy. A busy collision at the final output handshake still uses the pre-edge busy state. The previous RESULT survives errors and stays readable until a later successful operation completes or reset clears it.
