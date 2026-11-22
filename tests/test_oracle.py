import random
import unittest

from verification.reference.oracle import INT32_MAX, INT32_MIN, edge_mac


class OracleTests(unittest.TestCase):
    def test_hand_calculated_fixtures(self):
        fixtures = (
            ([0], [127], 0, 0),
            ([2, 3], [4, 5], 1, 24),
            ([-2, -3], [-4, -5], 0, 23),
            ([-128, 127, -3, 0, 4], [-128, -128, 7, -1, 2], -5, 110),
            ([-128] * 1024, [-128] * 1024, 0, 16_777_216),
            ([-128] * 1024, [127] * 1024, 0, -16_646_144),
            ([1], [1], INT32_MAX, INT32_MAX),
            ([-1], [1], INT32_MIN, INT32_MIN),
            ([-1], [1], INT32_MAX, INT32_MAX - 1),
            ([1], [1], INT32_MIN, INT32_MIN + 1),
            ([1, -1], [1, 1], INT32_MAX, INT32_MAX),
            ([-1, 1], [1, 1], INT32_MIN, INT32_MIN),
        )
        for activations, weights, bias, expected in fixtures:
            with self.subTest(length=len(activations), bias=bias, expected=expected):
                self.assertEqual(edge_mac(activations, weights, bias), expected)

    def test_rejects_invalid_inputs(self):
        for args in (
            ([], [], 0), ([0], [0, 0], 0), ([0] * 1025, [0] * 1025, 0),
            ([128], [0], 0), ([0], [-129], 0), ([True], [0], 0),
            ([0], [0.0], 0), ([0], [0], INT32_MAX + 1),
            ([0], [0], INT32_MIN - 1), ([0], [0], False),
        ):
            with self.subTest(args=repr(args)[:40]):
                with self.assertRaises(ValueError):
                    edge_mac(*args)

    def test_seeded_paired_cancellation(self):
        random_source = random.Random(0xED6E)
        activations = [random_source.randint(-128, 127) for _ in range(512)]
        weights = [random_source.randint(-127, 127) for _ in range(512)]
        self.assertEqual(
            edge_mac(activations * 2, weights + [-w for w in weights], -93),
            -93,
        )


if __name__ == "__main__":
    unittest.main()
