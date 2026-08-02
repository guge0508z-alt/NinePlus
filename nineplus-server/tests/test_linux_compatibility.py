from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from services.ninebot_service import NinebotService  # noqa: E402


class LinuxCompatibilityTests(unittest.TestCase):
    def test_ninecli_paths_can_be_overridden_for_systemd(self) -> None:
        environment = {
            "NINEBOT_CLI_PYTHON": "/opt/NinePlus/nineplus-server/.venv/bin/python",
            "NINEBOT_CLI_CONFIG": "/var/lib/nineplus-server/ninebot-config",
        }
        with patch.dict(os.environ, environment, clear=False):
            service = NinebotService()

        self.assertEqual(service._python_executable, Path(environment["NINEBOT_CLI_PYTHON"]))
        self.assertEqual(service._config_dir, Path(environment["NINEBOT_CLI_CONFIG"]))

    def test_linux_runtime_files_use_matching_paths(self) -> None:
        unit = (SERVER_ROOT / "nineplus-server.service").read_text(encoding="utf-8")
        deploy = (SERVER_ROOT / "DEPLOY_LINUX.md").read_text(encoding="utf-8")
        install_script = (SERVER_ROOT / "install.sh").read_text(encoding="utf-8")

        python_path = "/opt/NinePlus/nineplus-server/.venv/bin/python"
        config_path = "/var/lib/nineplus-server/ninebot-config"
        self.assertIn(python_path, unit)
        self.assertIn(python_path, deploy)
        self.assertIn("/var/lib/nineplus-server", unit)
        self.assertIn(config_path, deploy)
        self.assertIn('"${VENV_DIR}/bin/python"', install_script)

    def test_linux_dependencies_include_pinned_ninecli(self) -> None:
        requirements = (SERVER_ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("ninecli==0.1.7", requirements.splitlines())


if __name__ == "__main__":
    unittest.main()
