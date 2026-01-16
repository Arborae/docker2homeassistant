import unittest
import sys
from pathlib import Path

# Fix path to allow importing d2ha
sys.path.append(str(Path(__file__).resolve().parents[1] / "d2ha"))
from services.utils import build_stable_id, read_system_uptime_seconds, format_timedelta, human_bytes

class TestUtils(unittest.TestCase):
    def test_build_stable_id_returns_string(self):
        info = {"stack": "my_stack", "name": "my_container"}
        sid = build_stable_id(info)
        self.assertEqual(sid, "my_stack_my_container")

    def test_build_stable_id_sanitizes(self):
        info = {"stack": "My Stack!", "name": "Cont@iner#1"}
        sid = build_stable_id(info)
        # "My Stack!" -> "mystack" (lower, alnum only?)
        # Let's check logic: "".join(c.lower() if c.isalnum() else "_" ...)
        # "My Stack!" -> "mystack_"
        # "Cont@iner#1" -> "cont_iner_1"
        # "my_stack__cont_iner_1" -> "my_stack_cont_iner_1"
        self.assertIsInstance(sid, str)
        self.assertFalse("__" in sid)
        self.assertFalse(sid.startswith("_"))
        self.assertFalse(sid.endswith("_"))

    def test_format_timedelta(self):
        self.assertEqual(format_timedelta(60), "1m")
        self.assertEqual(format_timedelta(3600), "1h")
        self.assertEqual(format_timedelta(3661), "1h 1m")

    def test_human_bytes(self):
        self.assertEqual(human_bytes(1024), "1.0KB")
        self.assertEqual(human_bytes(1024**2), "1.0MB")

    def test_read_system_uptime(self):
        # We can't easily rely on /proc/uptime in windows or unknown env, 
        # but we can check it returns float
        val = read_system_uptime_seconds()
        self.assertIsInstance(val, float)

if __name__ == "__main__":
    unittest.main()
