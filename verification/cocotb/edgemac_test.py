"""Independent cycle-by-cycle verification and measurement of EdgeMAC."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import random

import cocotb
from cocotb.triggers import Timer

from verification.reference.oracle import INT32_MAX, INT32_MIN, edge_mac


CONTROL, STATUS, VECTOR_LENGTH, BIAS, RESULT, ERROR_STATUS = range(0, 0x18, 4)
U32 = (1 << 32) - 1
DEFAULT_TEST_SEED = 0xED6E2026
BENCHMARK_SEED = 0xED6EBEEF
BENCHMARK_LENGTHS = (1, 3, 4, 5, 7, 16, 63, 128, 255, 1024)


@dataclass(frozen=True)
class RegisterAccess:
    address: int
    write: int | None = None


@dataclass(frozen=True)
class OperandBeat:
    activations: int
    weights: int
    mask: int


@dataclass(frozen=True)
class Edge:
    cycle: int
    operand_transfer: OperandBeat | None
    output_before: int | None
    output_after: int | None
    output_transfer: int | None
    register_response: tuple[int, int] | None


def pack_bytes(values):
    return sum((value & 0xFF) << (8 * lane) for lane, value in enumerate(values))


def unpack_byte(word, lane):
    value = (word >> (8 * lane)) & 0xFF
    return value - 256 if value >= 128 else value


class Driver:
    def __init__(self, dut):
        self.dut = dut
        self.lanes = len(dut.activations) // 8
        assert self.lanes in (1, 4)
        self.cycle = 0

    def current_result(self):
        if int(self.dut.result_valid.value):
            return self.dut.result_data.value.to_signed()
        return None

    async def tick(self, *, access=None, beat=None, ready=False, reset=False):
        """Drive while low, observe transfers before rising, and sample new state."""
        dut = self.dut
        dut.clk.value = 0
        dut.rst_n.value = int(not reset)
        dut.cfg_valid.value = int(access is not None)
        dut.cfg_write.value = int(access is not None and access.write is not None)
        dut.cfg_addr.value = access.address if access is not None else 0
        dut.cfg_wdata.value = (access.write or 0) & U32 if access is not None else 0
        dut.in_valid.value = int(beat is not None)
        dut.activations.value = beat.activations if beat is not None else 0
        dut.weights.value = beat.weights if beat is not None else 0
        dut.in_mask.value = beat.mask if beat is not None else 0
        dut.result_ready.value = int(ready)
        await Timer(5, unit="ns")

        input_ready = int(dut.in_ready.value)
        output_before = self.current_result()
        operand_transfer = None
        if int(dut.in_valid.value) and input_ready:
            operand_transfer = OperandBeat(
                int(dut.activations.value), int(dut.weights.value), int(dut.in_mask.value)
            )
        register_response = None
        if access is not None and int(dut.cfg_ready.value):
            register_response = (int(dut.cfg_rdata.value), int(dut.cfg_error.value))
        if reset:
            assert not int(dut.cfg_ready.value), "register handshake during reset"
            assert not input_ready, "operand handshake during reset"
            assert output_before is None, "result handshake during reset"

        dut.clk.value = 1
        self.cycle += 1
        await Timer(5, unit="ns")
        output_after = self.current_result()
        if reset:
            assert output_after is None, "result present after reset edge"
        return Edge(
            self.cycle,
            operand_transfer,
            output_before,
            output_after,
            output_before if int(dut.result_ready.value) else None,
            register_response,
        )

    async def register(self, address, write=None, *, error=False):
        edge = await self.tick(access=RegisterAccess(address, write))
        assert edge.register_response is not None, f"cfg_ready low at 0x{address:02x}"
        data, observed_error = edge.register_response
        assert observed_error == error, (
            f"cycle {edge.cycle}, cfg access at 0x{address:02x}, write={write}: "
            f"expected error={error}, received {observed_error}"
        )
        if error:
            assert data == 0, f"cycle {edge.cycle}, nonzero error read data: {data}"
        return data

    async def configure(self, length, bias):
        await self.register(VECTOR_LENGTH, length)
        await self.register(BIAS, bias)

    async def reset(self, *, beat=None, access=None, ready=False):
        await self.tick(reset=True, beat=beat, access=access, ready=ready)
        idle = await self.tick(ready=True)
        assert idle.output_before is None and idle.output_after is None
        assert idle.operand_transfer is None
        for address in (STATUS, VECTOR_LENGTH, BIAS, RESULT, ERROR_STATUS):
            assert await self.register(address) == 0, f"register 0x{address:02x} survived reset"

    def make_beat(self, activations, weights):
        assert 1 <= len(activations) == len(weights) <= self.lanes
        unused = self.lanes - len(activations)
        # Nonzero inactive lanes expose arithmetic that ignores the final mask.
        return OperandBeat(
            pack_bytes([*activations, *([-128] * unused)]),
            pack_bytes([*weights, *([-128] * unused)]),
            (1 << len(activations)) - 1,
        )

    async def transaction(
        self, activations, weights, bias, *, input_gaps=None, output_stall=0
    ):
        expected = edge_mac(activations, weights, bias)
        planned = list(zip(activations, weights, strict=True))
        observed = []
        await self.configure(len(planned), bias)
        start = await self.tick(
            access=RegisterAccess(CONTROL, 1), ready=output_stall == 0
        )
        assert start.register_response is not None and start.register_response[1] == 0
        assert start.output_before is None and start.output_after is None

        accepted_beats = 0
        for beat_index, offset in enumerate(range(0, len(planned), self.lanes)):
            gaps = input_gaps[beat_index] if input_gaps is not None else 0
            for _ in range(gaps):
                idle = await self.tick(ready=output_stall == 0)
                assert idle.output_before is None and idle.output_after is None, (
                    f"result appeared during input gap on cycle {idle.cycle}"
                )
                assert idle.operand_transfer is None

            chunk = planned[offset:offset + self.lanes]
            row_activations, row_weights = zip(*chunk, strict=True)
            beat = self.make_beat(row_activations, row_weights)
            edge = await self.tick(beat=beat, ready=output_stall == 0)
            received = edge.operand_transfer
            assert received is not None, f"cycle {edge.cycle}, blocked operand beat {beat_index}"
            assert edge.output_before is None and edge.output_transfer is None, (
                f"cycle {edge.cycle}, result appeared before final operands were sampled"
            )
            received_pairs = [
                (unpack_byte(received.activations, lane), unpack_byte(received.weights, lane))
                for lane in range(self.lanes)
                if received.mask & (1 << lane)
            ]
            assert received_pairs == chunk, (
                f"cycle {edge.cycle}, operand stream order or count mismatch"
            )
            observed.extend(received_pairs)
            accepted_beats += 1
            if len(observed) < len(planned):
                assert edge.output_after is None, f"premature result on cycle {edge.cycle}"

        assert observed == planned
        assert edge.output_after == expected, (
            f"cycle {edge.cycle}, length={len(planned)} bias={bias}: "
            f"expected {expected}, got {edge.output_after}"
        )
        final_cycle = edge.cycle
        for _ in range(output_stall):
            stalled = await self.tick()
            assert stalled.output_before == expected and stalled.output_after == expected, (
                f"cycle {stalled.cycle}, unstable output during backpressure"
            )
            assert stalled.output_transfer is None
        ack = await self.tick(ready=True)
        assert ack.output_transfer == expected and ack.output_after is None, (
            f"cycle {ack.cycle}, wrong result at acknowledgement or duplicate result"
        )
        idle = await self.tick(ready=True)
        assert idle.output_transfer is None and idle.output_after is None, (
            f"cycle {idle.cycle}, unexpected extra result"
        )

        return {
            "length": len(planned),
            "output_stall_cycles": output_stall,
            "accepted_beats": accepted_beats,
            "start_to_result_cycles": final_cycle - start.cycle,
            "start_to_ack_cycles": ack.cycle - start.cycle,
            "accepted_pairs_per_input_cycle": len(planned) / (final_cycle - start.cycle),
            "accepted_pairs_per_operation_cycle": len(planned) / (ack.cycle - start.cycle),
        }


@cocotb.test()
async def directed_arithmetic(dut):
    driver = Driver(dut)
    await driver.reset()
    cases = (
        ([0], [-128], 0),
        ([127], [127], 1),
        ([-128], [-128], -1),
        ([-128], [127], 0),
        ([2, 3, 4], [5, 6, 7], 0),
        ([-2, -3, -4], [-5, -6, -7], -50),
        ([127, -128, 1, -1, 0, 7, -9], [-128, 127, -128, 127, 7, -9, 7], -123),
        ([1], [1], INT32_MAX - 1),
        ([1], [1], INT32_MAX),
        ([-1], [1], INT32_MIN + 1),
        ([-1], [1], INT32_MIN),
        # Cross the bound at a whole beat in both lane configurations, then return.
        ([1, 0, 0, 0, -1], [1] * 5, INT32_MAX),
        ([-1, 0, 0, 0, 1], [1] * 5, INT32_MIN),
        ([-128] * 1024, [-128] * 1024, INT32_MAX),
        ([-128] * 1024, [127] * 1024, INT32_MIN),
        ([-128] * 1024, [-128] * 1024, 0),
        ([-128] * 1024, [127] * 1024, 0),
    )
    for activations, weights, bias in cases:
        await driver.transaction(activations, weights, bias)
        assert await driver.register(RESULT) == (edge_mac(activations, weights, bias) & U32)
        assert await driver.register(STATUS) == 0b100


async def exercise_registers_and_masks(driver):
    for address, value in ((CONTROL, None), (STATUS, 1), (RESULT, 1), (0x18, None),
                           (0x03, 1), (CONTROL, 2), (ERROR_STATUS, 4)):
        await driver.register(address, value, error=True)
    for length in (0, 1025, U32):
        await driver.configure(length, -13)
        assert await driver.register(VECTOR_LENGTH) == length
        assert await driver.register(BIAS) == (U32 - 12)
        await driver.register(CONTROL, 1, error=True)
        assert await driver.register(ERROR_STATUS) == 1
        await driver.register(ERROR_STATUS, 1)
        assert await driver.register(ERROR_STATUS) == 0

    await driver.configure(1, -17)
    beat = driver.make_beat([-3], [5])
    start = await driver.tick(access=RegisterAccess(CONTROL, 1), beat=beat)
    assert start.register_response is not None and start.register_response[1] == 0
    assert start.operand_transfer is None and start.output_after is None
    final = await driver.tick(beat=beat)
    assert final.operand_transfer == beat
    assert final.output_after == -32
    assert await driver.register(STATUS) == 0b111
    assert await driver.register(RESULT) == (U32 - 31)
    collision = await driver.tick(access=RegisterAccess(CONTROL, 1), ready=True)
    assert collision.register_response == (0, 1), "busy start accepted during output handshake"
    assert collision.output_transfer == -32 and collision.output_after is None

    malformed_masks = (
        (length, mask)
        for length in range(1, driver.lanes + 2)
        for mask in range(1 << driver.lanes)
        if mask != (1 << min(length, driver.lanes)) - 1
    )
    for length, mask in malformed_masks:
        await driver.configure(length, 99)
        await driver.register(CONTROL, 1)
        assert await driver.register(STATUS) == 0b001
        for address, value in ((CONTROL, 1), (VECTOR_LENGTH, 3), (BIAS, 31)):
            await driver.register(address, value, error=True)
        assert await driver.register(VECTOR_LENGTH) == length
        assert await driver.register(BIAS) == 99
        bad = OperandBeat(0x7F, 0x80, mask)
        malformed = await driver.tick(beat=bad, access=RegisterAccess(ERROR_STATUS, 2))
        assert malformed.register_response is not None and malformed.register_response[1] == 0
        assert malformed.operand_transfer == bad
        assert malformed.output_after is None
        assert await driver.register(STATUS) == 0
        assert await driver.register(ERROR_STATUS) == 2, "new mask error lost to same-edge clear"
        assert await driver.register(RESULT) == (U32 - 31), "mask error clobbered previous result"
        await driver.register(ERROR_STATUS, 2)
        assert await driver.register(ERROR_STATUS) == 0

    await driver.transaction([2, 3, 4], [6, 7, 8], -14, output_stall=40)


async def exercise_reset(driver):
    for phase in ("receiving", "final_operand", "pending_output"):
        length = driver.lanes + 1 if phase == "receiving" else 1
        await driver.configure(length, 27)
        await driver.register(CONTROL, 1)
        if phase == "receiving":
            beat = driver.make_beat([1] * driver.lanes, [2] * driver.lanes)
            first = await driver.tick(beat=beat)
            assert first.operand_transfer == beat and first.output_after is None
        if phase == "pending_output":
            final = await driver.tick(beat=driver.make_beat([2], [5]))
            assert final.output_after == 37
        final_attempt = driver.make_beat([3], [-5]) if phase == "final_operand" else None
        await driver.reset(
            beat=final_attempt, access=RegisterAccess(BIAS, 11), ready=True
        )
        await driver.transaction([3, -5], [-7, 2], 9)


async def exercise_consecutive_operations(driver):
    bias = 6
    await driver.configure(1, bias)
    await driver.register(CONTROL, 1)
    first = await driver.tick(beat=driver.make_beat([8], [-4]))
    first_expected = edge_mac([8], [-4], bias)
    assert first.output_after == first_expected

    next_beat = driver.make_beat([-11], [-3])
    # Keep the following operand stable while the previous result is held and drained.
    stalled = await driver.tick(beat=next_beat)
    assert stalled.operand_transfer is None
    assert stalled.output_before == stalled.output_after == first_expected
    ack = await driver.tick(beat=next_beat, ready=True)
    assert ack.operand_transfer is None
    assert ack.output_transfer == first_expected and ack.output_after is None

    # Restart on the first idle edge, using the existing configuration.
    start = await driver.tick(access=RegisterAccess(CONTROL, 1), beat=next_beat)
    assert start.register_response == (0, 0)
    assert start.operand_transfer is None and start.output_after is None
    final = await driver.tick(beat=next_beat, ready=True)
    assert final.operand_transfer == next_beat and final.output_transfer is None
    expected = edge_mac([-11], [-3], bias)
    assert final.output_after == expected
    ack = await driver.tick(ready=True)
    assert ack.output_transfer == expected and ack.output_after is None
    idle = await driver.tick(ready=True)
    assert idle.output_before is None and idle.output_after is None


@cocotb.test()
async def randomized_protocol(dut):
    seed = int(os.environ.get("EDGEMAC_TEST_SEED", str(DEFAULT_TEST_SEED)), 0)
    rng = random.Random(seed)
    driver = Driver(dut)
    dut._log.info("LANES=%d EDGEMAC_TEST_SEED=%#x", driver.lanes, seed)
    await driver.reset()
    await exercise_registers_and_masks(driver)
    await exercise_reset(driver)
    await exercise_consecutive_operations(driver)
    await driver.transaction([1], [2], 3, input_gaps=[40])
    for index in range(64):
        length = rng.choice((1, 2, 3, 4, 5, 7, 8, rng.randint(1, 128)))
        activations = [rng.randint(-128, 127) for _ in range(length)]
        weights = [rng.randint(-128, 127) for _ in range(length)]
        bias = rng.choice((INT32_MIN, INT32_MAX, rng.randint(INT32_MIN, INT32_MAX)))
        gaps = [rng.randrange(3) for _ in range(0, length, driver.lanes)]
        try:
            await driver.transaction(
                activations, weights, bias, input_gaps=gaps, output_stall=rng.randrange(8)
            )
        except AssertionError as error:
            raise AssertionError(f"seed={seed:#x}, transaction={index}, length={length}") from error


def random_access(rng, phase, length, bias, previous_result, expected):
    """Pick a register access and predict its response from the specification."""
    holding = phase in ("stall", "ack")
    reads = {
        STATUS: 0b111 if holding else 0b001,
        RESULT: (expected if holding else previous_result) & U32,
        VECTOR_LENGTH: length,
        BIAS: bias & U32,
        ERROR_STATUS: 0,
    }
    kind = rng.choice(("read", "read", "busy_write", "clear", "invalid"))
    if kind == "read":
        address = rng.choice(tuple(reads))
        return RegisterAccess(address), (reads[address], 0)
    if kind == "busy_write":
        address, value = rng.choice(
            ((CONTROL, 1), (VECTOR_LENGTH, rng.randint(1, 1024)), (BIAS, rng.getrandbits(32)))
        )
        return RegisterAccess(address, value), (0, 1)
    if kind == "clear":
        return RegisterAccess(ERROR_STATUS, 0), (0, 0)
    return RegisterAccess(rng.choice((0x18, 0x02, 0xFC))), (0, 1)


async def interrupted_transaction(driver, rng, previous_result, reset_counts):
    """Run one transaction with register traffic on random edges and maybe a reset."""
    length = rng.choice((1, driver.lanes, driver.lanes + 1, rng.randint(1, 64)))
    activations = [rng.randint(-128, 127) for _ in range(length)]
    weights = [rng.randint(-128, 127) for _ in range(length)]
    bias = rng.randint(INT32_MIN, INT32_MAX)
    expected = edge_mac(activations, weights, bias)
    await driver.configure(length, bias)
    start = await driver.tick(access=RegisterAccess(CONTROL, 1))
    assert start.register_response == (0, 0)

    schedule = []
    for offset in range(0, length, driver.lanes):
        schedule += [("gap", None)] * rng.randrange(3)
        beat = driver.make_beat(
            activations[offset:offset + driver.lanes], weights[offset:offset + driver.lanes]
        )
        schedule.append(("beat", beat))
    schedule += [("stall", None)] * rng.randrange(6) + [("ack", None)]
    reset_at = rng.randrange(len(schedule)) if rng.random() < 0.35 else None
    last_beat = max(index for index, (phase, _) in enumerate(schedule) if phase == "beat")

    for index, (phase, beat) in enumerate(schedule):
        access, response = (None, None)
        if rng.random() < 0.6:
            access, response = random_access(rng, phase, length, bias, previous_result, expected)
        context = f"cycle {driver.cycle + 1}, phase={phase}, access={access}"
        if index == reset_at:
            reset_counts["final_beat" if index == last_beat else phase] += 1
            await driver.reset(beat=beat, access=access, ready=phase == "ack")
            return 0

        edge = await driver.tick(access=access, beat=beat, ready=phase == "ack")
        if access is not None:
            assert edge.register_response == response, (
                f"{context}: expected register response {response}, got {edge.register_response}"
            )
        if phase == "gap":
            assert edge.operand_transfer is None and edge.output_after is None, context
        elif phase == "beat":
            assert edge.operand_transfer == beat, f"{context}: operand beat not accepted"
            assert edge.output_before is None, f"{context}: premature result"
            assert edge.output_after == (expected if index == last_beat else None), (
                f"{context}: expected {expected if index == last_beat else None}, "
                f"got {edge.output_after}"
            )
        elif phase == "stall":
            assert edge.output_before == edge.output_after == expected, context
            assert edge.output_transfer is None, context
        else:
            assert edge.output_transfer == expected and edge.output_after is None, context
    assert await driver.register(STATUS) == 0b100
    assert await driver.register(RESULT) == expected & U32
    return expected & U32


@cocotb.test()
async def randomized_interruptions(dut):
    seed = int(os.environ.get("EDGEMAC_TEST_SEED", str(DEFAULT_TEST_SEED)), 0)
    rng = random.Random(seed ^ 0x5EED)
    driver = Driver(dut)
    dut._log.info("LANES=%d interruption seed=%#x", driver.lanes, seed ^ 0x5EED)
    await driver.reset()
    previous_result = 0
    reset_counts = {"gap": 0, "beat": 0, "final_beat": 0, "stall": 0, "ack": 0}
    for index in range(300):
        try:
            previous_result = await interrupted_transaction(
                driver, rng, previous_result, reset_counts
            )
        except AssertionError as error:
            raise AssertionError(f"seed={seed:#x}, transaction={index}") from error
    dut._log.info("Resets by phase: %s", reset_counts)
    # Fail rather than silently pass if the seed never reached a reset phase.
    for phase in ("beat", "final_beat", "stall", "ack"):
        assert reset_counts[phase] > 0, f"no reset landed in phase {phase}: {reset_counts}"


@cocotb.test()
async def all_vector_lengths(dut):
    driver = Driver(dut)
    await driver.reset()
    rng = random.Random(0xED6E0400)
    activations = [rng.randint(-128, 127) for _ in range(1024)]
    weights = [rng.randint(-128, 127) for _ in range(1024)]
    for length in range(1, 1025):
        bias = (0, INT32_MIN + 31, INT32_MAX - 31)[length % 3]
        try:
            await driver.transaction(activations[:length], weights[:length], bias)
        except AssertionError as error:
            raise AssertionError(f"LANES={driver.lanes}, length={length}") from error
    dut._log.info("Checked all 1024 legal vector lengths with LANES=%d", driver.lanes)


@cocotb.test()
async def benchmark_cycles(dut):
    driver = Driver(dut)
    await driver.reset()
    rng = random.Random(BENCHMARK_SEED)
    rows = []
    for length in BENCHMARK_LENGTHS:
        activations = [rng.randint(-128, 127) for _ in range(length)]
        weights = [rng.randint(-128, 127) for _ in range(length)]
        bias = rng.randint(INT32_MIN, INT32_MAX)
        for stall in (0, 8):
            row = await driver.transaction(activations, weights, bias, output_stall=stall)
            assert row["accepted_beats"] == (length + driver.lanes - 1) // driver.lanes
            assert row["start_to_ack_cycles"] - row["start_to_result_cycles"] == stall + 1
            rows.append(row)
    target = Path(os.environ.get("EDGEMAC_PERF_JSON", "edgemac-perf.json"))
    results = {"lanes": driver.lanes, "seed": BENCHMARK_SEED, "rows": rows}
    target.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    dut._log.info("Wrote benchmark measurements to %s", target.resolve())
