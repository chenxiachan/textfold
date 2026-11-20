"""A step of the PDF engine that fails says why, with the last line it wrote, and not its exit code alone (#140:
"mineru-kit.exe exited with 1" and nothing else, where the reason was in the log). Its progress bars are not a reason.
The step here is this Python, printing what a failing engine prints."""
import os
import sys
import unittest

from textfold import engine


def step(code):
    lines = []
    engine._run([sys.executable, "-c", code], dict(os.environ), lines.append, None)
    return lines


class FailedStep(unittest.TestCase):
    def test_the_error_is_its_last_line(self):
        code = ("import sys; print('Loading models'); print('Traceback (most recent call last):'); "
                "print('torch.OutOfMemoryError: CUDA out of memory'); sys.exit(1)")
        with self.assertRaises(RuntimeError) as got:
            step(code)
        self.assertRegex(str(got.exception), r"exited with 1: torch\.OutOfMemoryError: CUDA out of memory$")

    def test_a_progress_bar_is_not_the_reason(self):
        code = ("import sys; print('ValueError: no pages'); sys.stdout.write('Predict:  40%|####      | 2/5\\r'); "
                "sys.stdout.flush(); sys.exit(3)")
        with self.assertRaises(RuntimeError) as got:
            step(code)
        self.assertTrue(str(got.exception).endswith("exited with 3: ValueError: no pages"), str(got.exception))

    def test_a_step_that_wrote_nothing_has_its_code(self):
        with self.assertRaises(RuntimeError) as got:
            step("import sys; sys.exit(2)")
        self.assertTrue(str(got.exception).endswith("exited with 2"), str(got.exception))

    def test_a_step_that_ends_well_writes_its_lines(self):
        self.assertEqual(step("print('done')"), ["done"])


if __name__ == "__main__":
    unittest.main()
