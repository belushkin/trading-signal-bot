import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "manage.sh"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["start"], ["compose", "up", "-d", "--build"]),
        (["stop"], ["compose", "stop"]),
        (["restart"], ["compose", "restart"]),
        (["status"], ["compose", "ps", "--all"]),
        (["logs"], ["compose", "logs", "--tail=100", "--follow"]),
        (["logs", "api"], ["compose", "logs", "--tail=100", "--follow", "api"]),
        (
            ["health"],
            ["compose", "exec", "-T", "app", "python", "-m", "stellar_intelligence_bot", "health"],
        ),
        (
            ["report", "--since-days", "90", "--horizon-minutes", "60"],
            [
                "compose",
                "exec",
                "-T",
                "app",
                "python",
                "-m",
                "stellar_intelligence_bot",
                "report",
                "--since-days",
                "90",
                "--horizon-minutes",
                "60",
            ],
        ),
        (["config"], ["compose", "config", "--quiet"]),
    ],
)
def test_manage_dispatches_from_repository_root(tmp_path, arguments, expected) -> None:
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\npwd\nprintf "%s\\n" "$@"\nexit "${FAKE_EXIT:-0}"\n')
    docker.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}

    result = subprocess.run(
        [str(SCRIPT), *arguments], cwd=tmp_path, env=env, text=True, capture_output=True
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [str(ROOT), *expected]


@pytest.mark.parametrize("arguments", [["start", "extra"], ["logs", "--help"], ["unknown"]])
def test_manage_rejects_invalid_arguments(tmp_path, arguments) -> None:
    result = subprocess.run(
        [str(SCRIPT), *arguments], cwd=tmp_path, text=True, capture_output=True
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_manage_help_does_not_require_docker(tmp_path) -> None:
    result = subprocess.run([str(SCRIPT), "help"], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0
    assert "start" in result.stdout
    assert "stop" in result.stdout


def test_manage_propagates_docker_failure(tmp_path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 42\n")
    docker.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    result = subprocess.run([str(SCRIPT), "health"], cwd=tmp_path, env=env, check=False)
    assert result.returncode == 42
