import sqlite3
import subprocess
from pathlib import Path


def test_admin_demo_command_seeds_an_isolated_database(tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    database = tmp_path / "relay-demo.db"
    command = [
        str(project_root / "scripts" / "run_admin_demo.sh"),
        "--seed-only",
        "--seed",
        "7",
        "--database",
        str(database),
    ]
    result = subprocess.run(command, cwd=project_root, capture_output=True, text=True, check=True)

    assert "Relay admin demo is ready" in result.stdout
    assert "24 users" in result.stdout
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM teams").fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 24
        assert connection.execute("SELECT count(*) FROM usage_records").fetchone()[0] > 1_000
        assert connection.execute("SELECT count(*) FROM mcp_approvals WHERE status = 'pending'").fetchone()[0] == 5
        assert connection.execute("SELECT count(*) FROM mcp_approval_grants").fetchone()[0] == 5
        assert connection.execute("SELECT count(*) FROM mcp_approval_grant_offers").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM mcp_policy_versions").fetchone()[0] == 3
        assert connection.execute("SELECT active_version FROM mcp_policy_state").fetchone()[0] == "2026-07-demo"

    repeated = subprocess.run(command, cwd=project_root, capture_output=True, text=True)
    assert repeated.returncode != 0
    assert "Demo database is not empty" in repeated.stderr
