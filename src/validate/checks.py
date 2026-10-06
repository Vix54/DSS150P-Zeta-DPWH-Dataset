import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def run_quality_checks(df: pd.DataFrame) -> bool:
    """Runs 5 automated data quality checks on the DPWH dataset."""
    logging.info("Running automated data quality checks...")
    
    # Check 1: Schema Enforcement (Check if expected columns exist)
    expected_columns = ['contract_id', 'project_cost', 'physical_accomplishment']
    missing_cols = [col for col in expected_columns if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Schema Check Failed: Missing essential columns: {missing_cols}")

    # Check 2: Nullability (Primary ID cannot be null)
    if df['contract_id'].isnull().any():
        raise ValueError("Nullability Check Failed: 'contract_id' contains null values.")

    # Check 3: Uniqueness (No duplicate primary keys)
    if df['contract_id'].duplicated().any():
        duplicates = df[df['contract_id'].duplicated()]['contract_id'].tolist()
        logging.warning(f"Uniqueness Check Warning: {len(duplicates)} duplicate contract_ids found. Dropping duplicates.")
        df.drop_duplicates(subset=['contract_id'], inplace=True)

    # Check 4: Data Type (Project cost must be numeric)
    if not pd.api.types.is_numeric_dtype(df['project_cost']):
        raise TypeError("Data Type Check Failed: 'project_cost' must be numeric.")

    # Check 5: Domain/Range (Physical accomplishment should be between 0 and 100)
    out_of_bounds = df[~df['physical_accomplishment'].between(0, 100, inclusive='both')]
    if not out_of_bounds.empty:
        logging.warning(f"Range Check Warning: 'physical_accomplishment' out of bounds for {len(out_of_bounds)} rows. Capping at 100.")
        df.loc[df['physical_accomplishment'] > 100, 'physical_accomplishment'] = 100
        df.loc[df['physical_accomplishment'] < 0, 'physical_accomplishment'] = 0

    logging.info("All 5 data quality checks executed successfully!")
    return True