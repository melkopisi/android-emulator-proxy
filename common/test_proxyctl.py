import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import proxyctl
from proxyctl import ProbeResult, verdict

OK = ProbeResult(True, "204", 0.1)
DOWN = ProbeResult(False)


class VerdictTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(verdict(True, OK, OK)["state"], "healthy")

    def test_local_port_dead_is_proxy_down(self):
        self.assertEqual(verdict(False, OK, OK)["state"], "proxy_down")

    def test_direct_ok_but_proxy_fails_is_stale(self):
        self.assertEqual(verdict(True, OK, DOWN)["state"], "proxy_stale")

    def test_proxy_gateway_error_counts_as_failure(self):
        # privoxy/mitmproxy answer 502/503 themselves when forwarding fails
        self.assertEqual(verdict(True, OK, ProbeResult(True, "503", 0.2))["state"], "proxy_stale")

    def test_internal_privoxy_error_counts_as_failure(self):
        # seen live: orphaned privoxy whose microsocks died answers "500 Internal Privoxy Error"
        self.assertEqual(verdict(True, OK, ProbeResult(True, "500", 0.01))["state"], "proxy_stale")

    def test_gateway_error_from_the_target_itself_is_fine(self):
        same = ProbeResult(True, "503", 0.2)
        self.assertEqual(verdict(True, same, same)["state"], "healthy")

    def test_both_paths_fail_is_upstream_down(self):
        self.assertEqual(verdict(True, DOWN, DOWN)["state"], "upstream_down")

    def test_proxy_error_page_while_upstream_down_is_upstream_down(self):
        self.assertEqual(verdict(True, DOWN, ProbeResult(True, "503", 0.1))["state"], "upstream_down")

    def test_slow(self):
        self.assertEqual(verdict(True, OK, ProbeResult(True, "204", 3.5), 2.0)["state"], "slow")

    def test_auth_errors_count_as_reachable(self):
        r = ProbeResult(True, "401", 0.1)
        self.assertEqual(verdict(True, r, r)["state"], "healthy")

    def test_only_restart_fixable_states_heal(self):
        heal = {s for s, (_, _, h) in proxyctl.STATES.items() if h}
        self.assertNotIn("upstream_down", heal)
        self.assertIn("proxy_stale", heal)


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.multiple(proxyctl, STATE_DIR=Path(self.tmp.name),
                                      LOCK_FILE=Path(self.tmp.name) / "proxy.lock")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_second_lock_fails_until_released(self):
        a, b = proxyctl.InstanceLock(), proxyctl.InstanceLock()
        self.assertTrue(a.acquire())
        self.assertTrue(proxyctl.instance_running())
        self.assertFalse(b.acquire())
        a.release()
        self.assertFalse(proxyctl.instance_running())
        self.assertTrue(b.acquire())
        b.release()

    def test_lock_released_when_owner_dies(self):
        pid = os.fork()
        if pid == 0:
            proxyctl.InstanceLock().acquire()
            time.sleep(0.5)
            os._exit(0)  # dies without releasing
        time.sleep(0.2)
        self.assertTrue(proxyctl.instance_running())
        os.waitpid(pid, 0)
        self.assertFalse(proxyctl.instance_running())


class FormatTests(unittest.TestCase):
    def test_fmt_age(self):
        self.assertEqual(proxyctl.fmt_age(5), "5s")
        self.assertEqual(proxyctl.fmt_age(125), "2m")
        self.assertEqual(proxyctl.fmt_age(3 * 3600 + 120), "3h 2m")

    def test_swiftbar_not_running_offers_start(self):
        out = proxyctl.render_swiftbar({"running": False, "state": "not_running", "summary": ""},
                                       dict(proxyctl.DEFAULT_CONFIG))
        self.assertTrue(out.startswith("⚪"))
        self.assertIn("Settings", out)


if __name__ == "__main__":
    unittest.main()
