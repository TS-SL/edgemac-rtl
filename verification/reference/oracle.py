"""Scalar, arbitrary-precision integer model of the EdgeMAC operation."""

from collections.abc import Sequence

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


def edge_mac(activations: Sequence[int], weights: Sequence[int], bias: int) -> int:
    if not 1 <= len(activations) <= 1024 or len(activations) != len(weights):
        raise ValueError("vectors must have the same length in 1..1024")
    if type(bias) is not int or not INT32_MIN <= bias <= INT32_MAX:
        raise ValueError("bias must be a signed INT32 integer")

    total = bias
    for activation, weight in zip(activations, weights, strict=True):
        if type(activation) is not int or not -128 <= activation <= 127:
            raise ValueError("activation must be a signed INT8 integer")
        if type(weight) is not int or not -128 <= weight <= 127:
            raise ValueError("weight must be a signed INT8 integer")
        total += activation * weight
    return max(INT32_MIN, min(INT32_MAX, total))
