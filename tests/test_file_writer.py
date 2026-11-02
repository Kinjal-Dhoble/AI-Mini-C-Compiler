import os
import tempfile
import time
import unittest
from pathlib import Path
from ai_assistant.file_writer import SourceFile, confirm


class FileWriterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "prog.c"
        self.path.write_bytes(b"int main() {\r\n    return 1;\r\n}\r\n")

    def tearDown(self):
        self.dir.cleanup()

    def test_confirm_rules(self):
        self.assertTrue(confirm("q", True, input_fn=lambda _: "y"))
        self.assertFalse(confirm("q", True, input_fn=lambda _: ""))
        self.assertFalse(confirm("q", False, input_fn=lambda _: "y"))    # unverified needs 'yes'
        self.assertTrue(confirm("q", False, input_fn=lambda _: "yes"))

        def eof(_): raise EOFError
        self.assertFalse(confirm("q", True, input_fn=eof))

    def test_write_keeps_crlf_and_backs_up(self):
        sf = SourceFile(self.path)
        self.assertTrue(sf.offer("int main() {\n    return 2;\n}", True, "fix", input_fn=lambda _: "y"))
        self.assertEqual(self.path.read_bytes(), b"int main() {\r\n    return 2;\r\n}\r\n")
        self.assertIn(b"return 1;", (Path(self.dir.name) / "prog.c.bak").read_bytes())

    def test_declined_leaves_file(self):
        sf = SourceFile(self.path)
        before = self.path.read_bytes()
        self.assertFalse(sf.offer("int main() { return 3; }", True, "fix", input_fn=lambda _: "n"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((Path(self.dir.name) / "prog.c.bak").exists())

    def test_one_backup_per_run_and_no_overwrite_of_old_backup(self):
        (Path(self.dir.name) / "prog.c.bak").write_text("old backup")
        sf = SourceFile(self.path)
        sf.write("int main() { return 4; }")
        sf.write("int main() { return 5; }")
        self.assertEqual((Path(self.dir.name) / "prog.c.bak").read_text(), "old backup")
        self.assertIn("return 1;", (Path(self.dir.name) / "prog.c.bak1").read_text())
        self.assertFalse((Path(self.dir.name) / "prog.c.bak2").exists())

    def test_refuses_if_changed_on_disk(self):
        sf = SourceFile(self.path)
        time.sleep(0.01)
        self.path.write_text("edited elsewhere")
        os.utime(self.path, ns=(sf.mtime + 10**9, sf.mtime + 10**9))
        with self.assertRaises(RuntimeError):
            sf.write("int main() { return 6; }")


if __name__ == "__main__":
    unittest.main()
