# Source register

Every dataset that enters the pipeline is listed here with its provenance, licence, and collection method. A source is admitted only when it passes the admission check below.

## Admission check

A source may enter the raw layer only if all of the following hold:

1. **Known publisher.** A named organisation or government agency published it.
2. **Reuse is permitted.** An explicit licence or published terms allow reuse. Missing terms are recorded and the decision is made by the team, not assumed.
3. **Obtained through the publisher's own channel.** Downloaded with the publisher's button, export, or documented API, without getting around any access control or bot protection.
4. **Verifiable.** A checksum is recorded in `config/settings.yml` and checked on every ingestion; a mismatch stops the run.
5. **Method disclosed.** How the publisher collected the data is stated here, including methods the team would not use itself.

Sources added after the primary source must be official (government-published) and are used to add fields and flag disagreements. They never overwrite primary values; conflicts are recorded for validation.

## Register

| Source | Role | Status |
| --- | --- | --- |
| BetterGov.ph DPWH Infrastructure Transparency Dataset (Hugging Face) | Primary | Admitted |
| DPWH transparency API (`api.transparency.dpwh.gov.ph`) | Original source | Not collected: blocked automated access |
| PhilGEPS Open Data (`open.philgeps.gov.ph`) | Official cross-check | Pending: download availability and terms not yet confirmed |

## BetterGov.ph DPWH Infrastructure Transparency Dataset

| Item | Value |
| --- | --- |
| Publisher | BetterGov.ph |
| Dataset page | https://huggingface.co/datasets/bettergovph/dpwh-transparency-data |
| File used | `dpwh_transparency_data_all_details.parquet` |
| Revision | `648ea96af4f7625d606fda0b78803917913a26b7` (last modified 22 January 2026, UTC) |
| Licence | CC0 1.0 Universal |
| SHA-256 | `953f0bf99d162c062210219cc5f75c22df85049c24c0dae602c1f4dc976a7c97` (matches the value published on the dataset page) |
| Size | 114,819,143 bytes |
| Rows | 248,421, one per unique `contractId` |
| Columns | 52 |
| Coverage | 93.5% of the 265,582 projects the portal reported on 30 September 2026 (see `docs/reconciliation_baseline.md`) |

**Collection method (as stated by the publisher).** The dataset README states that the data was collected from the DPWH transparency API (`/projects`) with a third-party scraper (`csiiiv/dpwh-transparency-data-api-scraper`) that uses curl-cffi with browser fingerprint impersonation, concurrent requests, and a rate of about 300 requests per 10 minutes. Group Zeta did not collect this data and does not use that method; the team's own extractor identifies itself and stops on any bot-protection challenge. The dataset is used as a published third-party release under its CC0 licence.

**Why `_all_details` and not the smaller file.** The same revision also contains `dpwh_transparency_data.parquet` (248,220 rows, SHA-256 `5b411cf3f112fabd1913c70681791e5e2b78b43a8393f489f48bd882f154e123`). Every one of its contract IDs appears in `_all_details`, which adds 201 contracts and procurement fields (approved budget for the contract, award amount, bidders, bid dates, components, document links).

**Known limitations.**

- Snapshot age: the release reflects the portal around January 2026. Status counts differ from the September 2026 baseline in the expected direction (more completed, fewer ongoing, many new projects in procurement).
- The dataset card describes only the smaller file and lists a size category of 1K to 10K rows and 22 fields; both are out of date.
- `amountPaid` is zero in every row.
- Document links point to `dcs.infrawatch.ph`; the pipeline stores them as text and never fetches them.

**Credit.** Data published by BetterGov.ph, compiled from the DPWH Transparency Portal.

## DPWH transparency API

The original source of the data. The team built a polite extractor (`python -m src.cli extract`) and checked `robots.txt` and the portal's terms before any request. Cloudflare bot protection returned HTTP 403 on the first request to both `/ai/stats` and `/projects` (30 September 2026). The extractor stopped each time as designed, and automated collection from the API was ended. The extractor remains in the repository as tested code. The portal's summary figures were captured once by hand in a browser and are recorded in `docs/reconciliation_baseline.md`.

## PhilGEPS Open Data

The government procurement portal run by the Procurement Service of the Department of Budget and Management. Its announcement describes the data as downloadable and machine-readable for the general public, but the Terms and Conditions link on the portal returned HTTP 404 when checked on 5 October 2026. Before admission the team must confirm that a manual download exists, record any terms shown, and check whether records carry DPWH contract IDs or must be matched on contractor, amount, and date.

## Reviewed and not used

| Source | Reason |
| --- | --- |
| `csiiiv/dpwh-infra-data-scraper` and related scraper repositories | Tools rather than published datasets, and they rely on browser fingerprint impersonation |
| `gabconcepcionph/dpwh-contracts-dashboard` | No licence stated |
| Kaggle "DPWH Flood Control Projects" | Flood control subset only; licence and provenance not confirmed |
| DBM DIME (`dime.gov.ph`) | No downloadable data or API found |
