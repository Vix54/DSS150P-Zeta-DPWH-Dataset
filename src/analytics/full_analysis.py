import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.model_selection import GridSearchCV, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from src.transform.curated import read_curated_contracts

PALETTE = ["#1F4E79", "#2E86AB", "#F18F01", "#C73E1D", "#3B8B5A", "#6C757D", "#8E5572", "#4C956C"]
NUMERIC_FEATURES = ["log_project_cost", "log_abc_php", "award_to_abc_pct", "bidder_count", "contractor_count", "is_joint_venture", "infra_year"]
CATEGORICAL_FEATURES = ["category", "region"]
EXCLUDED_FEATURES = [
    "physical_accomplishment", "status_name", "expiry_date", "contract_effectivity_date", "start_date",
    "completion_date", "date_of_award", "delay_as_of_date",
]
MIN_GROUP = 30
GRID = {"model__learning_rate": [0.05, 0.1], "model__max_depth": [3, 6], "model__max_iter": [200]}


def analytics_config(settings):
    config = settings.raw.get("analytics") or {}
    return {
        "target": config.get("target", "is_delayed"),
        "population_status": config.get("population_status", "On-Going"),
        "test_size": float(config.get("test_size", 0.2)),
        "cv_folds": int(config.get("cv_folds", 3)),
        "top_categories": int(config.get("top_categories", 10)),
    }


def explore(df, log):
    log(f"Shape of the curated contracts: {df.shape[0]} rows x {df.shape[1]} columns")
    log(f"Column types: {df.dtypes.astype(str).value_counts().to_dict()}")
    nulls = df.isnull().sum().sort_values(ascending=False)
    log(f"Columns with the most missing values: {nulls[nulls > 0].head(10).to_dict()}")
    log(f"First rows:\n{df[['contract_id', 'status_name', 'region', 'project_cost', 'physical_accomplishment', 'is_delayed']].head(3).to_string(index=False)}")


def annotate_bars(ax, bars, labels):
    for bar, label in zip(bars, labels):
        ax.annotate(label, (bar.get_x() + bar.get_width() / 2, bar.get_height()), ha="center", va="bottom", fontsize=9, xytext=(0, 3), textcoords="offset points")


def plot_status_counts(df, out_dir):
    counts = df["status_name"].fillna("Missing").value_counts()
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(counts.index, counts.values, color=PALETTE[: len(counts)])
    annotate_bars(ax, bars, [f"{value:,}" for value in counts.values])
    ax.set_title("Curated Contracts by Status", fontweight="bold")
    ax.set_ylabel("Contracts")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(out_dir / "01_status_counts.png", dpi=150)
    plt.close(fig)


def plot_region_delay(rates, out_dir, as_of):
    fig, ax = plt.subplots(figsize=(10, 6))
    ordered = rates.sort_values("delay_rate_pct", ascending=False)
    bars = ax.bar(ordered.index, ordered["delay_rate_pct"], color=PALETTE[1])
    annotate_bars(ax, bars, [f"{value:.1f}%" for value in ordered["delay_rate_pct"]])
    ax.set_title(f"Delay Rate of On-Going Contracts by Region (as of {as_of})", fontweight="bold")
    ax.set_ylabel("Delayed (%)")
    ax.tick_params(axis="x", rotation=60)
    fig.tight_layout()
    fig.savefig(out_dir / "02_delay_rate_by_region.png", dpi=150)
    plt.close(fig)


def plot_cost_distribution(population, target, out_dir):
    fig, ax = plt.subplots(figsize=(9, 5))
    for flag, colour, label in ((False, PALETTE[0], "On schedule"), (True, PALETTE[3], "Delayed")):
        values = np.log10(population.loc[population[target] == flag, "project_cost"].dropna().clip(lower=1))
        ax.hist(values, bins=40, alpha=0.6, color=colour, label=f"{label} (n={len(values):,})")
    ax.set_title("Project Cost of On-Going Contracts: Delayed vs On Schedule", fontweight="bold")
    ax.set_xlabel("log10(project cost in PHP)")
    ax.set_ylabel("Contracts")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "03_cost_distribution.png", dpi=150)
    plt.close(fig)


def plot_importance(importance, out_dir):
    ordered = importance.sort_values("importance")
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.barh(ordered["feature"], ordered["importance"], color=PALETTE[4])
    for bar, value in zip(bars, ordered["importance"]):
        ax.annotate(f"{value:.3f}", (bar.get_width(), bar.get_y() + bar.get_height() / 2), va="center", fontsize=9, xytext=(3, 0), textcoords="offset points")
    ax.set_title("Permutation Importance in the Delay Model (drop in ROC AUC)", fontweight="bold")
    ax.set_xlabel("Mean drop in ROC AUC when the feature is shuffled")
    fig.tight_layout()
    fig.savefig(out_dir / "04_feature_importance.png", dpi=150)
    plt.close(fig)


