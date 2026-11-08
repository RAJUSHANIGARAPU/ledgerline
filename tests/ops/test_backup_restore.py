"""Backup to a local restic repository, then the restore drill against it (needs Docker)."""

import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRILL = ROOT / "scripts" / "restore-drill.sh"
RESTIC_IMAGE = "restic/restic:0.19.1"

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="Docker not available",
)


def run(cmd, env, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT, **kwargs)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("opsbackup")
    repo = tmp / "repo"
    repo.mkdir()
    project = f"ledgerline-opstest-{secrets.token_hex(3)}"
    override = tmp / "override.yml"
    override.write_text(
        "services:\n  backup:\n    volumes:\n"
        f"      - {repo}:/restic-repo\n      - {tmp / 'local'}:/backups\n"
    )
    env = {
        **os.environ,
        "POSTGRES_PASSWORD": secrets.token_hex(12),
        "LEDGERLINE_APP_DB_PASSWORD": secrets.token_hex(12),
        "LEDGERLINE_SECRET_KEY": secrets.token_hex(12),
        "LEDGERLINE_BIND": "127.0.0.1:",
        "RESTIC_PASSWORD": "test-restic-password",
        "COMPOSE_PROJECT_NAME": project,
        "COMPOSE_FILE": f"{ROOT / 'docker-compose.yml'}:{override}",
    }
    for key in (
        "RESTIC_REPOSITORY",
        "LEDGERLINE_INITIAL_OWNER_EMAIL",
        "LEDGERLINE_INITIAL_PASSWORD",
    ):
        env.pop(key, None)
    try:
        up = run(["docker", "compose", "up", "-d", "--wait", "db"], env)
        assert up.returncode == 0, up.stderr
        migrate = run(["docker", "compose", "run", "--rm", "migrate"], env)
        assert migrate.returncode == 0, migrate.stderr
        seed = (
            "INSERT INTO tenants (name) VALUES ('Alpha'), ('Beta'); "
            "INSERT INTO users (email, password_hash, is_active) "
            "VALUES ('a@example.com', 'x', true), ('b@example.com', 'x', true);"
        )
        inserted = run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "ledgerline",
                "-d",
                "ledgerline",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                seed,
            ],
            env,
        )
        assert inserted.returncode == 0, inserted.stderr
        yield {"env": env, "repo": repo, "tmp": tmp}
    finally:
        run(["docker", "compose", "down", "-v", "--remove-orphans"], env)


def backup(stack):
    env = stack["env"]
    return run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "BACKUP_ONCE=1",
            "-e",
            "RESTIC_REPOSITORY=/restic-repo",
            "backup",
        ],
        env,
    )


def drill(stack, skip_app, extra_env=None):
    env = {
        **os.environ,
        "RESTIC_REPOSITORY": str(stack["repo"]),
        "RESTIC_PASSWORD": "test-restic-password",
        "DRILL_SKIP_APP": "1" if skip_app else "0",
        "DRILL_PROJECT": stack["env"]["COMPOSE_PROJECT_NAME"] + "-d",
        **(extra_env or {}),
    }
    return run(["sh", str(DRILL), "--env-file", "/nonexistent"], env)


def test_backup_writes_local_and_offsite_copies_and_a_marker(stack):
    result = backup(stack)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "off-site snapshot stored" in result.stdout
    local = list((stack["tmp"] / "local").glob("ledgerline-*.dump"))
    manifests = list((stack["tmp"] / "local").glob("ledgerline-*.manifest.txt"))
    assert len(local) == 1 and len(manifests) == 1
    text = manifests[0].read_text()
    assert "\ntenants\t3\n" in text and "\nusers\t2\n" in text
    marker = run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "cat",
            "backup",
            "/ops/last_backup_ok",
        ],
        stack["env"],
    )
    assert "offsite=1" in marker.stdout


def test_drill_passes_on_the_latest_snapshot_and_starts_the_app(stack):
    result = drill(stack, skip_app=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tables match" in result.stdout
    assert '/healthz: {"status":"ok"}' in result.stdout
    assert result.stdout.strip().endswith("RESULT: PASS")


def test_drill_fails_when_a_manifest_count_does_not_match(stack):
    current = stack["tmp"] / "tampered" / "current"
    current.mkdir(parents=True)
    local = stack["tmp"] / "local"
    shutil.copy(next(local.glob("ledgerline-*.dump")), current / "ledgerline.dump")
    manifest = next(local.glob("ledgerline-*.manifest.txt")).read_text()
    assert "\nusers\t2\n" in manifest
    (current / "manifest.txt").write_text(manifest.replace("\nusers\t2\n", "\nusers\t3\n"))
    store = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{stack['repo']}:/repo",
            "-v",
            f"{current}:/in:ro",
            "-e",
            "RESTIC_PASSWORD=test-restic-password",
            "--entrypoint",
            "sh",
            RESTIC_IMAGE,
            "-c",
            "mkdir -p /work && cp -r /in /work/current && restic -r /repo backup --host ledgerline "
            "--tag ledgerline --quiet /work/current",
        ],
        capture_output=True,
        text=True,
    )
    assert store.returncode == 0, store.stderr
    result = drill(stack, skip_app=True)
    assert result.returncode == 1
    assert "users: expected 3, restored 2" in result.stdout
    assert result.stdout.strip().endswith("RESULT: FAIL - row counts differ from the manifest")


def test_drill_fails_with_the_wrong_repository_password(stack):
    result = drill(stack, skip_app=True, extra_env={"RESTIC_PASSWORD": "wrong"})
    assert result.returncode == 1
    assert "RESULT: FAIL - restic restore failed" in result.stdout


def test_backup_with_the_wrong_repository_password_fails_loudly(stack):
    env = stack["env"]
    cmd = ["docker", "compose", "run", "--rm", "--no-deps", "-e", "BACKUP_ONCE=1"]
    cmd += ["-e", "RESTIC_REPOSITORY=/restic-repo", "-e", "RESTIC_PASSWORD=wrong", "backup"]
    result = run(cmd, env)
    assert result.returncode != 0
    assert "wrong password" in result.stderr + result.stdout
    assert "FAILED" in result.stdout
