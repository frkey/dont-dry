#!/usr/bin/env bash
# Installs or updates dont-dry as a systemd service on Debian 12+ or Ubuntu 22.04+.
#
#   install.sh [options]
#     --printer URI          the printer, e.g. ipp://192.168.1.50/ipp/print
#     --schedule CRON        when to print (default: "0 9 * * 1,4", Mondays and Thursdays 09:00)
#     --timezone Area/City   time zone of the schedule (default: this machine's)
#     --locale en-US|pt-BR   language of the page, the notifications and these messages
#     --test-print           print a page at the end
#     --ref BRANCH_OR_TAG    version to install (default: the latest release)
#
# Run it as root. Running it again is how you update: it keeps the version (--ref) and the
# settings, and changes only the settings given as options.
#
# Layout:
#   /opt/dont-dry             code and virtualenv
#   /etc/dont-dry.env         settings (root only; systemd reads it for the service)
#   /usr/local/bin/dont-dry   runs dont-dry with those settings, e.g. "dont-dry info"
set -euo pipefail

ARGS=("$@")
# Fingerprint of this script, to notice when the download brings a newer installer.
# Only read regular files: under "bash <(curl ...)" $0 is a pipe that bash is still reading.
SELF_HASH=""
[ ! -f "$0" ] || SELF_HASH="$(sha256sum < "$0")"

REPO="${DONT_DRY_REPO:-}"
REF="${DONT_DRY_REF:-}"
GIVEN_LOCALE=""                  # --locale: also written to the settings
PRINTER="${DONT_DRY_PRINTER:-}"  # set when an earlier run of this script asked for it
SCHEDULE=""
TIMEZONE=""
TEST_PRINT=0
BAD_OPTION=""

APP_DIR=/opt/dont-dry
# Keys that sign dont-dry releases, in git's allowed-signers format. A release picked by
# "latest" is installed only when its tag is signed by one of them. The installer that checks a
# release is the one already installed, so a new key must ship in a release signed by a key
# listed here.
RELEASE_SIGNERS='dont-dry-release namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFIMxhSZX2sTucZesFd37ee+vm6teXJzhX5oj8RcAPN6'
CONF=/etc/dont-dry.env
UNIT=/etc/systemd/system/dont-dry.service
WRAPPER=/usr/local/bin/dont-dry

while [ $# -gt 0 ]; do
  case "$1" in
    --printer) PRINTER="${2:-}"; shift 2 || shift ;;
    --schedule) SCHEDULE="${2:-}"; shift 2 || shift ;;
    --timezone) TIMEZONE="${2:-}"; shift 2 || shift ;;
    --locale) GIVEN_LOCALE="${2:-}"; shift 2 || shift ;;
    --test-print) TEST_PRINT=1; shift ;;
    --ref) REF="${2:-}"; shift 2 || shift ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) BAD_OPTION="$1"; shift ;;
  esac
done

# normalize_locale NAME: en-US or pt-BR in any case, with - or _; nothing for anything else
normalize_locale() {
  case "$1" in
    [Ee][Nn][-_][Uu][Ss]) echo en-US ;;
    [Pp][Tt][-_][Bb][Rr]) echo pt-BR ;;
  esac
}

# The messages' language: --locale; else what a run of this script that re-ran it chose
# (DONT_DRY_LOCALE, which only picks the messages' language and is never written to the
# settings); else, for an update, the settings' own (the last LOCALE= line, as systemd reads it);
# else the system locale.
if [ -n "$GIVEN_LOCALE" ]; then
  LOCALE="$(normalize_locale "$GIVEN_LOCALE")"
else
  LOCALE="$(normalize_locale "${DONT_DRY_LOCALE:-}")"
  if [ -z "$LOCALE" ] && [ -f "$CONF" ] && [ ! -L "$CONF" ]; then
    LOCALE="$(normalize_locale "$(sed -n 's/^LOCALE=\([A-Za-z_-]*\).*/\1/p' "$CONF" | tail -n 1)")"
  fi
  if [ -z "$LOCALE" ]; then
    case "${LC_ALL:-${LANG:-}}" in pt*) LOCALE="pt-BR" ;; esac
  fi
