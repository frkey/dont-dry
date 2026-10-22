# Contributing to dont-dry

Thanks for your interest! dont-dry is a small project, and every kind of help counts: a report
from a printer model nobody has tried, a clearer sentence in the docs, or a pull request.

- [Ground rules](#ground-rules)
- [Development setup](#development-setup)
- [Project layout](#project-layout)
- [Making a change](#making-a-change)
- [Translations](#translations)
- [Testing on real printers](#testing-on-real-printers)
- [Releases](#releases)

## Ground rules

- **Never waste ink.** A job the printer accepted is never sent again, and dont-dry never prints
  more than the schedule asks for. Any change to retries or job handling keeps this rule.
- **No drivers, no clouds.** dont-dry talks IPP straight to the printer with the standard library
  and Pillow. A new runtime dependency needs a very good reason.
- **English in the repository**: code, comments, commits, issues and pull requests. The README
  exists in English and Brazilian Portuguese; update both in the same change.
- [`CLAUDE.md`](CLAUDE.md) holds the full conventions (it is written for AI assistants, and just
  as useful for people): project layout, rules, labels and the review process.

## Development setup

You need Python 3.10 or newer.

```sh
git clone https://github.com/frkey/dont-dry.git
cd dont-dry
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Then:

```sh
pytest                          # the whole suite, a few seconds, no printer or network needed
dont-dry render page.png        # look at the test page
docker build -t dont-dry .      # the image
```

The tests run against a fake IPP printer in the same process (`tests/fake_printer.py`). CI runs
them on Python 3.10, 3.12 and 3.13, builds the image and uploads the rendered page as an
artifact.

## Project layout

```
src/dont_dry/
  cli.py        commands and the scheduler loop (heartbeat, retries, signals)
  config.py     reads and checks the environment variables
  printer.py    inspects the printer, picks a format, sends the page and follows the job
  ipp.py        a minimal IPP/1.1 client (RFC 8010 / 8011)
  pwg.py        PWG Raster encoder (and a decoder for the tests)
  page.py       draws the maintenance page
  schedule.py   cron expressions
  i18n.py       translations and date formats; the catalogs are in locales/
  notify.py     ntfy / webhook notifications
  state.py      heartbeat and last-run files, written atomically
deploy/         systemd unit and the hashed lock the image installs
tests/          pytest suite with a fake IPP printer
docs/           images for the README
```

## Making a change

1. **Find or open an issue** first, unless the change is tiny. Search
   [open and closed issues](https://github.com/frkey/dont-dry/issues?q=is%3Aissue) to avoid
   duplicates.
2. **Branch from `master`** and keep the change focused on that issue.
3. **Add or update tests.** Tests are hermetic: no network, no real printer, and the conftest
   clears every dont-dry setting from the environment.
4. **Changed the page?** Render it in both languages and look at it (`dont-dry render page.png`
   and `LOCALE=pt-BR dont-dry render pagina.png`, on A4 and with `PAGE_SIZE=letter`): Portuguese
   labels are longer. If the README images show the change, regenerate both with the image's
   DejaVu font: `docker build -t dont-dry .`, then for each language
   `docker run --rm -e LOCALE=pt-BR --entrypoint sh dont-dry -c 'dont-dry render /tmp/p.png --dpi 90 >/dev/null && cat /tmp/p.png' > docs/sample-page-pt-BR.png`
   (and `LOCALE=en-US` into `docs/sample-page.png`).
5. **Anything user-visible?** Update `README.md` and `README.pt-BR.md`, and add a line to
   `## [Unreleased]` in `CHANGELOG.md`.
6. **Open a pull request** with the template: what changes and why, `Fixes #N` (or `Refs #N`),
   and the test plan.

### Review

Every pull request is reviewed until a round finds no new real defect (correctness, security,
wasted ink, or a rule in `CLAUDE.md`). Before opening yours, review your own diff line by line.
The maintainer then reviews it, you handle each finding (fix it, explain why it does not apply,
or move it to an issue) and push, and the new commits are reviewed the same way. Style
suggestions alone do not hold a pull request back.

### Dependencies

The Dockerfile and the systemd install use `deploy/requirements.txt`: exact versions with hashes,
setuptools included, so a build never fetches an unpinned package. After changing the
dependencies in `pyproject.toml` (or `deploy/requirements.in`), run the command at the top of
that file again (it needs [uv](https://docs.astral.sh/uv/)); `tests/test_lock.py` fails until the
lock names every dependency. Dependabot proposes version bumps weekly, and CI's `lock` job
installs the pinned versions, runs the tests with them and checks them against known
vulnerabilities.

## Translations

dont-dry speaks English (the reference) and Brazilian Portuguese on the printed page, in
notifications and in the output of `info`, `next` and `render`. Logs and error details stay in
English. To add a language:

1. Copy `src/dont_dry/locales/en-US.json` to `<code>.json` (for example `es-ES.json`) and
   translate the values. Keep the `{placeholders}`; the `format.*` keys choose their own fields
   (see `Translator.datetime`). `tests/test_i18n.py` fails if a key or a placeholder is missing.
2. Add the code to `LOCALES` in `src/dont_dry/i18n.py` and to the `LOCALE` row of the READMEs'
   configuration tables.
3. Render the page in the new language and look for labels that overflow.

## Testing on real printers

dont-dry should work with any printer that speaks IPP Everywhere or AirPrint. If you run it on a
model that is not in the README yet, a short report is very welcome, even when everything works:
open an issue with the model, the output of `dont-dry info` and how the page came out.

## Releases

Versions follow [Semantic Versioning](https://semver.org/). The version lives only in
`src/dont_dry/__init__.py`. To release:

1. In a pull request, set `__version__` and move `[Unreleased]` in `CHANGELOG.md` to a dated
   section (`## [X.Y.Z] - YYYY-MM-DD`), with its compare link at the bottom.
2. After it is merged, make a signed tag on `master` and push it:
   `git tag -s vX.Y.Z -m "dont-dry X.Y.Z"` then `git push origin vX.Y.Z`. CI publishes
   `ghcr.io/frkey/dont-dry:X.Y.Z`, `:X.Y` and `:latest`. Publish a GitHub release with the
   changelog section as its notes.

Pushes to `master` publish the `:edge` image only; `:latest` always means the latest release.
