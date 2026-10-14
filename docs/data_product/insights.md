# Delay analysis of curated DPWH contracts

Curated run `20261014T050858Z`; delay rule as of 2026-01-22 (On-Going, past the contract expiry date, progress below 100%).
Every figure below is computed by `python -m src.cli analyze` from the curated file; nothing is written in by hand.

## Descriptive findings

- 34,728 of 248,418 curated contracts are On-Going; 23,769 of them (68.4%) are delayed.
- Median project cost is 24,474,917 PHP for delayed contracts and 24,699,999 PHP for on-schedule ones, so the two groups have about the same median cost (ratio 0.99).
- Highest regional delay rate: Region I at 80.2% of 3,039 On-Going contracts; lowest: Region X at 50.5% of 1,751 (regions with at least 30 On-Going contracts).

## Per-capita view

- Population from PSA OpenSTAT (Parameter: Total Population), joined to the contracts on the PSGC region code for 18 regions.
- Most delayed On-Going contracts per 100,000 people: Cordillera Administrative Region (CAR) at 60.09 (1,087 delayed, population 1,808,985); fewest: Region XII (SOCCSKSARGEN) at 8.47 (378 delayed, population 4,462,776), among regions with contracts in the source.
- No contracts in the source for: Bangsamoro Autonomous Region In Muslim Mindanao (BARMM). These regions are left out of the comparison rather than counted as zero delays.
- Per-capita counts compare regions of different size; they do not account for project size, cost or need, so a higher value is not a judgement on a region.

## Exploratory delay classification

- Exploratory classification within one snapshot (as of 2026-01-22): it shows which recorded attributes separate delayed from on-schedule contracts in this snapshot. It is not a forecast of future delays and does not establish causes; that would need dated snapshots and time-based validation.
- HistGradientBoostingClassifier (gradient-boosted trees) on 34,728 On-Going contracts (68.4% delayed), tuned with 4-setting grid search and 3-fold cross-validation.
- Held-out ROC AUC 0.701 (moderate; 0.5 is chance) and balanced accuracy 0.600 (0.5 for always predicting one class).
- Most informative features by permutation importance: log_abc_php (0.095), infra_year (0.080), region (0.074).
- Excluded because they define the delay flag or carry the contract timeline it is computed from: physical_accomplishment, status_name, expiry_date, contract_effectivity_date, start_date, completion_date, date_of_award, delay_as_of_date.

## Limits

- These are associations in one snapshot of the source, not causes, and the source covers about 93.5% of the portal's contracts.