fi

# The messages' language is settled: from here on, every command runs in a locale that always
# exists. A shell reached through "pct exec" carries the Proxmox host's locale (en_US.UTF-8, say),
# which a fresh container does not have, and apt, perl and locale warn about it on every call.
export LC_ALL=C.UTF-8 LANG=C.UTF-8

case "$LOCALE" in
  pt-BR)
    MSG_NEED_ROOT="Rode este script como root."
    MSG_NEED_APT="Este instalador funciona só no Debian e no Ubuntu (apt)."
    MSG_NEED_SYSTEMD="O systemd não está rodando; o dont-dry precisa dele para rodar como serviço."
    MSG_BAD_OPTION="Opção desconhecida: %s (veja --help)."
    MSG_BAD_LOCALE="Idioma desconhecido: %s (use en-US ou pt-BR)."
    MSG_BAD_PRINTER="Endereço de impressora inválido: %s (exemplo: ipp://192.168.1.50/ipp/print)."
    MSG_BAD_SCHEDULE="Agendamento inválido: %s (exemplo: \"0 9 * * 1,4\", segundas e quintas às 09:00)."
    MSG_BAD_TIMEZONE="Fuso horário desconhecido: %s (exemplo: America/Sao_Paulo)."
    MSG_ASK_PRINTER="Endereço da impressora (exemplo: ipp://192.168.1.50/ipp/print): "
    MSG_NEED_PRINTER="Informe o endereço da impressora: --printer ipp://192.168.1.50/ipp/print"
    MSG_PACKAGES="Instalando pacotes do sistema..."
    MSG_OLD_PYTHON="O dont-dry precisa do Python 3.10 ou mais novo (Debian 12+ ou Ubuntu 22.04+)."
    MSG_DOWNLOAD="Baixando o dont-dry (%s)..."
    MSG_NO_TAGS="Não consegui ler as versões de %s. Confira a rede ou use --ref."
    MSG_NO_RELEASE="Nenhuma versão encontrada em %s, mas esta instalação já está numa versão; nada foi alterado. Para mudar para um branch, use --ref."
    MSG_UNSIGNED="A versão %s não está assinada com a chave de releases do dont-dry; nada foi instalado. Para instalar um branch ou uma tag sem essa conferência, use --ref."
    MSG_DOWNGRADE="A versão mais nova em %s é %s, mais antiga que a instalada (%s); nada foi alterado. Para instalá-la mesmo assim, use --ref %s."
    MSG_NOT_GIT="%s existe mas não é um clone do git; mova a pasta e rode de novo."
    MSG_PIP="Instalando o pacote Python..."
    MSG_CONFIG_NEW="Criei %s."
    MSG_CONFIG_KEPT="Mantive as configurações de %s."
    MSG_CONFIG_SET="Atualizei %s em %s."
    MSG_CONFIG_LINK="%s é um link simbólico; deixei como está (as opções de configuração foram ignoradas)."
    MSG_UTC="O agendamento usa UTC. Para usar o seu fuso, rode de novo com --timezone, por exemplo --timezone America/Sao_Paulo."
    MSG_SERVICE="Instalando o serviço do systemd..."
    MSG_FAILED="O serviço não subiu. Últimas linhas do log:"
    MSG_FAILED_HINT="Corrija %s e rode: systemctl restart dont-dry"
    MSG_CHECK="Conferindo a impressora..."
    MSG_UNREACHABLE="O dont-dry não conseguiu falar com a impressora agora; ele tenta de novo a cada impressão. Para conferir depois: dont-dry info"
    MSG_TEST_PRINT="Imprimindo uma página de teste..."
    MSG_TEST_FAILED="A página de teste não saiu; veja a mensagem acima."
    MSG_WRAPPER_FAILED="O comando dont-dry não conseguiu iniciar (o systemd parou com o código %s). Veja: journalctl -n 20 -u 'run-*'"
    MSG_RUNNING="
