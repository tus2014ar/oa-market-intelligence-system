"""Chunked data-quality profiling for the large public files.

Everything is read as text in chunks, so a 9 GB file never has to fit in memory and codes such as
`00123` keep their leading zeros. The profile reports structure and quality only: row counts, empty
values, distinct values of chosen columns, numeric ranges, date ranges and duplicate keys. It does
not compute any share, trend or ranking.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pandas as pd

DEFAULT_CHUNKSIZE = 500_000
TOP_VALUES = 25


def sha256_of(path: Path | str, block: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(block):
            digest.update(chunk)
    return digest.hexdigest()


def _read(handle_or_path, *, usecols, chunksize, keep, encoding) -> Iterator[pd.DataFrame]:
    reader = pd.read_csv(
        handle_or_path,
        dtype=str,
        keep_default_na=False,
        na_values=[],
        usecols=usecols,
        chunksize=chunksize,
        encoding=encoding,
        encoding_errors="replace",
        low_memory=False,
    )
    for chunk in reader:
        yield chunk[keep(chunk)] if keep is not None else chunk


def iter_csv(
    path: Path | str,
    *,
    usecols: list[str] | None = None,
    chunksize: int = DEFAULT_CHUNKSIZE,
    keep: Callable[[pd.DataFrame], pd.Series] | None = None,
    encoding: str = "utf-8",
) -> Iterator[pd.DataFrame]:
    """Yield a CSV in text-only chunks, optionally keeping some columns and some rows."""
    yield from _read(path, usecols=usecols, chunksize=chunksize, keep=keep, encoding=encoding)


def iter_zip_csv(
    zip_path: Path | str,
    *,
    member_contains: str,
    usecols: list[str] | None = None,
    chunksize: int = DEFAULT_CHUNKSIZE,
    keep: Callable[[pd.DataFrame], pd.Series] | None = None,
    encoding: str = "utf-8",
) -> Iterator[pd.DataFrame]:
    """Yield one CSV member of a zip in chunks, without extracting it to disk."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [
            n for n in archive.namelist() if member_contains in n and n.lower().endswith(".csv")
        ]
        if not names:
            raise FileNotFoundError(f"no csv containing {member_contains!r} in {zip_path}")
        with archive.open(names[0]) as handle:
            yield from _read(
                handle, usecols=usecols, chunksize=chunksize, keep=keep, encoding=encoding
            )


def _is_empty(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().eq("")


def profile_chunks(
    chunks: Iterable[pd.DataFrame],
    *,
    track: tuple[str, ...] = (),
    numeric: tuple[str, ...] = (),
    dates: tuple[str, ...] = (),
    key: tuple[str, ...] = (),
    max_distinct: int = 5000,
    date_format: str | None = None,
) -> dict:
    """Profile a stream of text chunks. Raises KeyError if a named column is not in the data."""
    n_rows = 0
    columns: list[str] | None = None
    empty: Counter = Counter()
    counters = {c: Counter() for c in track}
    capped = {c: False for c in track}
    blank = {"min": None, "max": None, "n_negative": 0, "n_zero": 0, "n_unparseable": 0}
    num = {c: dict(blank) for c in numeric}
    dat = {c: {"min": None, "max": None, "n_unparseable": 0} for c in dates}
    seen: set[int] = set()
    duplicates = 0

    for chunk in chunks:
        if columns is None:
            columns = list(chunk.columns)
            for name in (*track, *numeric, *dates, *key):
                if name not in columns:
                    raise KeyError(name)
        n_rows += len(chunk)
        for column in columns:
            empty[column] += int(_is_empty(chunk[column]).sum())
        for column in track:
            values = chunk[column][~_is_empty(chunk[column])]
            for value, count in values.value_counts().items():
                if value in counters[column] or len(counters[column]) < max_distinct:
                    counters[column][value] += int(count)
                else:
                    capped[column] = True
        for column in numeric:
            present = chunk[column][~_is_empty(chunk[column])]
            parsed = pd.to_numeric(present, errors="coerce")
            stats = num[column]
            stats["n_unparseable"] += int(parsed.isna().sum())
            parsed = parsed.dropna()
            if len(parsed):
                low, high = float(parsed.min()), float(parsed.max())
                stats["min"] = low if stats["min"] is None else min(stats["min"], low)
                stats["max"] = high if stats["max"] is None else max(stats["max"], high)
                stats["n_negative"] += int((parsed < 0).sum())
                stats["n_zero"] += int((parsed == 0).sum())
        for column in dates:
            present = chunk[column][~_is_empty(chunk[column])]
            parsed = pd.to_datetime(present, errors="coerce", format=date_format)
            stats = dat[column]
            stats["n_unparseable"] += int(parsed.isna().sum())
            parsed = parsed.dropna()
            if len(parsed):
                low, high = parsed.min().strftime("%Y-%m-%d"), parsed.max().strftime("%Y-%m-%d")
                stats["min"] = low if stats["min"] is None else min(stats["min"], low)
                stats["max"] = high if stats["max"] is None else max(stats["max"], high)
        if key:
            hashed = pd.util.hash_pandas_object(chunk[list(key)], index=False)
            for value in hashed.to_numpy().tolist():
                if value in seen:
                    duplicates += 1
                else:
                    seen.add(value)

    columns = columns or []
    out_columns: dict[str, dict] = {c: {"n_empty": int(empty[c])} for c in columns}
    for column, counter in counters.items():
        out_columns[column].update(
            n_distinct=len(counter),
            capped=capped[column],
            top_values=dict(counter.most_common(TOP_VALUES)),
        )
    for column, stats in num.items():
        out_columns[column].update(stats)
    for column, stats in dat.items():
        out_columns[column].update(stats)
    result = {"n_rows": n_rows, "n_columns": len(columns), "columns": out_columns}
    if key:
        result["key"] = list(key)
        result["duplicate_keys"] = duplicates
    return result
