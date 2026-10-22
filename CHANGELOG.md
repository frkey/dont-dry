# Changelog

All notable changes to dont-dry are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.1] - 2026-10-22

### Fixed

- In a Proxmox container, the `dont-dry` command (and with it the installer's printer check and
  test page) failed without a word when run from a terminal, such as the Proxmox web Shell:
  systemd could not hand the host's terminal to the service user. The command no longer passes
  its input on, and the installer says when the command itself could not start.
- The documented `pct exec <id> -- ...` commands use `/usr/local/bin/dont-dry`, since `pct exec`
  runs without `/usr/local/bin` in its `PATH`.

## [0.2.0] - 2026-10-22

Never a page more than the schedule asks for, and a safer installer and release chain.

### Added

- When dont-dry starts after missing a scheduled print (the machine was off or dont-dry was
  stopped), it prints once to catch up. `CATCH_UP=false` turns this off.
- `dont-dry notify-test` sends a test notification, so a wrong `NOTIFY_URL` or token shows up
  before the day the printer fails.

### Fixed

- A `SCHEDULE` that never fires (such as February 30) is refused before anything prints. Before,
  `RUN_ON_START` printed a page, dont-dry stopped with an error, and the service restarted and
  printed again about every 30 seconds. The systemd unit no longer restarts on a configuration
  error.
- When daylight saving time ends, a time in the repeated hour prints once, not twice.
- A page is never sent twice when the printer's answer to it is lost (a dropped connection, a
  timeout, a broken answer): only failures before anything was sent are retried.
- A printer that sends unexpected attribute types no longer crashes `dont-dry info` or a print,
  and text from the printer is cleaned of control characters before it reaches the terminal or
  the log.
- Each request to the printer has a two-minute deadline, so a printer that answers a byte at a
  time cannot hold the scheduler.
- A `CUSTOM_PAGE` image with transparency prints white where it is transparent, not black, and
  a photo prints upright.
- `CUSTOM_PAGE` is checked at start-up (missing, unreadable, not a PDF or an image), and a
  malformed URL in a setting is a clean configuration error instead of a traceback.
- An update with `install.sh` no longer writes `LOCALE` when it was not given, and an option
  such as `--timezone` now changes the setting systemd reads (the last line), not a comment
  above it.
- A low ink level notifies when it first reaches `LOW_INK_THRESHOLD` or drops further, not at
  every print until the ink is refilled.
- PWG Raster is encoded row by row: at 600 dpi it needs about a third of the memory it did. A
  printer that lists only non-square PWG resolutions gets another format.
- A schedule with prints less than 3 hours apart (such as `* 9 * * 1`, sixty pages every Monday)
  is refused at start-up.
- dont-dry never prints again within an hour of a page that reached the printer, whatever asks
  for it. Pages printed with the `dont-dry` command of the systemd install are recorded too, and
  `docker-compose.yml` keeps the record in a volume (`STATE_DIR=/var/lib/dont-dry`), so it
  survives restarts.

### Security

- The documented install commands download `install.sh` and `create-lxc.sh` attached to the
  latest release (taken from its signed tag, with `SHA256SUMS`) instead of the code on `master`,
  and `create-lxc.sh` installs with that release's installer.
- Release images are published only from a tag signed with a key in `master`'s
  `RELEASE_SIGNERS`, and carry a build provenance attestation.
- `master` takes only pull requests with passing checks, release tags cannot be moved or
  deleted, and Dependabot security updates are on.

- An update never moves back to an older release (if newer tags disappear, say); `--ref` still
  installs any version on purpose.
- `install.sh` installs avahi, which announces the machine on the network, only when the printer
  address uses a `.local` name.

- Notifications no longer follow redirects, which forwarded `NOTIFY_TOKEN` to whatever address
  the redirect named.
- A user name or password in `PRINTER_URI` or `NOTIFY_URL` is refused at start-up (it was never
  used, only logged), and logs and error messages leave out URL query strings, where an ntfy
  token can sit.

## [0.1.0] - 2026-10-22

The first public release.

### Printing

- Prints a color maintenance page over IPP (`ipp://` / `ipps://`), the driverless protocol behind
  AirPrint and IPP Everywhere: no drivers, no CUPS, no vendor cloud.
- Picks a format the printer accepts: PDF, then PWG Raster, then JPEG. PDF and JPEG keep full
  color resolution, so the thin nozzle-check lines print sharp.
- The page has solid blocks, gradients and mixed colors for every ink, plus fine lines that show
  a clogging nozzle. A PDF, PNG or JPEG of your own can replace it (`CUSTOM_PAGE`), scaled to fit
  the page.
- Waits for the job to finish and reports how it ended. Only failures before a job reaches the
  printer are retried (asleep, offline, busy); an accepted job is never sent twice.
- Reads ink levels and warns when one is low.

### Scheduling

- Cron schedule with names, ranges, steps and `@weekly`-style macros, in any time zone; day
  fields combine as in Vixie cron.
- A Docker healthcheck that stays healthy during retries and long jobs, and a clean stop on
  `SIGTERM` at any point.
- Every setting is checked at start-up, so a typo stops dont-dry right away instead of on the
  day of the next print.

### Languages

- English and Brazilian Portuguese for the printed page, the notifications and the command
  output (`LOCALE=pt-BR`), with each language's date and clock. Logs and error details stay in
  English.

### Notifications

- A plain-text POST to [ntfy](https://ntfy.sh) or any webhook on failure, low ink or every
  print, with an optional bearer token. It says in plain words what happened, followed by the
  technical detail.

### Running it

- A multi-arch Docker image (amd64, arm64) that runs as an unprivileged user, works with a
  read-only file system, and installs exact versions checked against their hashes.
- `deploy/proxmox/create-lxc.sh`: a ready, unprivileged Debian container on Proxmox VE in two
  commands, with dont-dry running and the printer checked.
- `deploy/install.sh`: installs or updates dont-dry as a sandboxed systemd service on Debian 12+
  or Ubuntu 22.04+, only from releases signed with the dont-dry release key; running it again
  updates and keeps the settings.

[Unreleased]: https://github.com/frkey/dont-dry/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/frkey/dont-dry/releases/tag/v0.2.1
[0.2.0]: https://github.com/frkey/dont-dry/releases/tag/v0.2.0
[0.1.0]: https://github.com/frkey/dont-dry/releases/tag/v0.1.0