O dont-dry está rodando."
    MSG_DAY_TO_DAY="  Configurações:   %s (depois, systemctl restart dont-dry)
  Impressora:      dont-dry info    ·   imprimir agora: dont-dry print
  Log:             journalctl -u dont-dry -f
  Atualizar:       bash /opt/dont-dry/deploy/install.sh"
    ;;
  *)
    MSG_NEED_ROOT="Run this script as root."
    MSG_NEED_APT="This installer supports Debian and Ubuntu (apt) only."
    MSG_NEED_SYSTEMD="systemd is not running; dont-dry needs it to run as a service."
    MSG_BAD_OPTION="Unknown option: %s (see --help)."
    MSG_BAD_LOCALE="Unknown language: %s (use en-US or pt-BR)."
    MSG_BAD_PRINTER="Not a printer address: %s (example: ipp://192.168.1.50/ipp/print)."
    MSG_BAD_SCHEDULE="Not a schedule: %s (example: \"0 9 * * 1,4\", Mondays and Thursdays at 09:00)."
    MSG_BAD_TIMEZONE="Unknown time zone: %s (example: America/New_York)."
    MSG_ASK_PRINTER="Printer address (example: ipp://192.168.1.50/ipp/print): "
    MSG_NEED_PRINTER="Give the printer's address: --printer ipp://192.168.1.50/ipp/print"
    MSG_PACKAGES="Installing system packages..."
    MSG_OLD_PYTHON="dont-dry needs Python 3.10 or newer (Debian 12+ or Ubuntu 22.04+)."
    MSG_DOWNLOAD="Downloading dont-dry (%s)..."
    MSG_NO_TAGS="Could not list the versions of %s. Check the network or use --ref."
    MSG_NO_RELEASE="No release found in %s, but this install is already on one; nothing was changed. To switch to a branch, use --ref."
    MSG_UNSIGNED="Release %s is not signed with the dont-dry release key; nothing was installed. To install a branch or tag without this check, use --ref."
    MSG_DOWNGRADE="The newest release in %s is %s, older than the installed %s; nothing was changed. To install it anyway, use --ref %s."
    MSG_NOT_GIT="%s exists but is not a git checkout; move it away and run again."
    MSG_PIP="Installing the Python package..."
    MSG_CONFIG_NEW="Created %s."
    MSG_CONFIG_KEPT="Kept the settings in %s."
    MSG_CONFIG_SET="Set %s in %s."
    MSG_CONFIG_LINK="%s is a symbolic link; left it as it is (the setting options were ignored)."
    MSG_UTC="The schedule uses UTC. To use your own time zone, run again with --timezone, e.g. --timezone America/New_York."
    MSG_SERVICE="Installing the systemd service..."
    MSG_FAILED="The service did not start. Its last log lines:"
    MSG_FAILED_HINT="Fix %s and run: systemctl restart dont-dry"
    MSG_CHECK="Checking the printer..."
    MSG_UNREACHABLE="dont-dry cannot reach the printer right now; it tries again at every print. To check later: dont-dry info"
    MSG_TEST_PRINT="Printing a test page..."
    MSG_TEST_FAILED="The test page did not print; see the message above."
    MSG_WRAPPER_FAILED="The dont-dry command could not start (systemd stopped it with code %s). See: journalctl -n 20 -u 'run-*'"
    MSG_RUNNING="
dont-dry is running."
    MSG_DAY_TO_DAY="  Settings:        %s (then systemctl restart dont-dry)
  Printer:         dont-dry info    ·   print now: dont-dry print
  Log:             journalctl -u dont-dry -f
  Update:          bash /opt/dont-dry/deploy/install.sh"
    ;;
