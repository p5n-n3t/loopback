"""Include the browser-free services view regressions in the standard test run."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for the services view tests')
class ServiceViewTests(unittest.TestCase):
    def test_service_recovery_controls(self):
        result = subprocess.run([shutil.which('node'), '--test', str(Path(__file__).with_name('dashboard_services.test.cjs'))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_overview_gauges(self):
        result = subprocess.run([shutil.which('node'), '--test', str(Path(__file__).with_name('dashboard_overview.test.cjs'))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_discovery_table(self):
        result = subprocess.run([shutil.which('node'), '--test', str(Path(__file__).with_name('dashboard_discovery.test.cjs'))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tool_policy_controls(self):
        result = subprocess.run([shutil.which('node'), '--test', str(Path(__file__).with_name('dashboard_tools.test.cjs'))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
