# CLAUDE.md

Guidance for Claude (and other contributors) working on this repository.

## Language policy

- **Everything in the repository is written in English**: code, identifiers, comments,
  docstrings, commit messages, pull requests, issues, tests, CI and technical docs. This applies
  even when the conversation with the maintainer happens in Portuguese.
- **The README exists in two languages.** `README.md` (English, the reference) is mirrored by
  `README.pt-BR.md` (natural Brazilian Portuguese). Update both in the same change.
  Developer-only docs (this file, `CONTRIBUTING.md`, `SECURITY.md`, code comments) are English
  only.
- **What users see is translated** (en-US, the reference, and pt-BR): the printed page, the
  notifications and the output of `info`, `next` and `render`. Never hard-code such a string:
  add a key to `src/dont_dry/locales/en-US.json` and use the `Translator`
  (`t("section.key", name=value)`), and add the same key to `pt-BR.json` in the same change
  (`tests/test_i18n.py` fails if the catalogs or their placeholders differ). Format dates with
  `Translator.datetime()`, never `strftime` (it depends on the OS locale).
- **Log lines, exception messages and `PrintResult.message` stay in English**, so they can be
  searched; a notification shows the translated `result.<reason>` sentence above that message.
  A new `PrintResult.reason` goes into `printer.REASONS` and gets a `result.<reason>` key.
- Write en-US text in US English with the 12-hour clock ("Oct 22, 2026 9:00 AM"), and pt-BR text
  in natural Brazilian Portuguese with the 24-hour clock ("22/10/2026 09:00").
- The setting is `LOCALE`, not `LANGUAGE`: gettext reads that one, and desktops set it to lists
  such as `en_US:en`.

## Project overview

dont-dry prints a maintenance page on a schedule so the printheads of an idle inkjet printer
don't dry out. It talks IPP straight to the printer: no drivers, no CUPS, no vendor cloud.

- `src/dont_dry/cli.py`: the commands and the `run` loop (`Scheduler`): it waits for the next
  cron time, prints, keeps the heartbeat fresh for the Docker healthcheck and stops on
  `SIGTERM`/`SIGINT`, also in the middle of a retry delay or a job wait.
- `printer.py`: one print attempt (`print_page`): inspect the printer, choose a format, build the
  document, send it and follow the job. It returns a `PrintResult`, never raises for a printer
  problem.
- `ipp.py` (IPP/1.1 client), `pwg.py` (PWG Raster), `page.py` (the test page), `schedule.py`
  (cron), `config.py` (environment variables), `notify.py` (ntfy/webhooks), `state.py`
  (heartbeat and last-run files), `i18n.py` and `locales/` (translations).
- `deploy/`: the systemd unit and `requirements.txt`, the hashed lock that the Dockerfile and the
  systemd install use.

## Commands

```sh
pip install -e ".[dev]"
pytest                                  # all tests, no printer or network needed
dont-dry render page.png                # the test page (.pdf, .png, .jpg or .pwg)
SCHEDULE="0 9 * * 1,4" dont-dry next    # upcoming print times
LOCALE=pt-BR dont-dry render pagina.png # the page in Brazilian Portuguese
docker build -t dont-dry . && docker run --rm --read-only --tmpfs /tmp dont-dry next
```

## Conventions

- Default branch: `master`. Supported Python: 3.10+ (the image uses 3.13).
- **Never waste ink.** Only failures before a job reaches the printer are retried
  (`PrintResult.retryable`): unreachable, not accepting jobs, or a `RETRYABLE_STATUSES` answer.
  A job the printer accepted is never resent, whatever happened to it.
- **The scheduler must not die.** `print_page` turns printer problems into a failed
  `PrintResult`; `_print_once` turns anything unexpected into one too, with a traceback in the
  log. Configuration problems, on the other hand, stop dont-dry at start-up (exit code 2), so a
  typo never waits for the day of the next print.
- **Every wait goes through `Scheduler.wait`** (or the `Wait` passed to `print_page`), never
  `time.sleep`: it keeps the heartbeat fresh and returns `False` when dont-dry is stopping.
- **Treat printer answers as untrusted.** `ipp.decode_message` raises only `IPPError`, and
  responses are capped at `ipp.MAX_RESPONSE`.
- **Dependencies**: the standard library and Pillow (plus `tzdata`). After changing them, run the
  command at the top of `deploy/requirements.txt`; `tests/test_lock.py` checks the lock.
- **The image** runs as uid 10001, needs no capabilities and must keep working with
  `--read-only --tmpfs /tmp`; the base image is pinned by digest and the CI actions by commit.
- **The systemd unit** stays sandboxed; if you add an option, check that it still starts in an
  unprivileged LXC container with nesting.
- The version lives only in `src/dont_dry/__init__.py` (`pyproject.toml` reads it).
- Tests are hermetic: `tests/conftest.py` clears every setting, and printing tests use the fake
  printer in `tests/fake_printer.py`. When you add a setting, add it to `SETTINGS` there, to
  `.env.example` and to the configuration table in both READMEs.

## Issues and labels

Work is tracked in GitHub issues on `frkey/dont-dry` (use `gh issue ...`).

- **Open an issue** for every bug, follow-up, deferred review finding or idea that is not done in
  the current change. Never leave follow-ups only in a chat, a PR comment or a local note.
- **Before opening one**, search open and closed issues and comment on an existing issue instead
  of creating a duplicate.
- **Write issues in English.** Title: short and imperative. Body: context, current vs expected
  behavior (steps to reproduce for bugs), acceptance criteria, and the files involved.
- **Labels**: one `type:` (`bug`, `feature`, `improvement`, `docs`, `chore`, `security`), one
  `priority:` (`critical`, `high`, `medium`, `low`) and at least one `area:` (`printing`, `ipp`,
  `page`, `scheduler`, `notify`, `docker`, `deploy`, `docs`). Creating or editing labels changes
  the public repository: ask the maintainer before running `gh label create`/`gh label edit`.

## Pull requests

- **Review until clean, before opening a pull request.** Once the work is committed and the tests
  pass, run `git fetch` and `/code-review high origin/master...HEAD` (always name the range). Handle
  its findings, commit, and review the fix commits (`/code-review high <last-reviewed>..HEAD`)
  until a round reports no new real defect: correctness, security, wasted ink or a rule in this
  file. Style suggestions do not keep the loop going.
- **Stop and ask** if the fourth round of the same loop still finds a new real defect.
- **Handle every finding**: fix it; or explain in the PR description why it does not apply; or,
  if it is real but out of scope, open an issue and link it.
- **Changelog**: a PR with a user-visible change adds a line to `## [Unreleased]` in
  `CHANGELOG.md`. Releases are described in `CONTRIBUTING.md`.
- **Describe the PR with the template** (`.github/pull_request_template.md`).

## Privacy

This is a public repository. Never commit `.env` (it can hold `NOTIFY_TOKEN` and a private ntfy
topic). Use documentation addresses such as `192.168.1.50` and `ntfy.example` in code, tests and
docs.
