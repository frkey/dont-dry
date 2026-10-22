#!/usr/bin/env bash
# Creates a Debian LXC container on a Proxmox VE host and installs dont-dry in it.
#
#   create-lxc.sh [options]
#     --printer URI            the printer, e.g. ipp://192.168.1.50/ipp/print (asked if missing)
#     --schedule CRON          when to print (default: "0 9 * * 1,4", Mondays and Thursdays 09:00)
#     --locale en-US|pt-BR     language of the page, the notifications and these messages
#     --test-print             print a page at the end
#     --ip dhcp|CIDR           e.g. 192.168.1.60/24 (default: dhcp)
#     --gateway IP             router address, required with a static --ip
#     --ctid N                 container ID (default: next free ID)
#     --hostname NAME          (default: dont-dry)
#     --bridge NAME            network bridge (default: vmbr0)
#     --storage NAME           storage for the container disk (default: local-lvm)
#     --template-storage NAME  storage for the Debian template (default: local)
#     --disk GB                (default: 2)
#     --memory MB              (default: 256)
#     --ref BRANCH_OR_TAG      dont-dry version to install (default: the latest release)
#
# Run it as root on the Proxmox host (the node's Shell in the web UI, or SSH). The container
# uses the host's time zone for the schedule.
set -euo pipefail

REPO="${DONT_DRY_REPO:-https://github.com/frkey/dont-dry.git}"
RAW_URL="${DONT_DRY_RAW_URL:-https://raw.githubusercontent.com/frkey/dont-dry}"
# Each release has install.sh attached, taken from its signed tag
RELEASES_URL="${DONT_DRY_RELEASES_URL:-https://github.com/frkey/dont-dry/releases/latest/download}"
REF="${DONT_DRY_REF:-}"  # empty: install.sh picks the latest release
LOCALE="${DONT_DRY_LOCALE:-}"
PRINTER=""
SCHEDULE=""
TEST_PRINT=0
IP="dhcp"
GATEWAY=""
CTID=""
CT_HOSTNAME="dont-dry"
BRIDGE="vmbr0"
STORAGE="local-lvm"
TEMPLATE_STORAGE="local"
DISK=2
MEMORY=256
BAD_OPTION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --printer) PRINTER="${2:-}"; shift 2 || shift ;;
    --schedule) SCHEDULE="${2:-}"; shift 2 || shift ;;
    --locale) LOCALE="${2:-}"; shift 2 || shift ;;
    --test-print) TEST_PRINT=1; shift ;;
    --ip) IP="${2:-}"; shift 2 || shift ;;
    --gateway) GATEWAY="${2:-}"; shift 2 || shift ;;
    --ctid) CTID="${2:-}"; shift 2 || shift ;;
    --hostname) CT_HOSTNAME="${2:-}"; shift 2 || shift ;;
    --bridge) BRIDGE="${2:-}"; shift 2 || shift ;;
    --storage) STORAGE="${2:-}"; shift 2 || shift ;;
    --template-storage) TEMPLATE_STORAGE="${2:-}"; shift 2 || shift ;;
    --disk) DISK="${2:-}"; shift 2 || shift ;;
    --memory) MEMORY="${2:-}"; shift 2 || shift ;;
    --ref) REF="${2:-}"; shift 2 || shift ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) BAD_OPTION="$1"; shift ;;
  esac
done

# Without --locale, follow the system locale
GIVEN_LOCALE="$LOCALE"
case "$LOCALE" in
  [Ee][Nn][-_][Uu][Ss]) LOCALE="en-US" ;;
  [Pp][Tt][-_][Bb][Rr]) LOCALE="pt-BR" ;;
  '') case "${LC_ALL:-${LANG:-}}" in pt*) LOCALE="pt-BR" ;; esac ;;
  *) LOCALE="" ;;
esac

case "$LOCALE" in
  pt-BR)
    MSG_BAD_OPTION="Opção desconhecida: %s (veja --help)."
    MSG_BAD_LOCALE="Idioma desconhecido: %s (use en-US ou pt-BR)."
    MSG_BAD_PRINTER="Endereço de impressora inválido: %s (exemplo: ipp://192.168.1.50/ipp/print)."
    MSG_ASK_PRINTER="Endereço da impressora (exemplo: ipp://192.168.1.50/ipp/print): "
    MSG_NEED_PRINTER="Informe o endereço da impressora: --printer ipp://192.168.1.50/ipp/print"
    MSG_NEED_ROOT="Rode este script como root."
    MSG_NEED_PVE="Rode este script no host do Proxmox VE (não achei o comando pct)."
    MSG_NEED_GATEWAY="Com um IP fixo, informe também o roteador: --gateway 192.168.1.1"
    MSG_CTID_TAKEN="O ID %s já está em uso. Escolha outro com --ctid."
    MSG_TEMPLATE="Procurando o template do Debian..."
    MSG_NO_TEMPLATE="Não achei um template debian-12 ou debian-13 em '%s'."
    MSG_DOWNLOAD_TEMPLATE="Baixando o template %s..."
    MSG_TEMPLATE_FALLBACK="Este Proxmox não aceitou %s; tentando %s."
    MSG_CREATE="Criando o container %s (%s)..."
    MSG_NETWORK="Esperando a rede do container..."
    MSG_NO_NETWORK="O container não conseguiu acessar a internet. Confira a rede (--bridge, --ip, --gateway)."
    MSG_FAILED_AFTER_CREATE="
