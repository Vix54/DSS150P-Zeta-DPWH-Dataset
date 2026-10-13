# Source register

Every dataset that enters the pipeline is listed here with its provenance, licence, and collection method. A source is admitted only when it passes the admission check below.

## Admission check

A source may enter the raw layer only if all of the following hold:

1. **Known publisher.** A named organisation or government agency published it.
2. **Reuse is permitted.** An explicit licence or published terms allow reuse. Missing terms are recorded and the decision is made by the team, not assumed.
3. **Obtained through the publisher's own channel.** Downloaded with the publisher's button, export, or documented API, without getting around any access control or bot protection.
4. **Verifiable.** A SHA-256 checksum is recorded in `config/settings.yml` and checked on every ingestion; a mismatch stops the run. When the publisher publishes checksums, the published value is used (`checksum_origin: published`); otherwise the value is recorded at download (`checksum_origin: recorded_at_download`) and the run metadata says so.
5. **Method disclosed.** How the publisher collected the data is stated here, including methods the team would not use itself.

Sources added after the primary source must be official (government-published) and are used to add fields and flag disagreements. They never overwrite primary values; conflicts are recorded for validation.

**Automated retrieval.** A source is retrieved automatically only through a channel the publisher provides for programs: a pinned release URL or a documented API. The retriever reads robots.txt first, identifies itself with a user-agent and the team's contact address from `.env`, keeps to the publisher's rate limit, and stops on HTTP 401, 403 or any bot-protection challenge. It saves nothing unless the SHA-256 matches the published or recorded value.

## Register

| Source | Role | Status | Retrieval |
| --- | --- | --- | --- |
| BetterGov.ph DPWH Infrastructure Transparency Dataset (Hugging Face) | Primary | Admitted | Automated download of the pinned revision |
| PSA Philippine Standard Geographic Code (PSGC) | Official reference for validating geography | Admitted | Manual browser download |
| PSA OpenSTAT population by region | Official population for per-capita comparison by region | Admitted | Automated, documented API |
| DPWH transparency API (`api.transparency.dpwh.gov.ph`) | Original source | Not collected: blocked automated access | None |
| PhilGEPS Open Data (`open.philgeps.gov.ph`) | Official cross-check | Pending: download availability and terms not yet confirmed | None |

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
| Retrieval | `extract-file --download` fetches the file from the pinned revision URL in `config/settings.yml` and saves it only if its SHA-256 equals the published value; a copy downloaded by hand is checked the same way |
| Coverage | 93.5% of the 265,582 projects the portal reported on 30 September 2026 (see `docs/reconciliation_baseline.md`) |

**Collection method (as stated by the publisher).** The dataset README states that the data was collected from the DPWH transparency API (`/projects`) with a third-party scraper (`csiiiv/dpwh-transparency-data-api-scraper`) that uses curl-cffi with browser fingerprint impersonation, concurrent requests, and a rate of about 300 requests per 10 minutes. Group Zeta did not collect this data and does not use that method; the team's own extractor identifies itself and stops on any bot-protection challenge. The dataset is used as a published third-party release under its CC0 licence.

**Why `_all_details` and not the smaller file.** The same revision also contains `dpwh_transparency_data.parquet` (248,220 rows, SHA-256 `5b411cf3f112fabd1913c70681791e5e2b78b43a8393f489f48bd882f154e123`). Every one of its contract IDs appears in `_all_details`, which adds 201 contracts and procurement fields (approved budget for the contract, award amount, bidders, bid dates, components, document links).

**Known limitations.**

- Snapshot age: the release reflects the portal around January 2026. Status counts differ from the September 2026 baseline in the expected direction (more completed, fewer ongoing, many new projects in procurement).
- The dataset card describes only the smaller file and lists a size category of 1K to 10K rows and 22 fields; both are out of date.
- `amountPaid` is zero in every row.
- Document links point to `dcs.infrawatch.ph`; the pipeline stores them as text and never fetches them.

**Credit.** Data published by BetterGov.ph, compiled from the DPWH Transparency Portal.

## PSA Philippine Standard Geographic Code (PSGC)

| Item | Value |
| --- | --- |
| Publisher | Philippine Statistics Authority (PSA) |
| Dataset page | https://psa.gov.ph/classification/psgc |
| File used | `PSGC-2Q-2026-Publication-Datafile.xlsx` |
| Revision | PSGC as of 30 June 2026 (2Q 2026 publication, released 13 July 2026) |
| Licence | CC BY 4.0 ("All data and content on this website are licensed under the Creative Commons Attribution 4.0 International (CC BY 4.0) License, unless otherwise stated") |
| SHA-256 | Recorded at download in `config/settings.yml`; the PSA does not publish checksums |
| Format | Excel workbook, a second raw format alongside the Parquet primary source |

