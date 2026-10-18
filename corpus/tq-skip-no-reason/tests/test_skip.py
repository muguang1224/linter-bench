import unittest

class TestSkip(unittest.TestCase):
    @unittest.skip
    def test_x(self):
        value = 1
        self.assertEqual(value + 1, 2)
