import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SH = ROOT / "ops" / "backup" / "manifest.sh"
UPDATE_SH = ROOT / "deploy" / "hetzner" / "update.sh"
PROVISION_SH = ROOT / "deploy" / "hetzner" / "provision.sh"
CLOUD_INIT = ROOT / "deploy" / "hetzner" / "cloud-init.yaml"


def sh(script: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, **kwargs)


def counts(path: Path, rows: dict[str, int]) -> Path:
    path.write_text("".join(f"{t}\t{n}\n" for t, n in rows.items()))
    return path


@pytest.fixture
def manifest(tmp_path):
    dump = tmp_path / "ledgerline.dump"
    dump.write_bytes(b"dump-bytes")
    source = counts(tmp_path / "counts", {"users": 3, "accounts": 36, "journal_lines": 0})
    out = tmp_path / "manifest.txt"
    result = sh(f". {MANIFEST_SH}; write_manifest {source} {dump} 0005 {out}")
    assert result.returncode == 0, result.stderr
    return out, tmp_path


def test_manifest_records_header_and_every_table(manifest):
    out, _ = manifest
    text = out.read_text()
    assert text.splitlines()[0] == "# ledgerline backup manifest v1"
    assert "alembic_revision=0005" in text
    import hashlib

    assert f"dump_sha256={hashlib.sha256(b'dump-bytes').hexdigest()}" in text
    rows = dict(line.split("\t") for line in text.splitlines() if "\t" in line)
    assert rows == {"users": "3", "accounts": "36", "journal_lines": "0"}


def test_manifest_value_reads_header_keys(manifest):
    out, _ = manifest
    assert sh(f". {MANIFEST_SH}; manifest_value {out} alembic_revision").stdout.strip() == "0005"


def compare(manifest, rows):
    out, tmp = manifest
    actual = counts(tmp / "actual", rows)
    return sh(f". {MANIFEST_SH}; compare_counts {out} {actual}")


def test_matching_counts_pass_regardless_of_order(manifest):
    result = compare(manifest, {"users": 3, "journal_lines": 0, "accounts": 36})
    assert result.returncode == 0 and result.stdout == ""


def test_changed_count_fails_and_names_the_table(manifest):
    result = compare(manifest, {"users": 2, "accounts": 36, "journal_lines": 0})
    assert result.returncode == 1
    assert result.stdout.strip() == "users: expected 3, restored 2"


def test_missing_table_fails(manifest):
    result = compare(manifest, {"users": 3, "accounts": 36})
    assert result.returncode == 1
    assert "journal_lines: missing after restore (expected 0)" in result.stdout


def test_extra_table_in_restored_database_is_tolerated(manifest):
    assert (
        compare(
            manifest, {"users": 3, "accounts": 36, "journal_lines": 0, "new_table": 9}
        ).returncode
        == 0
    )


# update.sh rollback logic, with git, docker and curl replaced by recording stubs.

STUBS = {
    "git": r"""
        echo "git $*" >> "$LOG"
        case "$1" in
          status) ;;
          rev-parse) case "$2" in
            --abbrev-ref) echo main ;; --short) echo bbbbbbb ;; *) echo "$GIT_HEAD" ;;
          esac ;;
          *) ;;
        esac""",
    "curl": r"""
        echo "curl $*" >> "$LOG"
        [ -f "$STATE/unhealthy" ] && exit 22
        echo '{"status":"ok"}' """,
    "sleep": "exit 0",
}


