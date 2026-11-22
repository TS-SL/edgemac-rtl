import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def signed_products(dut):
    for left, right, expected in (
        (-128, -128, 16384),
        (-128, 127, -16256),
        (127, 127, 16129),
        (0, -128, 0),
    ):
        dut.left.value = left & 0xFF
        dut.right.value = right & 0xFF
        await Timer(1, unit="ns")
        assert dut.product.value.to_signed() == expected
