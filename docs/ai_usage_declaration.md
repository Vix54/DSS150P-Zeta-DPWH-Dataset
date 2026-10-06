# **AI Usage Declaration**

**Risma, Vincenzo Luis S.**

**DSS150P (Group Zeta)**

**Focus Area: Data Acquisition, Staging and Curated Modules | Evaluation Period: 30 September – 6 October 2026**

This laboratory component used Artificial Intelligence to enhance, structure, and validate the engineering lifecycle of the pipeline's acquisition, staging, and curated layers. The AI functioned as a supplementary technical consultant and drafting assistant, parsing architectural constraints and synthesizing modular components that required careful alignment with project requirements. Moreover, the integration of these tools did not circumvent foundational learning outcomes; rather, the module enforced a rigorous standard of manual verification, systems engineering, and data integrity that continuously engaged my analytical capabilities.

## **Tools and Workflow**

Specifically, Claude (Anthropic) was used as an assistant that discussed design patterns, drafted code and documentation, ran tests on its drafts within its own sandboxed workspace, and performed web research on candidate data sources. It was never given write access to the project repository; every change reached the repository only through my own actions, ensuring continuous developer oversight and explicit version control provenance.

Considering that, a structured and compartmentalized workflow governed every interaction:

1. **Parameter Isolation:** System requirements and interface contracts were delineated and segmented into modular problem spaces prior to model consultation.
2. **Draft Generation:** The AI produced code, configuration, tests, and documentation within its sandbox, tested them against synthetic fixtures, and delivered them externally as archive bundles.
3. **Manual Validation and Execution:** Every proposed artifact was reviewed before integration, copied manually into the local workspace, and executed within Windows Subsystem for Linux (WSL) and containerized Docker environments.
4. **Architectural Enforcement:** Each artifact was validated against the real dataset before being committed. Where real-data runs exposed defects, I reported the results, decided how they should be handled, and verified the AI-drafted corrections before committing them to Git.

## **Delegated AI Contributions**

The AI assistant was leveraged to draft preliminary scaffolding, explore system behaviors, and accelerate documentation across distinct project milestones:

* **Environment Architecture (Milestone 1):** Drafted the initial containerization and environment management files, including the Dockerfile, docker-compose.yml, configuration schemas (config/settings.yml, .env.example), the configuration loader (src/config.py), automated tests, and the validate-env diagnostic utility.
* **Network and Endpoint Investigation:** Analyzed the network traces, page source captures, and DevTools session logs I supplied to characterize the client-side rendered portal, interpreted its public robots.txt files, and identified the public API endpoints its front end calls.
* **Polite Extractor Scaffolding:** Drafted the baseline network ingestion code (http\_client.py, raw\_store.py, dpwh\_projects.py), incorporating robots.txt compliance, 1.5-second request pacing, an identifying User-Agent, bounded retries with backoff, immediate stops on HTTP 401/403 or bot-mitigation challenges, and resume capability backed by raw checksum verification.
* **Ethical and Regulatory Guardrails:** Declined every method designed to circumvent the portal's Cloudflare protection (browser automation, TLS fingerprint impersonation, rotating proxies, or forged headers), and declined to build on an unverified third-party dataset that had been collected with such techniques.
* **Source Research and Ingestion:** Identified alternative open-data distributions and verified the licence, revision, and SHA-256 hashes of the BetterGov release against my local files. Notably, the AI initially missed, and later reported, that the release's own README states it was collected from the DPWH API by a third-party scraper using browser fingerprint impersonation; the dataset is used as a published CC0 release, its collection method is disclosed in docs/sources.md, and it was not collected by the team. The AI also identified the Philippine Statistics Authority's Philippine Standard Geographic Code (PSGC, CC BY 4.0) as an official reference dataset, drafted the file-based ingestion step (file\_source.py, extract-file) including support for checksums recorded at download when a publisher does not publish them, and drafted the source register (docs/sources.md).
* **Profiling and Staging Logic:** Wrote the profiling scripts I ran and drafted the staging transformation code (staging.py, cleaning.py, contractors.py), the staging data contract (docs/data\_contract.md), unit tests, and corrections after the real-data runs.
* **Curated Layer and Integration:** Reviewed the team's existing transformation code against the specification, outlined how to move it from the CSV to the BetterGov raw layer, and drafted the curated layer (curated.py) with its curate command: matching each region against the PSGC without dropping rows, award metrics, a delay flag measured against the source snapshot date, a deterministic record\_hash, and a curated quarantine. It also drafted the merge steps, the curated section of the data contract, and a rewritten src/transform/README.md.
* **Specification and Acceptance Alignment:** Mapped the work against the specification's Section 7 acceptance protocol and drafted the extract\_sources(run\_id) entry point, run identifier support (PIPELINE\_RUN\_ID), the pipeline\_run\_id lineage column, the evidence-capture commands, and the teammate handoff notes (docs/handoff.md).
* **Version Control Support:** Suggested branch plans, command sequences, and commit messages in the team's format.

