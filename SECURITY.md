# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through
GitHub: go to the repository's **Security** tab and choose
[**Report a vulnerability**](https://github.com/frkey/dont-dry/security/advisories/new).

Include what an attacker can do, the steps to reproduce it and the version (the release or
commit) you tested. Never send a real `NOTIFY_TOKEN`. You will get an answer as soon as
possible, and credit in the fix if you want it.

Only the latest release and `master` are supported; fixes are not backported.

## What dont-dry protects

dont-dry is meant to run on a home or office **local network**, next to the printer.

- **It listens on nothing.** dont-dry only makes outgoing connections: to the printer and, if
  set, to the notification URL. There is no port to expose.
- **The printer is not fully trusted.** Answers are size-capped and parsed defensively; a
  malformed or oversized answer is reported as a failed print, never a crash.
- **Settings are checked at start-up.** The printer and notification addresses must be
  `ipp://`, `ipps://`, `http://` or `https://` URLs without a user name or password in them.
  `NOTIFY_TOKEN` is never logged, and logs and messages show URLs without their query string.
- **Notifications go where you point them.** They never follow a redirect, which would carry
  the token to another address.
- **The image is minimal and unprivileged.** It runs as a fixed non-root user, needs no
  capabilities, works with a read-only file system, and installs Python packages at exact
  versions checked against their hashes, on a base image pinned by digest.
- **The systemd unit is sandboxed.** A throwaway user that can only write `/var/lib/dont-dry`,
  with no capabilities and a system call filter. Its settings file, `/etc/dont-dry.env`, is
  readable by root only.
- **Installs are verified.** `deploy/install.sh` (also run by the Proxmox script) installs a
  release only when its tag is signed with the dont-dry release key and names itself with that
  same version, and the Python packages at exact versions checked against their hashes. A branch
  or tag you name with `--ref` is installed as it is.
- **The supply chain is pinned.** CI runs with a read-only token, pins its actions by commit,
  audits the locked packages for known vulnerabilities every week, and publishes images with
  provenance and an SBOM. Dependabot proposes updates.

## Known limits

These are accepted trade-offs, documented so you can decide whether they fit your network:

- **`ipp://` is plain HTTP.** Anyone who can watch your network traffic can see the test page
  (it holds nothing private). Use `ipps://` to encrypt it.
- **Printer certificates are not checked by default.** Printers almost always ship a
  self-signed certificate, so `ipps://` encrypts but does not authenticate unless
  `TLS_VERIFY=true` and the printer has a certificate your system trusts.
- **The notification URL is a secret of sorts.** On ntfy.sh, anyone who knows the topic can read
  it; pick a long random topic or use a token on a server you control.
