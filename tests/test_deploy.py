"""deploy/install.sh and deploy/proxmox/create-lxc.sh: their messages, the release key they
trust, and create-lxc.sh run against stand-ins for Proxmox's commands."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "deploy" / "install.sh"
CREATE_LXC = ROOT / "deploy" / "proxmox" / "create-lxc.sh"
SCRIPTS = [INSTALL, CREATE_LXC]
_SIGNER = re.compile(r'[^\s]+ namespaces="git" ssh-(ed25519|rsa) [A-Za-z0-9+/]+=*')
_MESSAGE = re.compile(r'^    (MSG_[A-Z_]+)="((?:[^"\\]|\\.)*)"', re.M | re.S)

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_release_signers_lists_real_keys():
    match = re.search(r"^RELEASE_SIGNERS='([^']*)'$", INSTALL.read_text(), re.M)
    assert match, "RELEASE_SIGNERS='...' must stay a single-quoted line"
    keys = [line for line in match.group(1).splitlines() if line.strip()]
    assert keys, "list at least one release key"
    for key in keys:
        assert _SIGNER.fullmatch(key.strip()), f"not an allowed-signers line: {key[:40]}"


def _messages(script: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(pt-BR, en-US) messages of the script's case "$LOCALE" block."""
    text = script.read_text()
    block = text[text.index('case "$LOCALE" in\n  pt-BR)'):]
    portuguese, rest = block.split("\n  *)\n", 1)
    english = rest.split("\n    ;;\n", 1)[0]
    return dict(_MESSAGE.findall(portuguese)), dict(_MESSAGE.findall(english))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_messages_exist_in_both_languages(script):
    portuguese, english = _messages(script)
    assert english, "no messages found"
    assert portuguese.keys() == english.keys()
    for name, text in english.items():
        assert portuguese[name].count("%s") == text.count("%s"), name


def test_both_scripts_check_printers_the_same_way():
    def pattern(script):
        body = script.read_text().split("valid_printer() {", 1)[1]
        return body.split("\n", 2)[1]
    assert pattern(INSTALL) == pattern(CREATE_LXC)


# -- create-lxc.sh against stand-ins ----------------------------------------------


STUBS = {
    "id": '[ "${1:-}" = "-u" ] && echo 0 && exit 0; exec /usr/bin/id "$@"',
    "pvesh": "echo 123",
    "pveam": """
case "$1" in
  available) printf 'system  debian-12-standard_12.7-1_amd64.tar.zst\\n'
             printf 'system  debian-13-standard_13.1-2_amd64.tar.zst\\n' ;;
  list) printf 'NAME  SIZE\\n' ;;
  download) echo "download $*" >> "$LOG/calls" ;;
esac""",
    "pct": """
echo "$*" >> "$LOG/calls"
case "$1" in
  status) exit 2 ;;
  create) case "$*" in *debian-13*) [ -z "${REFUSE_13:-}" ] || exit 1 ;; esac ;;
  push) cp "$3" "$LOG/pushed-installer" ;;
  exec) case "$*" in *dont-dry-install.sh*) exit "${INSTALL_STATUS:-0}" ;; esac ;;
esac""",
    "curl": 'echo "curl must not run from a checkout" >&2; exit 99',
}


def _run(tmp_path, *args, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body.strip() + "\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    environment = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "LOG": str(tmp_path), "LANG": "C"}
    environment.update(env)
    result = subprocess.run(["bash", str(CREATE_LXC), *args], env=environment, text=True,
                            stdin=subprocess.DEVNULL, capture_output=True)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    return result, calls


@needs_bash
def test_creates_an_unprivileged_container_and_installs_in_it(tmp_path):
    result, calls = _run(tmp_path, "--printer", "ipp://192.168.1.50/ipp/print",
                         "--schedule", "0 8 * * mon", "--locale", "pt-BR",
                         "--ip", "192.168.1.60/24", "--gateway", "192.168.1.1")
    assert result.returncode == 0, result.stderr
    (create,) = [c for c in calls if c.startswith("create ")]
    assert create.startswith("create 123 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst ")
    for flag in ("--unprivileged 1", "--features nesting=1", "--onboot 1", "--timezone host",
                 "--memory 256", "--rootfs local-lvm:2",
                 "--net0 name=eth0,bridge=vmbr0,ip=192.168.1.60/24,gw=192.168.1.1"):
        assert flag in create
    assert (tmp_path / "pushed-installer").read_bytes() == INSTALL.read_bytes()
    (install,) = [c for c in calls if c.startswith("exec ") and "dont-dry-install.sh" in c]
    assert install.endswith("bash /root/dont-dry-install.sh --printer ipp://192.168.1.50/ipp/print"
                            " --locale pt-BR --schedule 0 8 * * mon")
    assert "Pronto! Container 123 (dont-dry)." in result.stdout


@needs_bash
def test_falls_back_to_debian_12_on_older_proxmox(tmp_path):
    result, calls = _run(tmp_path, "--printer", "ipp://192.168.1.50/ipp/print", REFUSE_13="1")
    assert result.returncode == 0, result.stderr
    creates = [c for c in calls if c.startswith("create ")]
    assert "debian-13" in creates[0] and "debian-12" in creates[1]
    assert "did not accept debian-13-standard_13.1-2_amd64.tar.zst" in result.stdout


@needs_bash
def test_a_failed_install_says_how_to_retry(tmp_path):
    result, _ = _run(tmp_path, "--printer", "ipp://192.168.1.50/ipp/print",
                     "--schedule", "0 8 * * mon", INSTALL_STATUS="1")
    assert result.returncode == 1
    assert ("pct exec 123 -- env LC_ALL=C.UTF-8 bash /root/dont-dry-install.sh"
            " --printer ipp://192.168.1.50/ipp/print --schedule 0\\ 8\\ \\*\\ \\*\\ mon"
            in result.stderr)


@needs_bash
@pytest.mark.parametrize("args,message", [
    ([], "--printer ipp://192.168.1.50/ipp/print"),        # nobody at the keyboard to ask
    (["--printer", "192.168.1.50"], "Not a printer address: 192.168.1.50"),
    (["--printer", "ipp://p/ipp/print", "--locale", "es-ES"], "Unknown language: es-ES"),
    (["--printer", "ipp://p/ipp/print", "--ip", "192.168.1.60/24"], "--gateway"),
    (["--printer", "ipp://p/ipp/print", "--bogus"], "Unknown option: --bogus"),
])
def test_refuses_before_creating_anything(tmp_path, args, message):
    result, calls = _run(tmp_path, *args)
    assert result.returncode == 1
    assert message in result.stderr
    assert not [c for c in calls if c.startswith("create ")]


@needs_bash
@pytest.mark.parametrize("args,message", [
    (["--printer", "ipp://p/ipp/print\nEVIL=1"], "Not a printer address"),
    (["--schedule", "0 9 * * 1,4\nEVIL=1"], "Not a schedule"),
    (["--locale", "xx"], "Unknown language: xx"),
    (["--whatever"], "Unknown option: --whatever"),
])
def test_installer_checks_options_before_touching_the_machine(args, message):
    result = subprocess.run(["bash", str(INSTALL), *args], text=True, capture_output=True,
                            stdin=subprocess.DEVNULL, env={**os.environ, "LANG": "C"})
    assert result.returncode == 1
    assert message in result.stderr