def run_update(tmp_path, healthy_after_rollback=True, new_version_healthy=False, args=()):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "log"
    docker = f"""
        echo "docker $*" >> "$LOG"
        case "$*" in
          "compose up -d --build") rm -f "$STATE/unhealthy"
            {"" if new_version_healthy else 'touch "$STATE/unhealthy"'} ;;
          "compose up -d --no-build") rm -f "$STATE/unhealthy"
            {"" if healthy_after_rollback else 'touch "$STATE/unhealthy"'} ;;
          "compose ps -a -q"*) echo cid1 ;;
          inspect*) case "$*" in
            *Config.Image*) echo ledgerline-app ;; *) echo sha256:oldimage ;; esac ;;
        esac
        exit 0"""
    for name, body in {**STUBS, "docker": docker}.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    root = tmp_path / "repo"
    (root / "deploy" / "hetzner").mkdir(parents=True)
    shutil.copy(UPDATE_SH, root / "deploy" / "hetzner" / "update.sh")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LOG": str(log),
        "STATE": str(state),
        "GIT_HEAD": "a" * 40,
        "SKIP_BACKUP": "1",
        "HEALTH_TIMEOUT": "3",
    }
    result = subprocess.run(
        ["sh", str(root / "deploy" / "hetzner" / "update.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return result, log.read_text().splitlines() if log.exists() else [], root


def test_healthy_update_does_not_roll_back(tmp_path):
    result, log, _ = run_update(tmp_path, new_version_healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "docker compose up -d --build" in log
    assert not any("--no-build" in line or "checkout" in line for line in log)
    assert "healthy" in result.stdout


def test_unhealthy_update_rolls_back_to_previous_commit_and_images(tmp_path):
    result, log, root = run_update(tmp_path, new_version_healthy=False)
    assert result.returncode == 1
    assert "ROLLING BACK" in result.stdout and "rollback healthy" in result.stdout
    assert f"git checkout --quiet {'a' * 40}" in log
    keep = "docker tag sha256:oldimage ledgerline-app:rollback"
    restore = "docker tag ledgerline-app:rollback ledgerline-app"
    assert keep in log and restore in log
    assert log.index(keep) < log.index("docker compose up -d --build")
    assert log.index("docker compose up -d --build") < log.index(restore)
    assert log.index(restore) < log.index("docker compose up -d --no-build")
    assert (root / ".update-previous-images").read_text().splitlines() == [
        "ledgerline-app sha256:oldimage"
    ] * 4


def test_failed_rollback_is_reported_and_still_exits_nonzero(tmp_path):
    result, _, _ = run_update(tmp_path, healthy_after_rollback=False)
    assert result.returncode == 1
    assert "rollback did not become healthy" in result.stdout


def test_update_to_a_tag_checks_it_out(tmp_path):
    _, log, _ = run_update(tmp_path, new_version_healthy=True, args=("v1.2.0",))
    assert "git checkout --quiet v1.2.0" in log


# Provisioning files.


def test_cloud_init_is_valid_yaml_with_expected_hardening():
    doc = yaml.safe_load(CLOUD_INIT.read_text())
    assert doc["users"][0]["name"] == "deploy" and doc["users"][0]["lock_passwd"] is True
    assert {"unattended-upgrades", "fail2ban"} <= set(doc["packages"])
    paths = {f["path"]: f["content"] for f in doc["write_files"]}
    assert "PermitRootLogin no" in paths["/etc/ssh/sshd_config.d/99-ledgerline.conf"]
    assert "enabled = true" in paths["/etc/fail2ban/jail.d/ledgerline-sshd.local"]
    setup = paths["/opt/ledgerline-bootstrap/setup.sh"]
    assert "download.docker.com" in setup and "dl.cloudsmith.io/public/caddy" in setup


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
@pytest.mark.parametrize(
    "script",
    [
        "ops/backup/backup.sh",
        "ops/backup/entrypoint.sh",
        "ops/backup/manifest.sh",
        "scripts/restore-drill.sh",
        "deploy/hetzner/update.sh",
        "deploy/hetzner/provision.sh",
    ],
)
def test_shellcheck_clean(script):
    result = subprocess.run(
        ["shellcheck", "-x", "-S", "warning", "-P", "SCRIPTDIR", ROOT / script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout


def test_provision_dry_run_renders_firewall_and_server(tmp_path):
    key = tmp_path / "key.pub"
    key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc test@host\n")
    env = {
        **os.environ,
        "DRY_RUN": "1",
        "DOMAIN": "books.example.com",
        "OWNER_EMAIL": "me@example.com",
        "SERVER_TYPE": "TYPE",
        "OWNER_IP": "203.0.113.7",
        "SSH_PUBLIC_KEY_FILE": str(key),
        "HETZNER_BACKUPS": "1",
    }
    out = subprocess.run(["bash", str(PROVISION_SH)], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "--port 22 --source-ips 203.0.113.7/32" in out.stdout
    assert "--port 443 --source-ips 0.0.0.0/0 --source-ips ::/0" in out.stdout
    assert "--type TYPE" in out.stdout and "--enable-backup" in out.stdout
    assert " 22 " not in out.stdout.replace("--port 22", "").replace("203.0.113.7/32", "")


@pytest.mark.parametrize(
    "override",
    [
        {"DOMAIN": "bad domain;rm"},
        {"OWNER_EMAIL": "x"},
        {"REPO_REF": "a b"},
        {"LOCATION": "ash"},
        {"SERVER_TYPE": ""},
        {"OWNER_IP": "1.2.3"},
    ],
)
def test_provision_rejects_bad_input(tmp_path, override):
    key = tmp_path / "key.pub"
    key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc test@host\n")
    env = {
        **os.environ,
        "DRY_RUN": "1",
        "DOMAIN": "books.example.com",
        "OWNER_EMAIL": "me@example.com",
        "SERVER_TYPE": "TYPE",
        "OWNER_IP": "203.0.113.7",
        "SSH_PUBLIC_KEY_FILE": str(key),
        **override,
    }
    out = subprocess.run(["bash", str(PROVISION_SH)], capture_output=True, text=True, env=env)
    assert out.returncode == 1 and "error:" in out.stderr


def test_env_template_renders_without_leftover_placeholders(tmp_path):
    doc = yaml.safe_load(CLOUD_INIT.read_text())
    setup = {f["path"]: f["content"] for f in doc["write_files"]}[
        "/opt/ledgerline-bootstrap/setup.sh"
    ]
    sed_args = [
        line.strip() for line in setup.splitlines() if '-e "s|{{' in line or 'sed -e "s|{{' in line
    ]
    assert len(sed_args) == 6
    rendered = (ROOT / "deploy/hetzner/env.template").read_text()
    for key in (
        "SECRET_KEY",
        "POSTGRES_PASSWORD",
        "APP_DB_PASSWORD",
        "DOMAIN",
        "OWNER_EMAIL",
        "INITIAL_PASSWORD",
    ):
        assert any(f"{{{{{key}}}}}" in line for line in sed_args), key
        rendered = rendered.replace("{{" + key + "}}", "x")
    assert "{{" not in rendered


RESTORE_SH = ROOT / "scripts" / "restore.sh"


@pytest.fixture
def fake_docker(tmp_path):
    """A `docker` on PATH that reports compose project 'ledgerline' and logs every other call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    shim = bin_dir / "docker"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            if [ "$1 $2" = "compose config" ]; then
              echo "name: ${{FAKE_PROJECT:-ledgerline}}"
              echo "services: {{}}"
              exit 0
            fi
            echo "$*" >> {log}
            cat > /dev/null
            """
        )
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    dump = tmp_path / "x.dump"
    dump.write_bytes(b"dump")
    return bin_dir, log, dump


def run_restore(fake_docker, *args, stdin="", **env):
    bin_dir, _, _ = fake_docker
    full_env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", **env}
    return subprocess.run(
        ["sh", str(RESTORE_SH), *args], input=stdin, capture_output=True, text=True, env=full_env
    )


def docker_calls(fake_docker) -> list[str]:
    _, log, _ = fake_docker
    return log.read_text().splitlines() if log.exists() else []


def test_restore_refuses_without_a_project(fake_docker):
    _, _, dump = fake_docker
    result = run_restore(fake_docker, "--yes", str(dump))
    assert result.returncode == 1
    assert "no target project" in result.stderr
    assert docker_calls(fake_docker) == []


def test_restore_refuses_a_project_that_is_not_the_directorys(fake_docker):
    _, _, dump = fake_docker
    result = run_restore(fake_docker, "--project", "lldrill", "--yes", str(dump))
    assert result.returncode == 1
    assert "'lldrill'" in result.stderr and "'ledgerline'" in result.stderr
    assert docker_calls(fake_docker) == []


def test_restore_refuses_the_environment_project_when_it_does_not_match(fake_docker):
    _, _, dump = fake_docker
    result = run_restore(
        fake_docker,
        "--yes",
        str(dump),
        FAKE_PROJECT="other",
        LEDGERLINE_RESTORE_PROJECT="ledgerline",
    )
    assert result.returncode == 1
    assert docker_calls(fake_docker) == []


def test_restore_aborts_when_the_typed_name_is_wrong(fake_docker):
    _, _, dump = fake_docker
    result = run_restore(fake_docker, "--project", "ledgerline", str(dump), stdin="restore\n")
    assert result.returncode == 1
    assert "aborted" in result.stdout
    assert docker_calls(fake_docker) == []


def test_restore_runs_against_the_named_project_after_confirmation(fake_docker):
    _, _, dump = fake_docker
    result = run_restore(fake_docker, "--project", "ledgerline", str(dump), stdin="ledgerline\n")
    assert result.returncode == 0, result.stderr
    assert docker_calls(fake_docker) == [
        "compose -p ledgerline stop app",
        "compose -p ledgerline exec -T db pg_restore -U ledgerline -d ledgerline"
        " --clean --if-exists --no-owner",
        "compose -p ledgerline start app",
    ]


def test_restore_yes_skips_only_the_confirmation(fake_docker):
    _, _, dump = fake_docker
    result = run_restore(fake_docker, "--yes", str(dump), LEDGERLINE_RESTORE_PROJECT="ledgerline")
    assert result.returncode == 0, result.stderr
    assert len(docker_calls(fake_docker)) == 3
