# Changelog

All notable changes to dont-dry are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- A `SCHEDULE` that never fires (such as February 30) is refused before anything prints. Before,
  `RUN_ON_START` printed a page, dont-dry stopped with an error, and the service restarted and
  printed again about every 30 seconds. The systemd unit no longer restarts on a configuration
  error.
- When daylight saving time ends, a time in the repeated hour prints once, not twice.
- A schedule with prints less than 3 hours apart (such as `* 9 * * 1`, sixty pages every Monday)
  is refused at start-up.
- dont-dry never prints again within an hour of a page that reached the printer, whatever asks
  for it. Pages printed with the `dont-dry` command of the systemd install are recorded too, and
  `docker-compose.yml` keeps the record in a volume, so it survives restarts.

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

[Unreleased]: https://github.com/frkey/dont-dry/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/frkey/dont-dry/releases/tag/v0.1.0
