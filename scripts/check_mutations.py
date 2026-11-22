"""Demonstrate that simulation detects six isolated RTL defects and formal refutes two."""

import shutil
import subprocess
import xml.etree.ElementTree as ET

from check import COCOTB, ROOT, RTL, TOP, simulate

MUTATIONS = (
    (
        "wrap_without_saturation", 1, "directed_arithmetic",
        "result_q <= saturate(next_total);", "result_q <= next_total[31:0];",
        "expected 2147483647, got -2147483648",
    ),
    (
        "ignore_lane_mask", 4, "directed_arithmetic",
        "in_mask[lane] ? 16'(activation) * 16'(weight) : 16'sd0",
        "16'(activation) * 16'(weight)",
        "expected 0, got 49152",
    ),
    (
        "drop_valid_during_stall", 4, "randomized_protocol",
        "assign result_valid = rst_n && state_q == HOLD;",
        "assign result_valid = rst_n && state_q == HOLD && result_ready;",
        "assert None == -32",
    ),
    (
        # Zero-extending one operand makes the whole product unsigned.
        "zero_extend_activation", 4, "directed_arithmetic",
        "in_mask[lane] ? 16'(activation) * 16'(weight) : 16'sd0",
        "in_mask[lane] ? 16'({8'd0, activation}) * 16'(weight) : 16'sd0",
        "expected 16383, got -16385",
    ),
    (
        # Completing while one beat is still outstanding ends the vector early.
        "complete_one_beat_early", 1, "directed_arithmetic",
        "assign final_beat = remaining_q <= LANE_COUNT;",
        "assign final_beat = remaining_q <= LANE_COUNT + 1'b1;",
        "premature result",
    ),
    (
        # Hides the previous RESULT from register reads while the next operation's
        # operands are being received; the specification keeps it readable.
        "result_read_zero_while_receiving", 4, "randomized_interruptions",
        "cfg_rdata = result_q;", "cfg_rdata = state_q == RECEIVE ? 32'd0 : result_q;",
        "expected register response",
    ),
)


def main():
    source = RTL.read_text(encoding="utf-8")
    for name, lanes, test, old, replacement, diagnostic in MUTATIONS:
        if source.count(old) != 1:
            raise RuntimeError(f"Cannot uniquely locate mutation site for {name}")
        build_dir = ROOT / "build" / "mutations" / name
        simulate([RTL], TOP, "edgemac_test", COCOTB, build_dir / "baseline",
                 (test,), {"LANES": lanes})

        mutated_dir = build_dir / "mutated"
        mutated_dir.mkdir(parents=True, exist_ok=True)
        mutated_rtl = mutated_dir / RTL.name
        mutated_rtl.write_text(source.replace(old, replacement), encoding="utf-8")
        try:
            simulate([mutated_rtl], TOP, "edgemac_test", COCOTB, mutated_dir,
                     (test,), {"LANES": lanes})
        except RuntimeError as error:
            if f"Required cocotb test {test}" not in str(error):
                raise
        else:
            raise RuntimeError(f"Mutation {name} escaped the test")
        cases = ET.parse(mutated_dir / "results.xml").findall(".//testcase")
        failures = [failure for case in cases if case.get("name") == test
                    for failure in case.findall("failure")]
        if len(failures) != 1:
            raise RuntimeError(f"Mutation {name} had no recorded assertion failure")
        observed = " ".join(failures[0].attrib.values()) + " " + (failures[0].text or "")
        if diagnostic not in observed:
            raise RuntimeError(f"Mutation {name} failed for an unexpected reason: {observed}")
        print(f"Detected {name} on {lanes} lane(s) in {test}", flush=True)
    # The unbounded 1024-element protocol proof must refute the stall defect, and the
    # full-range INT8 arithmetic check must refute the sign-extension defect.
    formal_mutation(source, MUTATIONS[2], "edgemac.sby", "one_prove_full")
    formal_mutation(source, MUTATIONS[3], "arithmetic.sby", "four_bmc_int8")


def formal_mutation(source, mutation, sby_file, task):
    """Require a formal task to fail with a counterexample on a mutated copy."""
    name, _, _, old, replacement, _ = mutation
    tree = ROOT / "build" / "mutations" / f"formal_{name}"
    shutil.rmtree(tree, ignore_errors=True)
    (tree / "rtl").mkdir(parents=True)
    shutil.copytree(ROOT / "verification" / "formal", tree / "verification" / "formal")
    (tree / "rtl" / RTL.name).write_text(source.replace(old, replacement), encoding="utf-8")
    result = subprocess.run(
        ["sby", "-f", "-d", f"build/{task}", f"verification/formal/{sby_file}", task],
        cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    status_file = tree / "build" / task / "status"
    status = status_file.read_text(encoding="utf-8").split()[0] if status_file.exists() else None
    if result.returncode == 0 or status != "FAIL" or "failed assertion" not in result.stdout:
        print(result.stdout)
        raise RuntimeError(f"Formal mutation {name} was not refuted by {task} (status {status})")
    print(f"Refuted {name} with counterexample in {task}", flush=True)


if __name__ == "__main__":
    main()
