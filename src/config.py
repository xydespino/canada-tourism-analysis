# Shared settings for the whole pipeline.
# Every script imports from here, so a path only ever changes in one place.
from pathlib import Path

# The StatCan table we're working with (seasonally adjusted, monthly)
TABLE_ID = "24100051"

# Project root: config.py lives in src/, so go up one folder
PROJECT_ROOT = Path(__file__).resolve().parent.parent

RAW_DIR = PROJECT_ROOT / "data" / "raw"              # untouched downloads
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"  # clean files for Power BI