import unittest

from core.budget import TokenBudget, estimate_tokens


class FakeUsage:
    input = 120
    output = 30

    @property
    def total(self):
        return 150


class BudgetTests(unittest.TestCase):
    def test_estimate_mixed(self):
        self.assertGreater(estimate_tokens("你好，world"), 0)
        self.assertGreater(estimate_tokens("你好" * 100), 20)

    def test_reserve_guard(self):
        budget = TokenBudget(max_total=1000, max_output_per_call=100)
        self.assertTrue(budget.can_afford("short", 100))
        self.assertFalse(budget.can_afford("x" * 2000, 100))
        self.assertTrue(budget.remaining > 0)

    def test_add_actual_usage(self):
        budget = TokenBudget(max_total=1000, max_output_per_call=100)
        used = budget.add(prompt="hello", output="world", usage=FakeUsage())
        self.assertEqual(used.total, 150)
        self.assertEqual(budget.input_tokens, 120)
        self.assertEqual(budget.output_tokens, 30)
        self.assertEqual(budget.calls, 1)

    def test_estimate_without_usage(self):
        budget = TokenBudget(max_total=1000, max_output_per_call=100)
        used = budget.add(prompt="你好世界" * 10, output="好的")
        self.assertGreater(used.total, 0)
        self.assertEqual(budget.calls, 1)


if __name__ == "__main__":
    unittest.main()