O container %s foi criado, mas a instalação não terminou.
  Tentar de novo:  pct exec %s -- env LC_ALL=C.UTF-8 bash /root/dont-dry-install.sh%s
  Ou apagar:       pct stop %s; pct destroy %s --purge"
    MSG_DONE="
Pronto! Container %s (%s).
  Configurações:   pct exec %s -- nano /etc/dont-dry.env  (depois: pct exec %s -- systemctl restart dont-dry)
  Impressora:      pct exec %s -- dont-dry info
  Atualizar:       pct exec %s -- env LC_ALL=C.UTF-8 bash /opt/dont-dry/deploy/install.sh"
    ;;
  *)
    MSG_BAD_OPTION="Unknown option: %s (see --help)."
    MSG_BAD_LOCALE="Unknown language: %s (use en-US or pt-BR)."
    MSG_BAD_PRINTER="Not a printer address: %s (example: ipp://192.168.1.50/ipp/print)."
    MSG_ASK_PRINTER="Printer address (example: ipp://192.168.1.50/ipp/print): "
    MSG_NEED_PRINTER="Give the printer's address: --printer ipp://192.168.1.50/ipp/print"
    MSG_NEED_ROOT="Run this script as root."
    MSG_NEED_PVE="Run this script on the Proxmox VE host (the pct command was not found)."
    MSG_NEED_GATEWAY="With a static IP, also give the router address: --gateway 192.168.1.1"
    MSG_CTID_TAKEN="ID %s is already in use. Pick another one with --ctid."
    MSG_TEMPLATE="Looking for the Debian template..."
    MSG_NO_TEMPLATE="No debian-12 or debian-13 template found in '%s'."
    MSG_DOWNLOAD_TEMPLATE="Downloading the %s template..."
    MSG_TEMPLATE_FALLBACK="This Proxmox did not accept %s; trying %s."
    MSG_CREATE="Creating container %s (%s)..."
    MSG_NETWORK="Waiting for the container's network..."
    MSG_NO_NETWORK="The container cannot reach the internet. Check the network (--bridge, --ip, --gateway)."
    MSG_FAILED_AFTER_CREATE="
Container %s was created, but the installation did not finish.
  Try again:     pct exec %s -- env LC_ALL=C.UTF-8 bash /root/dont-dry-install.sh%s
  Or remove it:  pct stop %s; pct destroy %s --purge"
    MSG_DONE="
Done! Container %s (%s).
  Settings:   pct exec %s -- nano /etc/dont-dry.env  (then: pct exec %s -- systemctl restart dont-dry)
  Printer:    pct exec %s -- dont-dry info
  Update:     pct exec %s -- env LC_ALL=C.UTF-8 bash /opt/dont-dry/deploy/install.sh"
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

# The same check as install.sh's, so a typo stops here, before a container exists
valid_printer() {
  local pattern='^(ipps?|https?)://[][A-Za-z0-9._~%@:-]+(/[A-Za-z0-9._~%/+-]*)?$'
  [[ "$1" =~ $pattern ]]
}

[ -z "$BAD_OPTION" ] || die "$MSG_BAD_OPTION" "$BAD_OPTION"
[ -z "$GIVEN_LOCALE" ] || [ -n "$LOCALE" ] || die "$MSG_BAD_LOCALE" "$GIVEN_LOCALE"
[ -z "$PRINTER" ] || valid_printer "$PRINTER" || die "$MSG_BAD_PRINTER" "$PRINTER"
if [ -z "$PRINTER" ]; then
  [ -t 0 ] || die "$MSG_NEED_PRINTER"
  read -r -p "$MSG_ASK_PRINTER" PRINTER
  valid_printer "$PRINTER" || die "$MSG_BAD_PRINTER" "$PRINTER"
fi
[ "$(id -u)" -eq 0 ] || die "$MSG_NEED_ROOT"
command -v pct >/dev/null || die "$MSG_NEED_PVE"
[ "$IP" = "dhcp" ] || [ -n "$GATEWAY" ] || die "$MSG_NEED_GATEWAY"
[ -n "$CTID" ] || CTID="$(pvesh get /cluster/nextid)"
! pct status "$CTID" >/dev/null 2>&1 || die "$MSG_CTID_TAKEN" "$CTID"