esac

# say FORMAT [ARGS...]: print a message; the formats above are trusted
say() {
  local format="$1"
  shift
  # shellcheck disable=SC2059
  printf -- "$format\n" "$@"
}

die() {
  say "$@" >&2
  exit 1
}

# The settings file is read by systemd, not by a shell, but each value must still fit on its
# line: these checks keep newlines, quotes and backslashes out. dont-dry checks the values
# themselves when it starts.
valid_printer() {
  local pattern='^(ipps?|https?)://[][A-Za-z0-9._~%@:-]+(/[A-Za-z0-9._~%/+-]*)?$'
  [[ "$1" =~ $pattern ]]
}
valid_schedule() {
  local pattern='^(@[a-z]+|[0-9A-Za-z*/,-]+( +[0-9A-Za-z*/,-]+){4})$'
  [[ "$1" =~ $pattern ]]
}
# valid_timezone NAME: a zone from the system's tz database, e.g. America/Sao_Paulo
valid_timezone() {
  case "$1" in ''|/*|*..*|*[!A-Za-z0-9/_+-]*) return 1 ;; esac
  [ -f "/usr/share/zoneinfo/$1" ]
}

[ -z "$BAD_OPTION" ] || die "$MSG_BAD_OPTION" "$BAD_OPTION"
[ -z "$GIVEN_LOCALE" ] || [ -n "$LOCALE" ] || die "$MSG_BAD_LOCALE" "$GIVEN_LOCALE"
[ -z "$PRINTER" ] || valid_printer "$PRINTER" || die "$MSG_BAD_PRINTER" "$PRINTER"
[ -z "$SCHEDULE" ] || valid_schedule "$SCHEDULE" || die "$MSG_BAD_SCHEDULE" "$SCHEDULE"
[ "$(id -u)" -eq 0 ] || die "$MSG_NEED_ROOT"
command -v apt-get >/dev/null || die "$MSG_NEED_APT"
[ -d /run/systemd/system ] || die "$MSG_NEED_SYSTEMD"

# A first install needs the printer: ask for it when someone is at the keyboard
if [ -z "$PRINTER" ] && [ ! -e "$CONF" ]; then
  [ -t 0 ] || die "$MSG_NEED_PRINTER"
  read -r -p "$MSG_ASK_PRINTER" PRINTER
  valid_printer "$PRINTER" || die "$MSG_BAD_PRINTER" "$PRINTER"
fi

say "$MSG_PACKAGES"
export DEBIAN_FRONTEND=noninteractive DEBCONF_NOWARNINGS=yes
apt-get update -qq
# printer_host URI: the host part, e.g. 192.168.1.50, printer.local or [fe80::1]
printer_host() {
  printf '%s\n' "$1" | sed -E -n 's#^[A-Za-z]+://([^/@]*@)?(\[[^]]*\]|[^/:]+).*#\2#p'
}
# The printer the service will use: --printer, else the one in the settings (the last line)
SERVICE_PRINTER="$PRINTER"
if [ -z "$SERVICE_PRINTER" ] && [ -f "$CONF" ] && [ ! -L "$CONF" ]; then
  SERVICE_PRINTER="$(sed -n 's/^PRINTER_URI=//p' "$CONF" | tail -n 1)"
fi
# avahi and nss-mdns resolve a printer's .local name (which keeps working when its IP address
# changes), but avahi also announces this machine on the network: only when the printer needs it
MDNS_PACKAGES=()
case "$(printer_host "$SERVICE_PRINTER")" in *.local|*.local.) MDNS_PACKAGES=(avahi-daemon libnss-mdns) ;; esac
# DejaVu draws the page's text. git checks release signatures with ssh-keygen.
apt-get install -y -qq --no-install-recommends python3 python3-venv git ca-certificates tzdata \
  fonts-dejavu-core openssh-client "${MDNS_PACKAGES[@]}" >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "$MSG_OLD_PYTHON"
[ -z "$TIMEZONE" ] || valid_timezone "$TIMEZONE" || die "$MSG_BAD_TIMEZONE" "$TIMEZONE"

# Without --ref or DONT_DRY_REPO, keep what the current install came from
if [ -d "$APP_DIR/.git" ]; then
  [ -n "$REPO" ] || REPO="$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || true)"
  [ -n "$REF" ] || REF="$(git -C "$APP_DIR" config --get dont-dry.version || true)"
fi
REPO="${REPO:-https://github.com/frkey/dont-dry.git}"
REF="${REF:-latest}"

# "latest" is the newest release tag (vX.Y.Z); until there is one, it is master. The re-run below
# gets "latest" too, so the version stays symbolic and every update picks the newest release.
TARGET="$REF"
if [ "$REF" = "latest" ]; then
  TAGS="$(git ls-remote --tags --refs "$REPO" 'v*')" || die "$MSG_NO_TAGS" "$REPO"
  TARGET="$(printf '%s\n' "$TAGS" |
    sed -n 's|^[0-9a-f]*[[:space:]]*refs/tags/\(v[0-9]*\.[0-9]*\.[0-9]*\)$|\1|p' |
    sort -V | tail -n 1)"
  CURRENT="$(git -C "$APP_DIR" describe --tags --exact-match HEAD 2>/dev/null || true)"
  case "$CURRENT" in v[0-9]*.[0-9]*.[0-9]*) ;; *) CURRENT="" ;; esac
  if [ -z "$TARGET" ]; then
    # No release yet: master. An install already on a release never falls back to master,
    # which is not signed: its releases disappearing is a reason to stop, not to follow.
    [ -z "$CURRENT" ] || die "$MSG_NO_RELEASE" "$REPO"
    TARGET="master"
  elif [ -n "$CURRENT" ] && [ "$TARGET" != "$CURRENT" ] &&
       [ "$(printf '%s\n%s\n' "$TARGET" "$CURRENT" | sort -V | tail -n 1)" = "$CURRENT" ]; then
    # Newer releases disappeared: going back to an older one, signed but maybe vulnerable, is
    # something to ask for with --ref, never a side effect of an update
    die "$MSG_DOWNGRADE" "$REPO" "$TARGET" "$CURRENT" "$TARGET"
  fi
fi

# verify_release TAG: the tag is signed by one of RELEASE_SIGNERS and names itself TAG (so an
# older signed release cannot come back under a newer name)
verify_release() {
  local signers status=0
  [ "$(git -C "$APP_DIR" cat-file tag "refs/tags/$1" 2>/dev/null | sed -n 's/^tag //p')" = "$1" ] ||
    return 1
  signers="$(mktemp)"
  printf '%s\n' "$RELEASE_SIGNERS" > "$signers"
  git -C "$APP_DIR" -c gpg.ssh.allowedSignersFile="$signers" verify-tag "refs/tags/$1" \
    >/dev/null 2>&1 || status=1
  rm -f "$signers"
  return "$status"
}

# A release picked by "latest" is fetched as a tag and verified before anything from it is
# checked out or run; master (no release yet) and a --ref are taken as they are
SIGNED=""
[ "$REF" != "latest" ] || [ "$TARGET" = "master" ] || SIGNED="$TARGET"
CHECKOUT="FETCH_HEAD"
[ -z "$SIGNED" ] || CHECKOUT="refs/tags/$SIGNED"
FRESH=0
say "$MSG_DOWNLOAD" "$TARGET"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" remote set-url origin "$REPO"
  if [ -n "$SIGNED" ]; then
    git -C "$APP_DIR" fetch --quiet --depth 1 --no-tags origin "+refs/tags/$SIGNED:refs/tags/$SIGNED"
  else
    git -C "$APP_DIR" fetch --quiet --depth 1 origin "$TARGET"
  fi
elif [ -e "$APP_DIR" ]; then
  die "$MSG_NOT_GIT" "$APP_DIR"
else
  git clone --quiet --depth 1 --no-checkout --branch "$TARGET" "$REPO" "$APP_DIR"
  FRESH=1
  [ -n "$SIGNED" ] || CHECKOUT="HEAD"
fi
if [ -n "$SIGNED" ] && ! verify_release "$SIGNED"; then
  if [ "$FRESH" -eq 1 ]; then
    rm -rf "$APP_DIR"
  else  # nothing was checked out; drop the refused tag so it never looks installed
    git -C "$APP_DIR" tag -d "$SIGNED" >/dev/null 2>&1 || true
  fi
  die "$MSG_UNSIGNED" "$SIGNED"
fi
git -C "$APP_DIR" -c advice.detachedHead=false checkout --quiet --force "$CHECKOUT"
git -C "$APP_DIR" config dont-dry.version "$REF"

# Finish with the installer that was just downloaded, so an update also updates these steps
if [ -z "${DONT_DRY_REEXEC:-}" ] && [ "$SELF_HASH" != "$(sha256sum < "$APP_DIR/deploy/install.sh")" ]; then
  # the language too: the locale it was found in is gone (LC_ALL=C.UTF-8 above); and the
  # printer, in case it was typed at the prompt
  DONT_DRY_REEXEC=1 DONT_DRY_REPO="$REPO" DONT_DRY_REF="$REF" DONT_DRY_LOCALE="$LOCALE" \
    DONT_DRY_PRINTER="$PRINTER" exec bash "$APP_DIR/deploy/install.sh" "${ARGS[@]}"
fi

say "$MSG_PIP"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
# The dependencies come pinned and hash-checked from deploy/requirements.txt, setuptools (the
# build backend) included; dont-dry itself is then built with them, so nothing unpinned is fetched
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check --require-hashes \
  -r "$APP_DIR/deploy/requirements.txt"
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check --no-deps \
  --no-build-isolation --upgrade "$APP_DIR"

# set_setting NAME VALUE: sets NAME the way systemd will read it. It replaces the last NAME= line
# (systemd uses the last one); with none, the first commented-out "# NAME=" example; else it adds
# a line. Comments are left alone. The file is replaced, never written through, and stays
# root-only.
set_setting() {
  local tmp
  tmp="$(mktemp "$CONF.XXXXXX")"
  awk -v name="$1" -v value="$2" '
    NR == FNR {  # first pass: find the line to replace
      if ($0 ~ "^" name "=") last = FNR
      else if (!example && $0 ~ "^# *" name "=") example = FNR
      next
    }
    FNR == (last ? last : example) { print name "=" value; done = 1; next }
    { print }
    END { if (!done) print name "=" value }' "$CONF" "$CONF" > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$CONF"
}

UTC=0
if [ -L "$CONF" ]; then
  say "$MSG_CONFIG_LINK" "$CONF"
elif [ -f "$CONF" ]; then
  say "$MSG_CONFIG_KEPT" "$CONF"
  chmod 600 "$CONF"
  [ -z "$PRINTER" ] || { set_setting PRINTER_URI "$PRINTER"; say "$MSG_CONFIG_SET" PRINTER_URI "$CONF"; }
  [ -z "$SCHEDULE" ] || { set_setting SCHEDULE "$SCHEDULE"; say "$MSG_CONFIG_SET" SCHEDULE "$CONF"; }
  [ -z "$TIMEZONE" ] || { set_setting TZ "$TIMEZONE"; say "$MSG_CONFIG_SET" TZ "$CONF"; }
  [ -z "$GIVEN_LOCALE" ] || { set_setting LOCALE "$LOCALE"; say "$MSG_CONFIG_SET" LOCALE "$CONF"; }
else
  # Without --timezone, the machine's zone (a Proxmox container gets the host's)
  if [ -z "$TIMEZONE" ]; then  # e.g. /usr/share/zoneinfo/America/Sao_Paulo
    TIMEZONE="$(readlink /etc/localtime 2>/dev/null | sed -n 's|^.*/zoneinfo/\(posix/\)\{0,1\}||p')"
    valid_timezone "$TIMEZONE" || TIMEZONE="UTC"
  fi
  case "$TIMEZONE" in UTC|Etc/UTC|Etc/Universal) UTC=1 ;; esac
  install -m 600 "$APP_DIR/.env.example" "$CONF"
  set_setting PRINTER_URI "$PRINTER"
  [ -z "$SCHEDULE" ] || set_setting SCHEDULE "$SCHEDULE"
  set_setting TZ "$TIMEZONE"
  set_setting LOCALE "${LOCALE:-en-US}"
  say "$MSG_CONFIG_NEW" "$CONF"
