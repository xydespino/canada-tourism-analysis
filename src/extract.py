import requests
import io
import zipfile
from pathlib import Path

WDS_BASE = "https://www150.statcan.gc.ca/t1/wds/rest"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"


def get_table_url(table_id: str) -> str:
    """Ask the StatCan WDS API where the full-table CSV zip lives."""
    url = f"{WDS_BASE}/getFullTableDownloadCSV/{table_id}/en"

    # TODO 1: make the GET request (add a timeout, see below)
    response = requests.get(url, timeout=10)  # timeout in seconds
    # TODO 2: fail loudly if the HTTP request itself failed
    response.raise_for_status()
    # TODO 3: turn the response into a Python dictionary
    data = response.json()
    # TODO 4: if the "status" isn't "SUCCESS", raise an error with a clear message
    if data.get("status") != "SUCCESS":
        raise ValueError(f"Failed to retrieve table URL for {table_id}: {data}")
    # TODO 5: return the zip URL
    return data["object"]

def download_and_extract(zip_url: str, dest_dir: Path) -> list[Path]:
    """Download a zip file and extract its contents into dest_dir."""
    # TODO 1: make sure dest_dir exists (create it if not)
    dest_dir.mkdir(parents=True, exist_ok=True)
    # TODO 2: download the zip (longer timeout, and check for HTTP errors)
    response = requests.get(zip_url, timeout=30)  # longer timeout for larger files
    response.raise_for_status()
    # TODO 3: wrap the bytes so zipfile can read them, then open the zip
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        # TODO 4: extract everything into dest_dir
        zf.extractall(dest_dir)
        # TODO 5: return the paths of the extracted files
        return [dest_dir / name for name in zf.namelist()]

    
if __name__ == "__main__":
    zip_url = get_table_url("24100051")
    files = download_and_extract(zip_url, RAW_DIR)
    print(files)