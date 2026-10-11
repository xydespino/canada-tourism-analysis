import requests

WDS_BASE = "https://www150.statcan.gc.ca/t1/wds/rest"


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



if __name__ == "__main__":
    print(get_table_url("24100051"))