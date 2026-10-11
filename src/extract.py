"""Extract stage: download a StatCan table via the WDS API and unzip it into data/raw."""

# Standard library imports first...
import io
import zipfile
from pathlib import Path

# ...then third-party packages (these need installing)...
import requests

# ...then our own project modules.
# Shared settings live in config.py (same folder, so a plain import works)
from config import RAW_DIR, TABLE_ID

# Base address of StatCan's Web Data Service (WDS) REST API
WDS_BASE = "https://www150.statcan.gc.ca/t1/wds/rest"


def get_table_url(table_id: str) -> str:
    """Ask the StatCan WDS API where the full-table CSV zip lives."""
    # Build the endpoint, e.g. .../getFullTableDownloadCSV/24100051/en
    url = f"{WDS_BASE}/getFullTableDownloadCSV/{table_id}/en"

    # Call the API. The timeout stops the script hanging forever if StatCan goes quiet
    response = requests.get(url, timeout=10)

    # Layer 1: did the delivery work? Raises on HTTP errors like 404 or 500
    response.raise_for_status()

    # Turn the JSON text into a Python dictionary
    data = response.json()

    # Layer 2: did StatCan say the operation succeeded?
    # .get() returns None if "status" is missing, which also counts as a failure
    if data.get("status") != "SUCCESS":
        raise ValueError(f"Failed to retrieve table URL for {table_id}: {data}")

    # The zip URL lives under "object". Square brackets (not .get) so a
    # missing key fails immediately instead of quietly returning None
    return data["object"]


def download_and_extract(zip_url: str, dest_dir: Path) -> list[Path]:
    """Download a zip file and extract its contents into dest_dir."""
    # Create the folder if needed. parents=True builds missing parent folders,
    # exist_ok=True means re-running doesn't crash (the step is idempotent)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Download the zip. The timeout covers gaps between chunks, not the total time,
    # so a large file that keeps streaming won't trip it
    response = requests.get(zip_url, timeout=30)

    # Catches a bad table ID here: the API happily returns a URL for any ID,
    # but downloading a file that doesn't exist gives a 404
    response.raise_for_status()

    # Report the size so scheduled runs leave a useful log line
    size_mb = len(response.content) / 1_000_000
    print(f"Downloaded {size_mb:.1f} MB")

    # BytesIO makes the downloaded bytes behave like a file, so zipfile can
    # read them without saving the zip to disk. The "with" block closes the
    # zip automatically, even if extraction fails partway through
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        # Only safe because we trust the source: a malicious zip could try
        # to write files outside dest_dir
        zf.extractall(dest_dir)

        # Full paths of everything extracted (the data CSV and the metadata CSV)
        return [dest_dir / name for name in zf.namelist()]


# Runs only when this file is executed directly (python src/extract.py),
# not when another script imports its functions
if __name__ == "__main__":
    zip_url = get_table_url(TABLE_ID)
    files = download_and_extract(zip_url, RAW_DIR)
    print(f"Extracted {len(files)} files:")
    for f in files:
        print(f"  {f.name}")  # .name shows just the file name, not the full path