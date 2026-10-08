# Runtime CU evidence policy

Verified read-only against the two issuers on 2026-10-08, approximately 16:27–16:29 UTC. This is an implementation recommendation, not an assertion that these undocumented website interfaces will remain stable.

## Confirmed official machine-readable sources

### TIME 426030: 20,000 shares per CU

- Product URL: https://timeetf.co.kr/m11_view.php?idx=2
- Raw HTTP response: 200, `text/html; charset=utf-8`, 67,872 bytes in the recorded read
- Fund identity: HTML title `TIME 미국나스닥100액티브(426030)`; the product number element and structured data also contain 426030
- CU evidence is a definition-list pair in server-rendered HTML: `<dt>설정단위(좌)</dt>` followed by `<dd>20,000</dd>`
- The page's disclosure says portfolio constituent files are prepared for one CU
- Raw response SHA-256 in this read: `9cd20073cdcd962b52816b317184e90d6ab95922aa49ab8482f982d0457601ba`
- The general-purpose web extraction omitted the CU section. A normal bounded UTF-8 HTTP GET exposes it; no browser execution is needed

### KOACT 0015B0: 50,000 shares per CU

- Public product page: https://www.samsungactive.co.kr/etf/view.do?id=2ETFQ1
- Its product JavaScript fetches the following official JSON endpoint and renders the 설정단위 field from `info.product.qPerCu`:
  https://www.samsungactive.co.kr/api/v1/product/etf/2ETFQ1.do
- Raw HTTP response: 200, `application/json`, 65,249 bytes in the recorded read
- Relevant JSON fields:
  - `info.product.fId`: `2ETFQ1`
  - `info.product.stkTicker`: `0015B0`
  - `info.product.fNm`: `KoAct 미국나스닥성장기업액티브`
  - `info.product.qPerCu`: `50000` (a JSON string)
  - `info.product.gijunYMD`: `20261007`
  - `info.product.agreeT`: `/upload/agreement/2ETFQ1-B.pdf`
- Raw response SHA-256 in this read: `d8c44e7aeca25e80a72083948d02ea95f704852de7ac56ac44503092bec26732`
- Discovery trail: the page loads `/assets/js/product.js?v=20261006075046`; that JavaScript connects the endpoint and field to the displayed CU. Do not fetch or execute the JavaScript on every daily run
- Independent corroboration: https://www.samsungactive.co.kr/upload/agreement/2ETFQ1-B.pdf, Article 20 on PDF page 7 (zero-indexed page 6), specifies 50,000 and allows changes subject to one-month advance disclosure

These recorded whole-response hashes document this inspection. They are NOT appropriate permanent production allowlists: unrelated prices, holdings, notices, or page assets can change response bytes without changing CU.

## Recommended production gate

Replace the `date > 2026-10-08` static rejection with one fresh CU-evidence fetch per fund in each live collection run. Historical fixtures can retain their explicitly documented seed profile. A successful current CU read removes the routine manual date gate; it does not convert the product page into a historical CU database.

1. Restrict URLs to the exact two GET endpoints above. No user-controlled URL, redirect, cookie, credential, or authentication header. Require final URL equality, HTTP 200, expected media type and bounded body size (1 MiB is ample for the observed 65–68 KiB responses). Set a 25-second total deadline and bounded socket timeout. If using a retry, keep it within a clear overall run deadline
2. Require UTF-8 and successful structural parsing. For TIME, require the exact product title/code and exactly one CU definition pair whose entire value is a canonical positive integer after comma removal. For KOACT, reject duplicate JSON keys, require an object at `info.product`, exact fId/ticker/name, and qPerCu as a canonical positive integer. Reject booleans, floats, scientific notation, zero, negative values, null, missing keys and conflicting duplicate fields
3. Require parsed CU to equal the approved profile: TIME 20,000, KOACT 50,000. A new positive number is a detected change requiring review, never an automatic profile upgrade
4. Persist source URL, response status/type, UTC fetch time, SHA-256, parser version, fund identifiers, parsed CU and a concise extracted evidence field with each new valid snapshot. Persist the original response privately if the existing immutable-original mechanism supports it. Do not store credentials or unrelated account data
5. Compare quantities only when BOTH the current snapshot and its retained comparison baseline carry valid recorded CU evidence and the CU values match. Do not overwrite a baseline's old CU with today's value. Keep seed evidence explicitly distinguished from live evidence
6. If download, identity, parsing or CU comparison fails, mark the affected result `cu_unverified` or `cu_changed_review_required`; do not send a normal quantity-change/unchanged alert, and do not advance its successful comparison/delivery baseline. Emit a diagnostic through the already-authorized failure path. This is a recoverable validation hold, not a permanent calendar cutoff

Suggested claim: “Current issuer CU checked at collection time.” Avoid “CU officially certified for this historical date.” The sources have no demonstrated historical-effective-date parameter. Fresh observation also cannot prove the issuer's website is never stale. Keep original holdings as-of-date validation independent.

### Date handling

- KOACT `info.product.gijunYMD` was 20261007 on October 8 while checking a product whose October 8 constituent file is available. This is product/NAV metadata, not independent proof of the requested holdings date. Do not require equality to the requested constituent-file date
- Runtime evidence should be fetched in the same collection run, rather than renewing an arbitrary `CU_PROFILE_AS_OF` timestamp. New future dates are not automatically invalid if the actual issuer holdings file and live CU gate pass
- Do not use today's product CU to certify arbitrary old backfill dates. Use archived per-date evidence or a separately verified effective-date history for that task
- CU equality before and after a long missing-data gap cannot exclude an intervening change and reversion. Label gap comparisons honestly and avoid implying a complete daily history

## Corporate actions and interpretation

CU verification removes one specific quantity-comparison ambiguity. It does not identify executed transactions or eliminate splits, consolidations, mergers, spin-offs, identifier changes, cash substitutions, rounding, or other portfolio adjustments. Label the result as published quantity change per one CU, not “the manager bought/sold X shares” or total-fund trading volume.

If a broad proportional shift, sudden identifier replacement, or obvious split-like ratio is detected, flag it as possible corporate action/basis change for review. A heuristic is not proof and must not rewrite source quantities. Keep the original Decimal differences and source links. An actual CU change should hold the raw per-CU comparison; only a separately reviewed normalization policy should permit later cross-CU comparisons.

## Minimum regression tests

- Valid TIME HTML gives 20,000 with exact identity; whitespace around its dt/dd pair is allowed
- Missing CU, duplicate/conflicting CU pairs, bad title/code, numeric substrings in unrelated content, HTML error pages and response-size/deadline breaches fail closed
- Valid KOACT object gives 50,000; wrong fId/ticker/name, missing or malformed qPerCu, duplicate JSON keys and non-JSON bodies fail closed
- Changed TIME 40,000 or KOACT 100,000 holds comparisons and leaves the accepted baseline unchanged
- A verified post-2026-10-08 date passes the CU gate, proving removal of the perpetual static-date block
- KOACT product gijunYMD one day behind holdings date does not itself reject a separately verified workbook
- Validation failure never produces an “unchanged” outcome and never advances baseline/delivery state
- Each baseline retains its own CU/evidence; current evidence cannot retroactively bless an unverified historical snapshot

