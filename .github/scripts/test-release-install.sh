#!/usr/bin/env bash
# CI check of deploy/install.sh's release path, on a machine with systemd, against the fake
# printer: a signed release installs; a newer unsigned tag is refused; an update to a newer
# signed release runs that release's installer and leaves the settings alone (also LOCALE, which
# it is never given); an older "latest" is refused as a downgrade; --ref installs it on purpose.
# The releases are signed with a throwaway key that only this copy of the installer trusts.
#
#   test-release-install.sh PRINTER_URI      (as root, from a checkout)
set -euo pipefail

PRINTER="$1"
CHECKOUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
KEY="$WORK/release-key"

fail() { echo "FAIL: $*" >&2; exit 1; }
installed() { git -C /opt/dont-dry describe --tags --exact-match HEAD 2>/dev/null || echo none; }
in_repo() {
  git -C "$REPO" -c user.name=ci -c user.email=ci@example.invalid \
    -c gpg.format=ssh -c user.signingkey="$KEY.pub" "$@"
}

ssh-keygen -q -t ed25519 -N "" -C ci-release -f "$KEY"
mkdir "$REPO"
git -C "$CHECKOUT" archive HEAD | tar -x -C "$REPO"
git -C "$REPO" init -q -b master
sed -i "s|^RELEASE_SIGNERS=.*|RELEASE_SIGNERS='dont-dry-release namespaces=\"git\" $(cut -d' ' -f1,2 "$KEY.pub")'|" \
  "$REPO/deploy/install.sh"
in_repo add -A
in_repo commit -q -m "release 1.0.0"
in_repo tag -s v1.0.0 -m "dont-dry 1.0.0"

echo "== a fresh install takes the latest signed release"
DONT_DRY_REPO="$REPO" bash "$REPO/deploy/install.sh" --printer "$PRINTER" --locale en-US
[ "$(installed)" = v1.0.0 ] || fail "expected v1.0.0, got $(installed)"
systemctl is-active --quiet dont-dry || fail "the service is not running"

echo "== a newer unsigned tag is refused"
echo "# unsigned" >> "$REPO/README.md"
in_repo commit -q -am "unsigned"
in_repo tag -a v1.1.0 -m "dont-dry 1.1.0"
if bash /opt/dont-dry/deploy/install.sh; then fail "the unsigned v1.1.0 was installed"; fi
[ "$(installed)" = v1.0.0 ] || fail "expected v1.0.0 after the refusal, got $(installed)"
git -C /opt/dont-dry rev-parse -q --verify refs/tags/v1.1.0 >/dev/null &&
  fail "the refused tag was kept"
systemctl is-active --quiet dont-dry || fail "the service stopped"
in_repo tag -d v1.1.0 >/dev/null

echo "== an update runs the new release's installer and leaves the settings alone"
sed -i '/^LOCALE=/d' /etc/dont-dry.env  # like a settings file written by hand, without LOCALE
cp /etc/dont-dry.env "$WORK/settings-before"
echo "# a changed installer, so the update re-runs it" >> "$REPO/deploy/install.sh"
in_repo commit -q -am "release 1.0.1"
in_repo tag -s v1.0.1 -m "dont-dry 1.0.1"
LANG=pt_BR.UTF-8 bash /opt/dont-dry/deploy/install.sh | tee "$WORK/update.log"
[ "$(installed)" = v1.0.1 ] || fail "expected v1.0.1, got $(installed)"
cmp "$WORK/settings-before" /etc/dont-dry.env || fail "the update changed the settings"
grep -q "Mantive as configurações" "$WORK/update.log" || fail "the update did not speak pt-BR"

echo "== an older latest release is refused as a downgrade"
in_repo tag -d v1.0.1 >/dev/null
if bash /opt/dont-dry/deploy/install.sh; then fail "downgraded to v1.0.0"; fi
[ "$(installed)" = v1.0.1 ] || fail "expected v1.0.1 after the refusal, got $(installed)"

echo "== --ref installs it on purpose"
bash /opt/dont-dry/deploy/install.sh --ref v1.0.0
[ "$(installed)" = v1.0.0 ] || fail "expected v1.0.0 with --ref, got $(installed)"
systemctl is-active --quiet dont-dry || fail "the service is not running"

echo "all release-path checks passed"