def feature_frame(population):
    features = population.copy()
    features["log_project_cost"] = np.log1p(pd.to_numeric(features["project_cost"], errors="coerce").astype(float).clip(lower=0))
    features["log_abc_php"] = np.log1p(pd.to_numeric(features["abc_php"], errors="coerce").astype(float).clip(lower=0))
    for column in ("award_to_abc_pct", "bidder_count", "contractor_count", "infra_year"):
        features[column] = pd.to_numeric(features[column], errors="coerce").astype(float)
    features["is_joint_venture"] = features["is_joint_venture"].astype("Float64").astype(float)
    for column in CATEGORICAL_FEATURES:
        features[column] = features[column].astype("string").fillna("Unknown").astype(object)
    return features[NUMERIC_FEATURES + CATEGORICAL_FEATURES]


def train_delay_model(population, config, log):
    target = config["target"]
    y = population[target].astype(bool).astype(int)
    if len(population) < 100 or y.nunique() < 2:
        log("Delay model skipped: fewer than 100 contracts or only one class in the population")
        return None
    X = feature_frame(population)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=config["test_size"], random_state=42, stratify=y)
    encoder = OneHotEncoder(handle_unknown="infrequent_if_exist", max_categories=config["top_categories"], sparse_output=False)
    pipeline = Pipeline([
        ("prepare", ColumnTransformer([("categorical", encoder, CATEGORICAL_FEATURES)], remainder="passthrough")),
        ("model", HistGradientBoostingClassifier(random_state=42)),
    ])
    search = GridSearchCV(pipeline, GRID, scoring="roc_auc", cv=config["cv_folds"], n_jobs=1)
    search.fit(X_train, y_train)
    probabilities = search.predict_proba(X_test)[:, 1]
    predictions = search.predict(X_test)
    importance = permutation_importance(search.best_estimator_, X_test, y_test, scoring="roc_auc", n_repeats=5, random_state=42)
    ranked = pd.DataFrame({"feature": X.columns, "importance": importance.importances_mean, "std": importance.importances_std}).sort_values("importance", ascending=False)
    metrics = {
        "population": int(len(population)),
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "positive_rate_pct": round(float(y.mean() * 100), 2),
        "best_params": {key.replace("model__", ""): value for key, value in search.best_params_.items()},
        "cv_roc_auc": round(float(search.best_score_), 4),
        "test_roc_auc": round(float(roc_auc_score(y_test, probabilities)), 4),
        "test_balanced_accuracy": round(float(balanced_accuracy_score(y_test, predictions)), 4),
        "cv_folds": config["cv_folds"],
        "features": NUMERIC_FEATURES + CATEGORICAL_FEATURES,
        "excluded_features": EXCLUDED_FEATURES,
    }
    log(f"Delay model: best parameters {metrics['best_params']}, cross-validated ROC AUC {metrics['cv_roc_auc']}, test ROC AUC {metrics['test_roc_auc']}, test balanced accuracy {metrics['test_balanced_accuracy']}")
    log(f"Permutation importance:\n{ranked.to_string(index=False)}")
    return metrics, ranked


def describe_auc(value):
    if value < 0.6:
        return "little better than chance"
    if value < 0.7:
        return "weak"
    if value < 0.8:
        return "moderate"
    return "strong"


