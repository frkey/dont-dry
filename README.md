<div align="center">

# dont-dry

**Keep an idle inkjet printer from clogging: print a small maintenance page on a schedule.**

[![CI](https://github.com/frkey/dont-dry/actions/workflows/ci.yml/badge.svg)](https://github.com/frkey/dont-dry/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Docker: amd64 | arm64](https://img.shields.io/badge/docker-amd64%20%7C%20arm64-blue.svg)](https://github.com/frkey/dont-dry/pkgs/container/dont-dry)

**English** · [Português (Brasil)](README.pt-BR.md)

[Quick start](#quick-start-docker) · [Configuration](#configuration) · [Troubleshooting](#troubleshooting) · [Contributing](CONTRIBUTING.md)

</div>

Inkjet printers that sit idle clog: the ink dries in the nozzles, and the next print comes out
streaky or not at all. **dont-dry** prints a small color maintenance page on a schedule (by
default Mondays and Thursdays at 09:00), so the ink keeps flowing.

It talks to the printer directly over **IPP**, the same driverless protocol AirPrint and Android
use, so it works with any modern network printer, needs no drivers or CUPS, and does not depend
on vendor clouds such as HP ePrint or Epson Connect (which can disappear with a firmware update).

<p align="center"><img src="docs/sample-page.png" width="300" alt="The maintenance page: solid blocks, gradients and mixed colors for every ink, and rows of fine lines"></p>

The page has solid blocks, gradients and mixed colors for every ink, plus fine nozzle-check
lines: gaps in those lines tell you a nozzle is starting to clog.

## Features

- **Driverless printing** over `ipp://` / `ipps://`, with the standard library plus Pillow.
- **Picks a format the printer accepts**: PDF, then PWG Raster (which every IPP Everywhere
  printer must take), then JPEG.
- **Cron-style schedule** with time zone support.
- **Never wastes ink**: it waits for the job to finish and reports the result, retries when the
  printer is asleep or offline, and never sends a job twice once the printer accepted it.
- **Ink levels**: warns when one is low (if the printer reports them).
- **Notifications** via [ntfy](https://ntfy.sh) or any webhook.
- **English or Brazilian Portuguese** for the page, the notifications and the command output
  (`LOCALE=pt-BR`).
- **Small, locked-down Docker image** (amd64 + arm64): non-root, read-only, no open ports, with a
  healthcheck. Or a sandboxed systemd service without Docker.

## Quick start (Docker)

1. **Give the printer a fixed IP** with a DHCP reservation in your router.
2. Check that dont-dry can see it:

   ```sh
   docker run --rm -e PRINTER_URI=ipp://192.168.1.50/ipp/print ghcr.io/frkey/dont-dry info
   ```

   ```
   Printer:        Smart Tank 720  (HP Smart Tank 720 series)
   State:          idle  [none]
   Accepting jobs: yes
   Formats:        application/pdf, image/pwg-raster, image/jpeg, ...
   Ink:            Cyan: 70%, Magenta: 65%, Yellow: 80%, Black: 55%
   dont-dry would send: application/pdf
   ```

3. Print one page right now:

   ```sh
   docker run --rm -e PRINTER_URI=ipp://192.168.1.50/ipp/print ghcr.io/frkey/dont-dry print
   ```

4. Run it on a schedule:

   ```sh
   git clone https://github.com/frkey/dont-dry && cd dont-dry
   cp .env.example .env && chmod 600 .env   # set PRINTER_URI, SCHEDULE and TZ
   docker compose up -d
   docker compose logs -f
   ```

`ghcr.io/frkey/dont-dry:latest` is the latest release (`:edge` follows `master`). To build the
image yourself: `docker build -t dont-dry .`

### Finding the printer URI

- **HP:** `ipp://<ip>/ipp/print`
- **Epson, Brother, Canon:** usually `ipp://<ip>/ipp/print` as well; some use `ipp://<ip>/ipp`
- Not sure? On Linux, `ippfind` (package `cups-ipp-utils`) lists every IPP printer on the network.
  Or open the printer's web page (type its IP in a browser) and look for AirPrint/IPP settings.
- Use the IP address, or a name your router's DNS resolves: `.local` (mDNS) names do not resolve
  inside the container.
- Use `ipps://` instead of `ipp://` to encrypt the connection; self-signed certificates are
  accepted unless `TLS_VERIFY=true`.

## Running on Proxmox

Any of these works. dont-dry only needs to reach the printer's IP on port 631.

**A. Docker inside an LXC (lightest).** Create a Debian 12 or 13 LXC (1 core, 256 MB RAM and 2 GB
disk are plenty), turn on *Options → Features → nesting* (and *keyctl* for unprivileged
containers), then install Docker and follow the [quick start](#quick-start-docker):

```sh
apt update && apt install -y curl git
curl -fsSL https://get.docker.com | sh
```

**B. Plain LXC, no Docker.** Python and a sandboxed systemd service, with the same pinned and
hash-checked packages as the image:

```sh
apt update && apt install -y python3-venv git fonts-dejavu-core
git clone https://github.com/frkey/dont-dry /opt/dont-dry
python3 -m venv /opt/dont-dry/venv
/opt/dont-dry/venv/bin/pip install --require-hashes -r /opt/dont-dry/deploy/requirements.txt
/opt/dont-dry/venv/bin/pip install --no-deps --no-build-isolation /opt/dont-dry
install -m 600 /opt/dont-dry/.env.example /etc/dont-dry.env && nano /etc/dont-dry.env
cp /opt/dont-dry/deploy/dont-dry.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now dont-dry
journalctl -u dont-dry -f
```

To update, run `git -C /opt/dont-dry pull`, the two `pip install` lines again, and
`systemctl restart dont-dry`. The unit's sandbox needs the *nesting* feature, which is on by
default for new containers.

**C. An existing Docker VM.** If you already run Docker in a VM, add the service from
[`docker-compose.yml`](docker-compose.yml) to your stack.

If the printer is on a different VLAN than the LXC or VM, allow TCP 631 between them.

## Configuration

All settings are environment variables (see [`.env.example`](.env.example)). dont-dry checks
them all when it starts and stops with a clear message if one is wrong.

| Variable | Default | Description |
|---|---|---|
| `PRINTER_URI` | *(required)* | e.g. `ipp://192.168.1.50/ipp/print` |
| `SCHEDULE` | `0 9 * * 1,4` | Cron expression: minute hour day-of-month month day-of-week. Names (`mon`, `jan`), ranges, lists, steps and `@weekly`-style macros work. |
| `TZ` | `UTC` | Time zone for the schedule, e.g. `America/Sao_Paulo` |
| `RUN_ON_START` | `false` | Print once when the scheduler starts |
| `PAGE_SIZE` | `a4` | `a4` or `letter` |
| `LOCALE` | `en-US` | Language of the page, notifications and command output: `en-US` or `pt-BR` |
| `PAGE_TITLE` | `dont-dry maintenance page` | Heading printed on the page (the default follows `LOCALE`) |
| `DOCUMENT_FORMAT` | `auto` | `auto`, `application/pdf`, `image/pwg-raster` or `image/jpeg` |
| `CUSTOM_PAGE` | | Path to your own PDF, PNG or JPEG to print instead (images are scaled to fit) |
| `RETRIES` | `3` | Extra attempts when the printer can't be reached (0 to 20) |
| `RETRY_DELAY` | `300` | Seconds between attempts |
| `JOB_TIMEOUT` | `300` | Seconds to wait for a job to finish |
| `LOW_INK_THRESHOLD` | `10` | Warn when an ink is at or below this percentage |
| `NOTIFY_URL` | | URL that receives a plain-text POST, e.g. `https://ntfy.sh/my-topic` |
| `NOTIFY_TOKEN` | | Optional bearer token for the notification URL |
| `NOTIFY_ON` | `failure` | `failure` (also low ink), `always` or `never` |
| `TLS_VERIFY` | `false` | Verify the printer's certificate for `ipps://` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `STATE_DIR` | `~/.local/state/dont-dry` | Where the heartbeat and `last-run.json` go (`/tmp/dont-dry` in the image) |

A retry only happens when the job never reached the printer (it was asleep, offline, busy or not
accepting jobs). If the printer accepted a job and then aborted it, dont-dry reports the failure
instead of wasting another page.

### Notifications

With [ntfy](https://ntfy.sh), pick a long, random topic (anyone who knows it can read it), set
`NOTIFY_URL=https://ntfy.sh/<topic>` and subscribe to the topic in the ntfy app. Failures arrive
with high priority; a successful print notifies only when an ink is low, unless
`NOTIFY_ON=always`. Any other webhook that accepts a plain-text POST works too; the title goes in
a `Title` header.

## Commands

```
dont-dry run            # scheduler (the container default)
dont-dry print          # print now (--retry to retry like the scheduler)
dont-dry info           # printer status, supported formats, ink levels
dont-dry next -n 5      # upcoming print times for SCHEDULE/TZ
dont-dry render out.pdf # save the test page locally (.pdf, .png, .jpg or .pwg)
dont-dry healthcheck    # exit 0 if the scheduler is alive (used by the image)
```

With Docker: `docker compose exec dont-dry dont-dry info`, or `docker compose run --rm dont-dry next`.

## Troubleshooting

| You see | What to do |
|---|---|
| `cannot reach printer at ...` | Check the IP, that the printer is on, and that TCP 631 is open between dont-dry and the printer. Printers that are asleep usually wake up for the next retry. |
| `printer answered HTTP 404` | The path is wrong: try `/ipp/print`, then `/ipp`. |
| `printer supports none of application/pdf, image/pwg-raster, image/jpeg` | The printer does not do driverless printing (IPP Everywhere / AirPrint), so dont-dry cannot talk to it. |
| `printer rejected the job: client-error-document-format-not-supported` | Set `DOCUMENT_FORMAT` to another format that `dont-dry info` lists. |
| `job N processing (still processing after 300s)` | The printer was busy, for example cleaning its heads. Raise `JOB_TIMEOUT`. |
| `printer is not accepting jobs (...)` | The printer is paused or shows an error on its panel (the reasons in parentheses say which). Fix it there; dont-dry retries on its own. |
| The container is `unhealthy` | Read `docker compose logs dont-dry`: the scheduler stopped or cannot write its heartbeat to `STATE_DIR`. |
| Lines missing or streaky on the page | That is what the page is for: run the head cleaning from the printer's menu or app, then `dont-dry print`. If it keeps happening, print more often. |

For more detail, set `LOG_LEVEL=DEBUG`.

## How often should it print?

Printer makers commonly suggest printing at least once a week to keep the heads healthy. Twice a
week (the default) leaves some margin. In dry climates, or if the nozzle-check lines start
showing gaps, print more often.

## Security

dont-dry opens no port: it only connects to the printer and, if set, to the notification URL.
The image runs as a non-root user with a read-only file system, and the systemd unit is
sandboxed. See [SECURITY.md](SECURITY.md) for what it protects, the known limits and how to
report a vulnerability.

## Contributing

Bug reports, printer reports ("works on my Epson L3250") and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md). The tests run against an in-process fake IPP printer, so no
hardware is needed:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)
