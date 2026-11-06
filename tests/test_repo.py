import os
import stat
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = ["scripts/" + f for f in os.listdir(os.path.join(ROOT, "scripts")) if f.endswith(".sh")] + ["docker/restore-prepare.sh"]


class Repo(unittest.TestCase):
    def test_scripts_parse_and_are_executable(self):
        for rel in SCRIPTS:
            path = os.path.join(ROOT, rel)
            self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR, rel + " is not executable")
            self.assertEqual(subprocess.run(["bash", "-n", path]).returncode, 0, rel)

    def test_required_files(self):
        for rel in ("docker-compose.yml", ".env.example", ".gitignore", ".gitattributes", "demo/writer.py", "demo/console.py",
                    "demo/Dockerfile", "README.md", "STEPS.md", "DEMO.md", "DIAGRAM_PROMPT.md"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_compose_has_archiving_and_local_ports(self):
        text = open(os.path.join(ROOT, "docker-compose.yml")).read()
        for needle in ("archive_mode=on", "archive_timeout=10", "127.0.0.1:9100", "127.0.0.1:5433", "profiles"):
            self.assertIn(needle, text)
        self.assertNotIn("0.0.0.0:5432", text)

    def test_demo_doc_is_brief(self):
        text = open(os.path.join(ROOT, "DEMO.md")).read()
        self.assertLess(len(text.split()), 450)
        self.assertLess(len(text.splitlines()), 40)

    def test_every_chmod_is_documented(self):
        steps = open(os.path.join(ROOT, "STEPS.md")).read()
        self.assertIn("chmod +x scripts/*.sh docker/*.sh", steps)


if __name__ == "__main__":
    unittest.main()
