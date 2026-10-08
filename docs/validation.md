# Local validation, 2026-10-08

Executed successfully on Python 3.12.14:

1. A clean virtual environment installed `xlrd==2.0.1` from the exact SHA-256-locked wheel
2. All 12 archived XLS/XLSX workbook bytes matched their snapshot SHA-256 values
3. All 12 independently parsed row lists matched `fixtures/seed.json` exactly
4. Actual Oct 7→8 results: TIME unchanged (41 equities); KOACT 44 changed (45 equities), SPCX +63, AMAT −0.01, 43 negative deltas
5. Exact decimal arithmetic, zero-position additions/removals and missing-zero handling
6. Wrong date, wrong fund/schema, missing TIME filename, HTML, malformed dates, size limits, redirects, DTD/entities and XLSX formulas rejected
7. A failed issuer-response validation retains bounded raw bytes, sets a null current as-of, and does not emit unchanged/comparison
8. Network failure and absent archived date are explicit errors
9. Dates later than the configured CU profile date require review and suppress comparison
10. Offline dry run attempted no network requests and refused to overwrite an existing output directory
11. Four independent delivery-state helper tests passed; no transport or durable remote state is attached
12. Workflow YAML parsed successfully; manual-only trigger, read-only contents permission, full-SHA official Actions and absent secret references were checked

Not executed: GitHub workflow dispatch, actual issuer-network probe, bot setup/login, Telegram API calls, scheduled collection, remote repository writes, remote durable outbox, and end-to-end notifications.

Representative output is at `output/verified-final-fixture-run/report.json` locally; output is excluded from source control. The workflow will create its own run artifact only after an authorized upload and manual dispatch.
