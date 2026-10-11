"""Run the full pipeline: extract from StatCan, transform, validate, save."""

import extract
import transform

if __name__ == "__main__":
    print("== Extract ==")
    zip_url = extract.get_table_url(extract.TABLE_ID)
    extract.download_and_extract(zip_url, extract.RAW_DIR)

    print("\n== Transform ==")
    transform.main()