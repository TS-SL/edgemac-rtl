# EdgeMAC microarchitecture

```mermaid
flowchart LR
    CFG["Configuration and status"] --> CTRL["Idle / Receive / Hold"]
    STREAM["Packed INT8 operand pairs"] --> MUL["Signed multipliers (1 or 4)"]
    CTRL --> MASK["Expected mask and beat accounting"]
    MASK --> MUL
    MUL --> SUM["Beat reduction"]
    BIAS["Signed INT32 bias"] --> ACC["Signed 33-bit accumulator"]
    SUM --> ACC
    ACC --> CLIP["INT32 saturation on final beat"]
    CLIP --> OUT["Held result"]
    OUT --> CFG
    CTRL --> OUT
```

The single-clock controller receives one legal beat per cycle. A valid four-lane beat reduces two product pairs and then adds the pair sums. The result register is both the stream holding register and the most recent result for configuration reads. When the result stream stalls, the controller remains busy and the accumulator stays idle.

For a vector of length N, the no-gap input beat count is ceil(N / LANES). The result becomes visible after the final accepted beat. A subsequent edge acknowledges it. Configuration writes and output stalls consume separate cycles. Compare measured operation cycles separately from accepted input beats.