fi

say "$MSG_SERVICE"
# Written next to it and moved into place: a link planted at $WRAPPER is replaced, never followed
WRAPPER_TMP="$(mktemp "$WRAPPER.XXXXXX")"
cat > "$WRAPPER_TMP" <<'EOF'
#!/bin/sh
# Runs dont-dry with the service's settings (installed by deploy/install.sh), e.g.
# "dont-dry info" or "dont-dry print". systemd reads /etc/dont-dry.env exactly as it does for the
# service, and the command runs as the service's throwaway user, with its state folder, so a
# page printed by hand is recorded like a scheduled one. stdin is never passed on: in an
# unprivileged container a terminal there belongs to the host, systemd cannot hand it to the
# throwaway user, and the command would die at step STDIN; dont-dry never reads it anyway.
exec systemd-run --quiet --wait --pipe --collect \
  --property=EnvironmentFile=/etc/dont-dry.env \
  --property=DynamicUser=yes --property=User=dont-dry --property=StateDirectory=dont-dry \
  --setenv=PYTHONUNBUFFERED=1 --setenv=STATE_DIR=/var/lib/dont-dry \
  /opt/dont-dry/venv/bin/dont-dry "$@" </dev/null
EOF
chmod 755 "$WRAPPER_TMP"
mv -f "$WRAPPER_TMP" "$WRAPPER"
install -m 644 "$APP_DIR/deploy/dont-dry.service" "$UNIT"
systemctl daemon-reload
systemctl enable --quiet dont-dry
[ "${#MDNS_PACKAGES[@]}" -eq 0 ] || systemctl enable --now --quiet avahi-daemon >/dev/null 2>&1 || true
systemctl restart dont-dry
sleep 3
if ! systemctl is-active --quiet dont-dry; then
  say "$MSG_FAILED" >&2
  journalctl -u dont-dry -n 10 --no-pager -o cat >&2 || true
  die "$MSG_FAILED_HINT" "$CONF"
fi

say "$MSG_CHECK"
# wrapper_failed STATUS: the transient unit never got to run dont-dry (systemd's own exit codes
# start at 200), which no message of dont-dry's would explain
wrapper_failed() {
  [ "$1" -lt 200 ] && return 1
  say "$MSG_WRAPPER_FAILED" "$1" >&2
}
status=0
"$WRAPPER" info || status=$?
[ "$status" -eq 0 ] || wrapper_failed "$status" || say "$MSG_UNREACHABLE"
if [ "$TEST_PRINT" -eq 1 ]; then
  say "$MSG_TEST_PRINT"
  status=0
  "$WRAPPER" print || status=$?
  [ "$status" -eq 0 ] || wrapper_failed "$status" || say "$MSG_TEST_FAILED"
fi

say "$MSG_RUNNING"
status=0
"$WRAPPER" next -n 3 || status=$?
[ "$status" -eq 0 ] || wrapper_failed "$status" || true
say "$MSG_DAY_TO_DAY" "$CONF"
[ "$UTC" -eq 0 ] || say "$MSG_UTC"