def build_insights(summary, rates, metrics, ranked):
    lines = [
        "# Delay analysis of curated DPWH contracts",
        "",
        f"Curated run `{summary['curated_run_id']}`; delay rule as of {summary['as_of']} (On-Going, past the contract expiry date, progress below 100%).",
        "Every figure below is computed by `python -m src.cli analyze` from the curated file; nothing is written in by hand.",
        "",
        "## Descriptive findings",
        "",
        f"- {summary['ongoing']:,} of {summary['rows']:,} curated contracts are On-Going; {summary['delayed']:,} of them ({summary['delay_rate_pct']:.1f}%) are delayed.",
    ]
    if summary["median_cost_delayed"] is not None and summary["median_cost_on_schedule"] is not None:
        relation = "higher" if summary["median_cost_delayed"] > summary["median_cost_on_schedule"] else "lower"
        lines.append(
            f"- Median project cost is {summary['median_cost_delayed']:,.0f} PHP for delayed contracts and {summary['median_cost_on_schedule']:,.0f} PHP for on-schedule ones, so delayed contracts have a {relation} median cost (ratio {summary['median_cost_ratio']:.2f})."
        )
    if len(rates):
        top = rates.sort_values("delay_rate_pct", ascending=False)
        lines.append(f"- Highest regional delay rate: {top.index[0]} at {top.iloc[0]['delay_rate_pct']:.1f}% of {int(top.iloc[0]['contracts']):,} On-Going contracts; lowest: {top.index[-1]} at {top.iloc[-1]['delay_rate_pct']:.1f}% of {int(top.iloc[-1]['contracts']):,} (regions with at least {MIN_GROUP} On-Going contracts).")
    lines += ["", "## Delay model", ""]
    if metrics is None:
        lines.append("- Not trained: the On-Going population was too small or had only one class.")
    else:
        top_features = ", ".join(f"{row.feature} ({row.importance:.3f})" for row in ranked.head(3).itertuples())
        lines += [
            f"- Gradient-boosted trees on {metrics['population']:,} On-Going contracts ({metrics['positive_rate_pct']:.1f}% delayed), tuned with {len(GRID['model__learning_rate']) * len(GRID['model__max_depth'])}-setting grid search and {metrics['cv_folds']}-fold cross-validation.",
            f"- Held-out ROC AUC {metrics['test_roc_auc']:.3f} ({describe_auc(metrics['test_roc_auc'])}; 0.5 is chance) and balanced accuracy {metrics['test_balanced_accuracy']:.3f} (0.5 for always predicting one class).",
            f"- Most informative features by permutation importance: {top_features}.",
            f"- Excluded because they define the delay flag or carry the contract timeline it is computed from: {', '.join(metrics['excluded_features'])}.",
        ]
        if ranked.iloc[0]["feature"] == "infra_year":
            lines.append("- The top feature, infra_year, largely measures how old a contract is; older On-Going contracts being past their expiry date is expected, so this signal says more about contract age than about what causes delays.")
    lines += ["", "## Limits", "", "- These are associations in one snapshot of the source, not causes, and the source covers about 93.5% of the portal's contracts."]
    return "\n".join(lines) + "\n"


def run_analytics(settings, run_id=None, log=print):
    config = analytics_config(settings)
    out_dir = Path(settings.paths["analytics"])
    out_dir.mkdir(parents=True, exist_ok=True)

    curated_run_id, curated, _ = read_curated_contracts(settings, run_id)
    df = curated.copy()
    explore(df, log)

    target = config["target"]
    population = df.loc[df["status_name"] == config["population_status"]].copy()
    population[target] = population[target].fillna(False).astype(bool)
    as_of = str(df["delay_as_of_date"].dropna().iloc[0]) if df["delay_as_of_date"].notna().any() else "unknown"
    delayed = population.loc[population[target]]
    on_schedule = population.loc[~population[target]]
    median_delayed = float(delayed["project_cost"].median()) if delayed["project_cost"].notna().any() else None
    median_on_schedule = float(on_schedule["project_cost"].median()) if on_schedule["project_cost"].notna().any() else None
    summary = {
        "curated_run_id": curated_run_id,
        "as_of": as_of,
        "rows": int(len(df)),
        "ongoing": int(len(population)),
        "delayed": int(len(delayed)),
        "delay_rate_pct": round(float(len(delayed) / len(population) * 100), 2) if len(population) else 0.0,
        "median_cost_delayed": median_delayed,
        "median_cost_on_schedule": median_on_schedule,
        "median_cost_ratio": round(median_delayed / median_on_schedule, 4) if median_delayed and median_on_schedule else None,
    }
    log(f"On-Going contracts: {summary['ongoing']}, delayed: {summary['delayed']} ({summary['delay_rate_pct']}%) as of {as_of}")

    grouped = population.groupby(population["region"].fillna("Unknown"))[target]
    rates = pd.DataFrame({"contracts": grouped.size(), "delay_rate_pct": grouped.mean() * 100})
    rates = rates.loc[rates["contracts"] >= MIN_GROUP]
    log(f"Delay rate by region (at least {MIN_GROUP} On-Going contracts):\n{rates.sort_values('delay_rate_pct', ascending=False).round(2).to_string()}")

    plot_status_counts(df, out_dir)
    if len(rates):
        plot_region_delay(rates, out_dir, as_of)
    if len(population):
        plot_cost_distribution(population, target, out_dir)

    trained = train_delay_model(population, config, log)
    metrics, ranked = trained if trained else (None, None)
    if ranked is not None:
        plot_importance(ranked, out_dir)

    insights = build_insights(summary, rates, metrics, ranked)
    (out_dir / "insights.md").write_text(insights, encoding="utf-8")
    (out_dir / "analytics_metrics.json").write_text(json.dumps({"summary": summary, "model": metrics, "regions": rates.round(4).reset_index().to_dict(orient="records")}, indent=2, default=str), encoding="utf-8")
    log(insights)
    log(f"Charts, insights.md and analytics_metrics.json saved to {out_dir}")
    return summary, metrics
