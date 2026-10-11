import re
from io import StringIO

import pandas as pd

from config import PROCESSED_DIR, RAW_DIR, TABLE_ID

# The top-level member ("All visitors") has this ID in the country dimension
ROOT_ID = 1

# Balancing members get IDs well clear of StatCan's (which stop at 89):
# "Other Caribbean" = 1000 + Caribbean's ID
BALANCING_OFFSET = 1000

# StatCan's long names -> short names for the report.
# Only these five are wordy; every other member keeps its original name
SHORT_NAMES = {
    "Non-resident visitors entering Canada": "All visitors",
    "United States of America residents entering Canada": "United States",
    "Residents of countries other than the United States of America entering Canada": "Overseas",
    "Americas, countries other than the United States of America": "Americas (excl. US)",
    "North America, countries other than the United States of America": "North America (excl. US)",
}

def load_data(table_id: str = TABLE_ID) -> pd.DataFrame:
    """Load the main data CSV and add clean ID and date columns."""
    path = RAW_DIR / f"{table_id}.csv"

    # Keep COORDINATE as text so "1.10" isn't turned into the number 1.1
    df = pd.read_csv(path, dtype={"COORDINATE": str})

    # "1.10" -> geo_id 1, member_id 10
    df[["geo_id", "member_id"]] = (
        df["COORDINATE"].str.split(".", expand=True).astype(int)
    )

    # "2024-07" -> a real date (the 1st of that month).
    # Real dates let Power BI sort and do time intelligence properly
    df["date"] = pd.to_datetime(df["REF_DATE"], format="%Y-%m")

    # Keep only the columns we need, with consistent lowercase names
    return df.rename(columns={"VALUE": "visitors", "STATUS": "status"})[
        ["date", "geo_id", "member_id", "visitors", "status"]
    ]


def load_members(table_id: str = TABLE_ID) -> pd.DataFrame:
    """Parse the member table (the hierarchy) out of the multi-section metadata file."""
    path = RAW_DIR / f"{table_id}_MetaData.csv"
    text = path.read_text(encoding="utf-8-sig")

    # Sections are separated by blank lines
    sections = re.split(r"\n\s*\n", text)

    # Pick the section whose header matches the member table.
    # Raises StopIteration (fails loudly) if StatCan ever changes the format
    members_text = next(
        s for s in sections if s.startswith('"Dimension ID","Member Name"')
    )
    members = pd.read_csv(StringIO(members_text))

    # Short, consistent column names; Int64 (capital I) allows blanks,
    # needed because the top-level members have no parent
    return members.rename(
        columns={
            "Dimension ID": "dimension_id",
            "Member Name": "name",
            "Member ID": "member_id",
            "Parent Member ID": "parent_id",
        }
    )[["dimension_id", "member_id", "name", "parent_id"]].astype(
        {"parent_id": "Int64"}
    )

def build_dim_country(members: pd.DataFrame) -> pd.DataFrame:
    """Build the country dimension: every member plus balancing members,
    with the ragged hierarchy flattened into fixed columns."""

    # Dimension 2 is Country of residence (dimension 1 is Geography).
    # Both dimensions number their members from 1, so this filter is essential:
    # otherwise member 2 could mean Quebec OR the United States
    dim = members[members["dimension_id"] == 2].drop(columns="dimension_id").copy()

    # Swap the five long names for short ones; names not in the dict are untouched
    dim["name"] = dim["name"].replace(SHORT_NAMES)
    dim["is_balancing"] = False

    # --- Balancing members: one "Other X" for every member that has children ---
    parent_ids = dim["parent_id"].dropna().unique()
    parents = dim[dim["member_id"].isin(parent_ids)]

    balancing = pd.DataFrame({
        "member_id": parents["member_id"] + BALANCING_OFFSET,
        "name": "Other " + parents["name"],
        "parent_id": parents["member_id"],   # each sits directly under its parent
        "is_balancing": True,
    })
    # The root's residual is pure rounding (US + Overseas is off by about 1),
    # so give it an honest name instead of "Other All visitors"
    balancing.loc[balancing["parent_id"] == ROOT_ID, "name"] = "Unallocated (rounding)"

    # Stack real and balancing members into one table
    dim = pd.concat([dim, balancing], ignore_index=True)
    dim["parent_id"] = dim["parent_id"].astype("Int64")

    # --- Flatten the hierarchy ---
    # Two lookups: ID -> name, and ID -> parent ID
    name_of = dict(zip(dim["member_id"], dim["name"]))
    parent_of = dict(zip(dim["member_id"], dim["parent_id"]))

    def path_to_root(member_id: int) -> list[str]:
        """Follow parent links up to the root, then reverse: root first."""
        path = []
        current = member_id
        while pd.notna(current):          # the root's parent is <NA>, which ends the loop
            path.append(name_of[current])
            current = parent_of[current]
        return path[::-1]                 # [::-1] reverses a list

    def level(path: list[str], i: int) -> str:
        """Name at depth i; if the branch is shorter, repeat its deepest name (padding)."""
        return path[min(i, len(path) - 1)]

    paths = dim["member_id"].map(path_to_root)

    dim["depth"] = paths.map(len)                       # 1 = root, 5 = deepest countries
    dim["market"] = paths.map(lambda p: level(p, 1))    # United States / Overseas
    dim["continent"] = paths.map(lambda p: level(p, 2))
    dim["region"] = paths.map(lambda p: level(p, 3))
    dim["country"] = paths.map(lambda p: level(p, 4))

    return dim.sort_values("member_id").reset_index(drop=True)


if __name__ == "__main__":
    members = load_members()
    dim_country = build_dim_country(members)

    # Spot-check a few interesting members: the US, a deep country,
    # a region-leaf, and some balancing members
    check_ids = [2, 6, 8, 28, 1009, 1003, 1001]
    cols = ["member_id", "name", "depth", "market", "continent", "region", "country"]
    print(dim_country[dim_country["member_id"].isin(check_ids)][cols].to_string())
    print(f"\n{len(dim_country)} members in total")