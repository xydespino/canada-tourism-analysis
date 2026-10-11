"""Transform stage: turn raw StatCan files into a validated star schema."""

import re
import pandas as pd
from io import StringIO


from config import PROCESSED_DIR, RAW_DIR, TABLE_ID

# GEO member ID for Canada (the national total); 2 to 5 are the provinces
CANADA_GEO_ID = 1
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


def build_dim_geo(members: pd.DataFrame) -> pd.DataFrame:
    """Geography dimension: Canada and the four provincial groupings."""
    return (
        members[members["dimension_id"] == 1]            # dimension 1 = Geography
        .rename(columns={"member_id": "geo_id", "name": "geography"})
        [["geo_id", "geography"]]
        .reset_index(drop=True)
    )


def add_balancing(data: pd.DataFrame, dim_country: pd.DataFrame) -> pd.DataFrame:
    """Return leaf rows plus balancing rows for one slice of data.

    Works at any grain, because a member counts as a leaf only if none of
    its children have data in the same month and geography.
    """
    keys = ["date", "geo_id", "parent_id"]
    cols = ["date", "geo_id", "member_id", "visitors"]

    # 1. Drop "not available" rows, so they can't make a GEO look deeper than it is
    data = data.dropna(subset=["visitors"])

    # Parent links for real members only. Dropping the root (no parent) and
    # casting to plain int makes the IDs match member_id's type for joins
    links = (
        dim_country.loc[~dim_country["is_balancing"], ["member_id", "parent_id"]]
        .dropna(subset=["parent_id"])
        .astype({"parent_id": "int64"})
    )

    # 2. Attach each row's parent, then add up the children under each parent
    children = (
        data.merge(links, on="member_id")                 # inner join: root drops out
        .groupby(keys, as_index=False)["visitors"].sum()
        .rename(columns={"visitors": "children_visitors"})
    )

    # The parent's own published value, renamed so it lines up with those sums
    parent_values = data.rename(
        columns={"member_id": "parent_id", "visitors": "parent_visitors"}
    )[keys + ["parent_visitors"]]

    # 3. Balancing row = published parent minus the sum of its children
    balancing = children.merge(parent_values, on=keys)
    balancing["visitors"] = balancing["parent_visitors"] - balancing["children_visitors"]
    balancing["member_id"] = balancing["parent_id"] + BALANCING_OFFSET

    # 4. Leaves = rows that are NOT a parent of anything present (an anti-join).
    # indicator=True adds a "_merge" column saying where each row was found
    parents_present = children[keys].rename(columns={"parent_id": "member_id"})
    flagged = data.merge(
        parents_present, on=["date", "geo_id", "member_id"], how="left", indicator=True
    )
    leaves = flagged[flagged["_merge"] == "left_only"]   # no match = not a parent = leaf

    # Stack real leaves and balancing rows into one table
    return (
        pd.concat([leaves[cols], balancing[cols]], ignore_index=True)
        .sort_values(["date", "geo_id", "member_id"])
        .reset_index(drop=True)
    )


def build_facts(data: pd.DataFrame, dim_country: pd.DataFrame):
    """Split the data into its two grains and balance each one."""
    national = data[data["geo_id"] == CANADA_GEO_ID]
    provincial = data[data["geo_id"] != CANADA_GEO_ID]

    # National grain: GEO is always Canada, so the column adds nothing; drop it
    fact_country = add_balancing(national, dim_country).drop(columns="geo_id")

    # Provincial grain: keep geo_id to link to dim_geo
    fact_province = add_balancing(provincial, dim_country)

    return fact_country, fact_province


def validate(data: pd.DataFrame, fact_country: pd.DataFrame,
             fact_province: pd.DataFrame, tolerance: float = 0.5) -> None:
    """Stop the pipeline if leaves don't add up to the published totals,
    and report any negative balancing values."""

    # Published totals = the root member ("All visitors"), wherever it has a value
    published = data[(data["member_id"] == ROOT_ID) & data["visitors"].notna()]

    # --- Check 1: national leaves sum to the national total, every month ---
    national_sum = fact_country.groupby("date")["visitors"].sum()
    national_pub = published[published["geo_id"] == CANADA_GEO_ID].set_index("date")["visitors"]

    # fill_value=0: a month missing from either side shows up as a big
    # difference instead of being silently skipped
    national_diff = national_sum.sub(national_pub, fill_value=0).abs()
    if national_diff.max() > tolerance:
        raise ValueError(f"National leaves don't match totals:\n{national_diff[national_diff > tolerance]}")

    # --- Check 2: the same thing for every province, every month ---
    keys = ["date", "geo_id"]
    province_sum = fact_province.groupby(keys)["visitors"].sum()
    province_pub = published[published["geo_id"] != CANADA_GEO_ID].set_index(keys)["visitors"]

    province_diff = province_sum.sub(province_pub, fill_value=0).abs()
    if province_diff.max() > tolerance:
        raise ValueError(f"Provincial leaves don't match totals:\n{province_diff[province_diff > tolerance]}")

    print(f"Totals reconcile: {len(national_sum)} months national, "
          f"{len(province_sum)} month-province pairs")

    # --- Check 3: report negative balancing values (don't change them) ---
    # Stack both facts; the national one gets geo_id back just for reporting
    all_facts = pd.concat([fact_country.assign(geo_id=CANADA_GEO_ID), fact_province])
    negatives = all_facts[
        (all_facts["member_id"] > BALANCING_OFFSET) & (all_facts["visitors"] < 0)
    ]

    if negatives.empty:
        print("No negative balancing values")
    else:
        print(f"WARNING: {len(negatives)} negative balancing values. Most negative:")
        print(negatives.nsmallest(5, "visitors").to_string(index=False))


def save(tables: dict[str, pd.DataFrame]) -> None:
    """Write each table to data/processed/ as a CSV for Power BI."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)   # same idempotent pattern as extract
    for name, table in tables.items():
        path = PROCESSED_DIR / f"{name}.csv"
        table.to_csv(path, index=False)                # index=False: don't write pandas' row numbers
        print(f"Saved {name}: {len(table):,} rows")

def main() -> None:
    """Run the whole Transform stage: build, validate, then save."""
    data = load_data()
    members = load_members()

    dim_country = build_dim_country(members)
    dim_geo = build_dim_geo(members)
    fact_country, fact_province = build_facts(data, dim_country)

    # Validate BEFORE saving: bad data should never reach data/processed
    validate(data, fact_country, fact_province)

    save({
        "dim_country": dim_country,
        "dim_geo": dim_geo,
        "fact_country": fact_country,
        "fact_province": fact_province,
    })


if __name__ == "__main__":
    main()