# What the installer gets: the printer and the language always, the rest only when given
ARGS=(--printer "$PRINTER")
[ -z "$LOCALE" ] || ARGS+=(--locale "$LOCALE")
[ -z "$SCHEDULE" ] || ARGS+=(--schedule "$SCHEDULE")
[ "$TEST_PRINT" -eq 0 ] || ARGS+=(--test-print)

CREATED=0
INSTALLER="$(mktemp)"
on_exit() {
  local status=$?
  rm -f "$INSTALLER"
  if [ "$status" -ne 0 ] && [ "$CREATED" -eq 1 ]; then
    # the retry runs under C.UTF-8 too, so it names everything this run chose
    say "$MSG_FAILED_AFTER_CREATE" "$CTID" "$CTID" "$(printf ' %q' "${ARGS[@]}")${REF:+ --ref $REF}" \
      "$CTID" "$CTID" >&2
  fi
}
trap on_exit EXIT

# The installer: from this checkout when the script runs from deploy/proxmox/ of a clone,
# otherwise downloaded. Never pick up some other install.sh lying next to a downloaded script.
DEPLOY_DIR="$(dirname "${BASH_SOURCE[0]}")/.."
if [ -f "$DEPLOY_DIR/proxmox/create-lxc.sh" ] &&
   [ -f "$DEPLOY_DIR/dont-dry.service" ] && [ -f "$DEPLOY_DIR/install.sh" ]; then
  cp "$DEPLOY_DIR/install.sh" "$INSTALLER"
elif [ -n "$REF" ]; then
  curl -fsSL "$RAW_URL/$REF/deploy/install.sh" -o "$INSTALLER"
else
  # the latest release's installer, which then checks that release's signature
  curl -fsSL "$RELEASES_URL/install.sh" -o "$INSTALLER"
fi

# newest_template VERSION: newest debian-VERSION-standard template, available or downloaded
newest_template() {
  {
    pveam available --section system 2>/dev/null | awk '{ print $2 }'
    pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{ print $1 }' | sed 's|^.*vztmpl/||'
  } | grep -E "^debian-$1-standard_.*_amd64\.tar\.(zst|gz|xz)\$" | sort -V | tail -n 1 || true
}

say "$MSG_TEMPLATE"
pveam update >/dev/null 2>&1 || true
# Newest Debian first; Proxmox releases older than Debian 13 refuse its template
TEMPLATES=()
for version in 13 12; do
  template="$(newest_template "$version")"
  [ -z "$template" ] || TEMPLATES+=("$template")
done
[ "${#TEMPLATES[@]}" -gt 0 ] || die "$MSG_NO_TEMPLATE" "$TEMPLATE_STORAGE"

NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[ -z "$GATEWAY" ] || NET="$NET,gw=$GATEWAY"

say "$MSG_CREATE" "$CTID" "$CT_HOSTNAME"
for i in "${!TEMPLATES[@]}"; do
  TEMPLATE="${TEMPLATES[$i]}"
  if ! pveam list "$TEMPLATE_STORAGE" | grep -q "vztmpl/$TEMPLATE"; then
    say "$MSG_DOWNLOAD_TEMPLATE" "$TEMPLATE"
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
  fi
  if pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
      --hostname "$CT_HOSTNAME" \
      --description "dont-dry: https://github.com/frkey/dont-dry" \
      --tags dont-dry \
      --cores 1 --memory "$MEMORY" --swap 256 \
      --rootfs "$STORAGE:$DISK" \
      --net0 "$NET" \
      --unprivileged 1 --features nesting=1 \
      --timezone host \
      --onboot 1 >/dev/null; then
    break
  fi
  NEXT="${TEMPLATES[$((i + 1))]:-}"
  [ -n "$NEXT" ] || exit 1
  say "$MSG_TEMPLATE_FALLBACK" "$TEMPLATE" "$NEXT"
done
CREATED=1
pct start "$CTID"
# Copied before the network check, so the retry command printed on failure works
pct push "$CTID" "$INSTALLER" /root/dont-dry-install.sh --perms 755

say "$MSG_NETWORK"
for _ in $(seq 60); do
  if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 || die "$MSG_NO_NETWORK"

# pct exec passes on this host's locale, which the new container does not have: use one it has
pct exec "$CTID" -- env LC_ALL=C.UTF-8 LANG=C.UTF-8 DONT_DRY_REPO="$REPO" DONT_DRY_REF="$REF" \
  bash /root/dont-dry-install.sh "${ARGS[@]}"

say "$MSG_DONE" "$CTID" "$CT_HOSTNAME" "$CTID" "$CTID" "$CTID" "$CTID"
