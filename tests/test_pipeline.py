"""The real scripts against REAL Postgres 16 servers (stub docker maps containers to local processes). Skipped when the
server package or psycopg is missing, as on the demo instance."""
import json
import os
import re
import subprocess
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import stack  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")


@unittest.skipUnless(stack.AVAILABLE, stack.SKIP_REASON)
class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = stack.Stack().start()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def run_script(self, *args, timeout=300):
        r = subprocess.run(["bash", os.path.join(stack.ROOT, "scripts", args[0]), *args[1:]], env=self.s.script_env(),
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, ANSI.sub("", r.stdout + r.stderr)

    def scenario(self, name):
        code, out = self.run_script("demo.sh", name)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  " + name, out)
        return out

    def test_1_smoke(self):
        code, out = self.run_script("smoke.sh")
        self.assertEqual(code, 0, out)
        self.assertIn("All 13 checks passed.", out)

    def test_2_backup(self):
        self.scenario("backup")

    def test_3_oops(self):
        self.scenario("oops")

    def test_4_drill(self):
        self.scenario("drill")

    def test_5_retention(self):
        self.scenario("retention")

    def test_6_crash(self):
        out = self.scenario("crash")
        self.assertIn("RTO", out)
        state = json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.s.writer_url + "/state"))
        self.assertIsNotNone(state["recovery"])
        self.assertLessEqual(state["recovery"]["data_loss_seconds"] or 0, 20)


if __name__ == "__main__":
    unittest.main()