## **AI Errors Identified and Corrected**

Hence, AI output was treated as provisional. Errors found during validation included the missed collection-method disclosure in the BetterGov README, documentation that understated the number of blocked API runs (two instead of four), incorrect counts in the first data contract revision (74 instead of 34 repeated-member contracts; placeholder dates described as limited to 9 rows instead of 446 and 186), a delay rule first based on completion\_date, which is empty for every On-Going contract (corrected to the contract expiry date after a real-data check, which changed the count from 0 to 23,769), and a configuration check that made the database connection test depend on the scraper contact variable. Each was surfaced by real-data runs or evidence review. All but the last were corrected in the final commits; the contact-variable dependency remains in the code and was satisfied by restoring the value in the local, untracked .env file.

## **Developer Oversight, Execution, and Governance**

Every material implementation choice, execution step, and quality gate remained under my direction and manual control:

* **Local and Containerized Execution:** I ran all executions across WSL and Docker. Notably, this included the four live extractor runs on 30 September 2026 (each stopping on its first data request after receiving HTTP 403), the profiling passes, full clean-room rebuilds of the raw and staging layers from the local source file, the merge that replaced the CSV-based staging on main, and the curated runs and rebuild.
* **Empirical Evidence and Audit Baselines:** Captured DevTools network sessions, recorded the portal's public summary figures (/ai/stats) by hand in a normal browser session for reconciliation, retrieved the published file hashes from Hugging Face, checked the PhilGEPS terms page (which returned HTTP 404), downloaded the PSGC publication in a standard browser and recorded its SHA-256 at download, and compiled the evidence suite of nineteen transcripts (docs/evidence/01–19).
* **Architectural and Engineering Decisions:**
  * Committed to honest, non-evasive data collection after the upstream API block.
  * Designated the BetterGov release as the primary raw data source on the condition that its original collection method is disclosed.
  * Established the rule that supplementary sources must be official and may only add fields or flag disagreements, never overwrite primary values, and added the PSA PSGC under that rule as the official reference for validating geography.
  * Selected \_all\_details as the primary dataset, restricted quarantine to structurally unusable records, approved parsing of the alternate date format, and mapped the 1900-01-01 placeholder dates to null values with traceable warnings.
  * Replaced the team's CSV-based staging with the BetterGov staging layer, kept the team's column names so the database load keeps working, limited the PSGC to flagging regions, approved basing the delay flag on the contract expiry date, and accepted quarantining out-of-range progress values in the curated layer.
* **Institutional Escalation:** Disclosed the collection method of a third-party scraped CSV to the course professor, who approved keeping it as supplemental material only. An interim team staging step read it; that step was replaced by the BetterGov staging layer on main (v0.3.0), so the CSV is not used in any current pipeline layer, comparison, or correction.
* **Version Control and Integration:** Executed every Git commit, branch, merge, and push according to team formatting standards, managed the semantic milestone tags (v0.1.0, v0.2.0, v0.2.1, v0.2.2, v0.3.0), and validated every AI-drafted change against the real data before committing.

## **Handoff Verification and State**

At the handoff boundary for the acquisition, staging, and curated modules, the repository reflects a verified and tested state:

* **Raw Data Integrity:** The raw layer contains the checksum-validated BetterGov dataset of 248,421 records with a unique contractId, rebuilt from scratch in a clean-room run that reproduced the identical SHA-256 hash, and the official PSA PSGC reference (as of 30 June 2026) as a second source in a second format.
* **Branch and Tag Consistency:** The work is on main at v0.3.0. Staging processes all 248,421 records with zero quarantine rejections. The curated layer holds 248,418 records, quarantines 3 with out-of-range progress, matches 248,121 contracts to an official PSGC region, and flags 23,769 contracts as delayed as of 22 January 2026.
* **Automated Test Coverage:** 92 automated unit tests pass on main. Environment validation passes locally, including the new openpyxl pin (21 passed, 1 skipped without the database check). The earlier Docker validation (20 passed, 1 expected skip) predates that pin, so the image must be rebuilt to include it.
* **Downstream Integration:** The curated output, data contract, source register, handoff notes, and evidence transcripts have been handed over to support the team's database loading, validation, benchmarking, and Airflow orchestration work.
