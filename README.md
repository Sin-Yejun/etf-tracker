# Scheduled ETF quantity alerts

The daily workflow is registered for Korean weekdays at 08:30 (UTC Sunday–Thursday 23:30). It uses the existing verified private Telegram destination only when a newly verified disclosure changes exact stock quantities. The first accepted disclosure establishes a baseline without an alert. GitHub scheduling can be delayed.

See [daily operations](docs/daily-alerts.md) for durable repository history, current/historical CU gates, correction holds, CAS-before-send and uncertain-delivery quarantine. Manual execution of the daily workflow is always read-only and sends nothing. Initial scheduled execution and hosted history writes still require operational verification. No separate notification schedule is created.

The user approved the daily job contents:write permission to preserve public issuer originals and notification state under runtime/. Bot token and chat ID remain existing GitHub Secrets. The existing web dashboard is unchanged and is not automatically synchronized to this history.

---

## Original manual preparation package


This is a read-only, manually triggered GitHub Actions dry run for TIME 미국나스닥100액티브 (426030) and KOACT 미국나스닥성장기업액티브 (0015B0). It includes 12 archived official Excel disclosures and their existing snapshot records, with dates from 2026-09-28 through 2026-10-08.

## Current scope

- Source code, fixture validation, a manual workflow and local output artifacts are prepared
- No repository creation/push, workflow dispatch, scheduled job, live issuer probe, or notification has been performed as part of this package's local verification
- The workflow has only `workflow_dispatch`; no push, PR or scheduled trigger
- `contents: read`, no custom secrets, and checkout credentials are not persisted
- The optional issuer probe makes only two GET requests to fixed official URLs for the supplied date
- No sender, bot token handling, GitHub state commits, database integration or durable outbox is connected
- `notification-plan.mjs` and `delivery_state.py` are independent, pure preparation helpers. They cannot send anything. A local runner's filesystem is not durable delivery state

## Local usage