**Why it is used.** The PSGC is the government's official classification of regions, provinces, cities, municipalities and barangays. It is a trusted reference for validating the primary source's geography: checking `region` values against the official list (the PSGC counts 18 regions, while the portal's page text says 17 and its summary reports 19), and mapping implementing offices such as "Quezon 2nd DEO" to their provinces. Following the rule for supplementary sources, it is used only to flag disagreements, never to overwrite values in the primary source.

**Collection method.** Downloaded manually by a team member from the PSA website in a standard browser, as the publisher intends. It stays a manual step: the PSA offers the workbook as a website download, not through a documented programmatic channel, so `extract-file --download` leaves it to the team and checks the recorded SHA-256.

**Credit.** Philippine Standard Geographic Code (PSGC) as of 30 June 2026, Philippine Statistics Authority, licensed under CC BY 4.0.

## PSA OpenSTAT population by region

| Item | Value |
| --- | --- |
| Publisher | Philippine Statistics Authority (PSA) |
| Platform | OpenSTAT, the PSA's open data platform (https://openstat.psa.gov.ph/About) |
| Table | 2024 Census of Population, total population by region (table 0191A6DTHP8) (`DB/1A/PO_2024/0191A6DTHP8.px`) |
| Selection | `Parameter` = `0` (Total Population) for every value of `Geographic Location` (137 rows: the Philippines, regions, provinces and highly urbanized cities). Only rows whose label matches one PSGC region are kept; the rest are counted as `population_labels_not_regions` in the curated report, and two rows matching the same region stop the run |
| Licence | CC BY 4.0. The terms (https://openstat.psa.gov.ph/Terms) allow copying, redistribution and adaptation with credit to the PSA, a link to the terms and an indication of changes |
| API | PX-Web API v1, documented at https://openstat.psa.gov.ph/API-Documentation: a GET of the table URL returns its metadata (codes and labels), and a POST with a JSON query returns the data in PX-Web JSON. The documentation allows at most 10 requests within 10 seconds per user; the pipeline spaces requests at least 1.5 seconds apart, so a run makes three (robots.txt, metadata, data). The table's JSON-stat 2.0 output is not used: on 14 October 2026 it returned a single value of 1 for any selection, while PX-Web JSON and CSV returned the published figures (for example NCR 14,001,751) |
| SHA-256 | Recorded at the first retrieval in `config/settings.yml` (`checksum_origin: recorded_at_download`); every later run must return the same bytes |
| Stored as | `data/raw/source=psa_openstat_population/run_id=<UTC timestamp>/population_by_region.json` and `table_metadata.json`, with the request in `run.json`. The recorded SHA-256 is that of the data response |
| Matching | The rows carry 10-digit PSGC codes (NCR is `1300000000`), so a row is a region exactly when its code is a PSGC region code; the Philippines total, provinces and cities drop out on their own |

**Why it is used.** It closes the source-diversity gap with a third official, licensed source that has a clear integration purpose. Contract counts by region mix the size of a region with how its projects perform. Joining population on the PSGC region code turns them into contracts and delayed contracts per 100,000 people, so regions of very different size can be compared. Following the rule for supplementary sources, it adds a field in a separate table (`region_population.parquet`) and never changes primary values or their `record_hash`.

**Collection method.** Retrieved by the pipeline itself through the publisher's documented API, as the publisher intends, after reading robots.txt and identifying itself with the team's contact address.

**Limitations.** The census reference date (1 July 2024) differs from the contract snapshot (January 2026), and per-capita counts do not account for project size, cost or need. A region with no population value is reported by `validate` as a warning, not hidden.

**Credit.** Source: Philippine Statistics Authority, 2024 Census of Population (POPCEN), OpenSTAT table 0191A6DTHP8, licensed under CC BY 4.0 (https://openstat.psa.gov.ph/Terms). Changes: regions selected and matched to PSGC codes by Group Zeta.

## Source diversity

The pipeline has three admitted sources from two publishers: one third-party release of the DPWH portal's data and two official PSA products. OpenSTAT is the third permitted source, with a documented integration purpose. The DPWH portal itself could not be used (blocked), and PhilGEPS is still pending, so the contract records come from a single release. Cross-checking contract values against a second independent record of the same contracts remains open.

## DPWH transparency API

The original source of the data. The team built a polite extractor (`python -m src.cli extract`) and checked `robots.txt` and the portal's terms before any request. Cloudflare bot protection returned HTTP 403 on the first data request of each of four runs on 30 September 2026 (one on `/ai/stats`, three on `/projects`; two requests per run including `robots.txt`). The extractor stopped each time as designed, and automated collection from the API was ended. The extractor remains in the repository as tested code. The portal's summary figures were captured once by hand in a browser and are recorded in `docs/reconciliation_baseline.md`.

## PhilGEPS Open Data

The government procurement portal run by the Procurement Service of the Department of Budget and Management. Its announcement describes the data as downloadable and machine-readable for the general public, but the Terms and Conditions link on the portal returned HTTP 404 when checked on 5 October 2026. Before admission the team must confirm that a manual download exists, record any terms shown, and check whether records carry DPWH contract IDs or must be matched on contractor, amount, and date.

## Reviewed and not used

| Source | Reason |
| --- | --- |
| `csiiiv/dpwh-infra-data-scraper` and related scraper repositories | Tools rather than published datasets, and they rely on browser fingerprint impersonation |
| `gabconcepcionph/dpwh-contracts-dashboard` | No licence stated |
| Kaggle "DPWH Flood Control Projects" | Flood control subset only; licence and provenance not confirmed |
| DBM DIME (`dime.gov.ph`) | No downloadable data or API found |
| NOAA NCEI GHCN-Daily precipitation (Climate Data Online API) | Built, tested and removed before v1.0.0: only 16 Philippine stations, covering 8 of 18 regions and thinning after 2021, so regional rainfall was too coarse to support a finding; NOAA's terms also do not allow results derived from non-U.S. station data to be passed on |
| DPWH website notices of award (`dpwh.gov.ph`) | Official and current, but answered with a bot-protection challenge (Imperva) when checked on 14 October 2026, so automated collection would mean getting around an access control |
