"""Writer and console against a real local Postgres."""
import json
import os
import sys
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import stack  # noqa: E402


def get(url):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5).read().decode()


def post(url):
    req = urllib.request.Request(url, method="POST")
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5).read().decode()


@unittest.skipUnless(stack.AVAILABLE, stack.SKIP_REASON)
class Services(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = stack.Stack().start()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_writer_writes_contiguous_ids(self):
        time.sleep(2)
        self.assertEqual(self.s.psql("select count(*) = max(id) and min(id) = 1 from orders"), "t")

    def test_pause_and_resume(self):
        post(self.s.writer_url + "/pause")
        time.sleep(0.5)
        a = json.loads(get(self.s.writer_url + "/state"))["last_acked_id"]
        time.sleep(1.5)
        b = json.loads(get(self.s.writer_url + "/state"))["last_acked_id"]
        self.assertEqual(a, b)
        post(self.s.writer_url + "/resume")
        time.sleep(1.5)
        self.assertGreater(json.loads(get(self.s.writer_url + "/state"))["last_acked_id"], b)

    def test_console_shows_live_data(self):
        page = get(self.s.console_url + "/")
        self.assertIn("orders", page.lower())
        self.assertEqual(get(self.s.console_url + "/healthz").strip()[:2].lower(), "ok")


if __name__ == "__main__":
    unittest.main()