Use Python 3.12 on Linux or macOS. Fixture validation is offline after dependency installation. The live probe uses POSIX signal timers and is not supported on native Windows; the supplied GitHub runner is Ubuntu.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements.lock
python -m unittest discover -s tests -v
python -m unittest test_delivery_state -v
python -m etf_collector.collect --date 2026-10-08 --output output/my-fixture-run
```

An optional live download can be requested explicitly:

```sh
python -m etf_collector.collect --date 2026-10-08 --probe --output output/my-issuer-probe
```

Every output directory must be new. A repeated command with the same output path fails rather than overwriting evidence. Each run writes `report.json`, `summary.txt`, and content-addressed raw response files in `originals/`. A failed HTTP/Excel/date validation retains bounded response bytes when available; a timeout or oversize response cannot preserve a complete original. Archived input files remain unchanged.

## GitHub manual run, after an authorized upload

Open Actions → Manual ETF quantity dry run → Run workflow. Supply the official `as_of_date` as `YYYY-MM-DD`. Leave `probe_issuers` false for archived fixtures, or explicitly enable it to download from the issuers. Inputs are passed as quoted environment variables and validated as real calendar dates, never interpolated into shell commands.

The workflow installs the hash-locked dependency, runs the tests, produces the report, and retains output artifacts for 7 days even when the collection step reports an error. This artifact retention is diagnostic storage, not a permanent daily history. No custom secret is required. GitHub provides its standard read-only checkout/runtime mechanisms; this package does not configure credentials.

## Quantity and provenance contract

The Python parser ports the existing site's `lib/parser.ts` and `lib/quantities.ts` source-identity and raw-quantity rules. All 12 parsed originals must exactly match the corresponding `fixtures/seed.json` rows and SHA-256 values.

- KOACT identity: ISIN from the official sheet, not name or translated ticker
- TIME identity: uppercased, whitespace-normalized official source code
- Equity filtering: source code ends in `EQUITY`; cash/other rows are retained in snapshots but excluded from equity comparisons
- Quantities: exact canonical decimal strings and scaled integer subtraction, without an epsilon or rounding threshold
- A position addition means exact zero → positive quantity; removal means positive → exact zero, whether the zero row is present or absent. Missing → zero is unchanged
- XLSX numeric XML values are read as text. Legacy XLS numeric cells are stored as IEEE-754 in Excel; their shortest round-trip decimal representation matches the existing parser. Arithmetic thereafter is exact; precision absent from the original binary file cannot be recovered
- KOACT date evidence: the internal workbook date and fund title
- Live TIME date evidence: the date in the actual HTTP `Content-Disposition` filename; TIME worksheets do not contain a date. The fixed endpoint identifies the selected fund
- Archived TIME fixtures record that filename-date evidence existed, but the original HTTP header was not retained. Dry-run reports explicitly label this as archived evidence rather than claiming a newly observed response filename
- HTTP success alone is insufficient: magic bytes, format, source identity, headers, date and minimum stock count must validate

## Baselines and CU limitations

The baseline is always the latest strictly earlier verified fixture in this package. It is labeled `archived_fixture` in the report. It is **not** the preceding scheduled run or necessarily the previous trading day's disclosure. The collector does not append new snapshots to the archive. Production daily history is future work.

Configured CU profiles are TIME 20,000 ETF units and KOACT 50,000 ETF units. The profile is dated 2026-10-08; these workbooks do not independently prove the CU size. The archived comparison therefore uses that configured profile. For source dates after 2026-10-08, parsing may succeed but comparison is suppressed with `verified_cu_review_required` until issuer CU verification and a dated profile update are implemented. Do not advance the profile date without checking the official issuer source and preserving the evidence.

Quantity differences describe published holdings per one CU. They do not establish actual executed trades or fund-wide buying/selling, and changes in CU size or corporate actions can affect interpretation.

## Statuses

- `verified_changed`: date and source verified; nonzero equity quantity differences against the archived baseline
- `verified_unchanged`: date and source verified; zero equity quantity differences against the archived baseline
- `verified_no_baseline`: source verified; no strictly earlier archived disclosure
- `verified_cu_review_required`: source date verified; CU profile is too old for comparison
- `fixture_error`, `download_error`, `validation_error`: collection cannot establish a current disclosure; `asOf` is null, `errors` is populated, and no comparison is emitted

A failure or a not-yet-published file never becomes “unchanged.” `requestedDate`, verified `asOf`, baseline date and collection timestamp remain separate. In a dry run, `archivedCollectedAt` retains the original collection timestamp.

## Boundaries and security controls

- URLs can only be constructed for the two fixed issuer HTTPS endpoints; no user-provided URL is accepted
- Every redirect is refused; TLS verification is left enabled
- 10-second socket timeout, 25-second total per-issuer deadline, 4 MiB response cap
- XLSX caps: 100 ZIP entries, 16 MiB expanded data, 2,000 rows, 32 columns; duplicate members/cells, DTD/entities, external worksheet links and formulas are rejected
- XLS reader: `xlrd==2.0.1`, wheel SHA-256 pinned in `requirements.lock`; no macros are executed
- Three official Actions are pinned by verified full commit SHA; see `docs/dependency-sources.md`
- Python tracks the 3.12 security-patch line rather than pinning an obsolete patch; local tests used Python 3.12.14. The hosted runner image and runtime tools are not bit-for-bit locked
- Outbound notifications, automatic retries and permanent data persistence require separate reviewed implementation and authorization

## Verified result

Local clean-venv installation validated the dependency hash. The original Oct 7 → Oct 8 comparison shows:

- TIME: 41 equities, zero changed quantities
- KOACT: 45 equities in the union, 44 changed quantities, one new position and no exits
- SPCX: +63
- AMAT: −0.01
- KOACT: 43 negative quantity deltas

See `docs/validation.md` for executed versus unexecuted checks. See `docs/telegram-setup.md` for the separately prepared notification setup guidance.
