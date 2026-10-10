"""Session-only validation of uploaded company vehicle profiles."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import isfinite
from pathlib import Path
import re
from typing import Any

import pandas as pd

from .scenario import FUEL_OPTIONS, FUEL_TYPE_ALIASES, canonical_fuel_type


FLEET_TEMPLATE_COLUMNS = (
    "company_id",
    "vehicle_id",
    "vehicle_type",
    "capacity",
    "max_speed_kmph",
    "available",
    "fuel_type",
    "fuel_consumption_per_km",
    "fuel_price_per_unit",
    "currency",
    "driver_cost_per_hour",
    "shift_start_min",
    "shift_end_min",
    "available_fuel_quantity",
)
REQUIRED_FLEET_COLUMNS = frozenset({"vehicle_id", "capacity"})
FLEET_COLUMN_ALIASES = {
    "company": "company_id",
    "fleet_id": "company_id",
    "company_name": "company_id",
    "vehicle": "vehicle_id",
    "id": "vehicle_id",
    "type": "vehicle_type",
    "vehicle_capacity": "capacity",
    "speed": "max_speed_kmph",
    "max_speed": "max_speed_kmph",
    "availability": "available",
    "status": "available",
    "fuel_efficiency": "fuel_consumption_per_km",
    "fuel_use_per_km": "fuel_consumption_per_km",
    "fuel_price": "fuel_price_per_unit",
    "cost_currency": "currency",
    "driver_hourly_cost": "driver_cost_per_hour",
    "shift_start": "shift_start_min",
    "shift_end": "shift_end_min",
    "available_fuel": "available_fuel_quantity",
}
FLEET_TEMPLATE_CSV = (
    ",".join(FLEET_TEMPLATE_COLUMNS) + "\n"
).encode("utf-8")


@dataclass(frozen=True)
class FleetUploadValidation:
    rows: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]
    columns: tuple[str, ...]
    preview_rows: tuple[dict[str, Any], ...] = ()

    @property
    def valid(self) -> bool:
        return bool(self.rows) and not self.errors


def validate_fleet_upload(
    filename: str,
    content: bytes,
) -> FleetUploadValidation:
    """Parse CSV/XLSX data and reject the entire upload on any invalid row."""
    suffix = Path(filename).suffix.casefold()
    if suffix not in {".csv", ".xlsx"}:
        return FleetUploadValidation((), ("Upload a .csv or .xlsx fleet file.",), ())
    try:
        frame = (
            pd.read_csv(BytesIO(content), dtype=object)
            if suffix == ".csv"
            else pd.read_excel(BytesIO(content), dtype=object, engine="openpyxl")
        )
    except ImportError:
        return FleetUploadValidation(
            (),
            ("Excel support is unavailable; use CSV or install the declared Excel reader dependency.",),
            (),
        )
    except Exception:
        return FleetUploadValidation(
            (),
            ("The uploaded file could not be parsed. Check its format and header row.",),
            (),
        )

    if frame.empty and len(frame.columns) == 0:
        return FleetUploadValidation((), ("The uploaded file has no header row.",), ())

    normalized_columns: list[str] = []
    for column in frame.columns:
        normalized = "_".join(str(column).strip().casefold().replace("-", " ").split())
        normalized_columns.append(FLEET_COLUMN_ALIASES.get(normalized, normalized))
    if len(set(normalized_columns)) != len(normalized_columns):
        return FleetUploadValidation(
            (),
            ("Column names collide after normalization; use one column per supported field.",),
            tuple(normalized_columns),
            tuple(
                {name: _clean_value(value) for name, value in raw.items()}
                for raw in frame.to_dict(orient="records")
            ),
        )
    frame.columns = normalized_columns
    missing = sorted(REQUIRED_FLEET_COLUMNS - set(normalized_columns))
    unknown = sorted(set(normalized_columns) - set(FLEET_TEMPLATE_COLUMNS))
    header_errors = []
    if missing:
        header_errors.append(f"Missing required column(s): {', '.join(missing)}.")
    if unknown:
        header_errors.append(f"Unsupported column(s): {', '.join(unknown)}.")
    if header_errors:
        return FleetUploadValidation(
            (),
            tuple(header_errors),
            tuple(normalized_columns),
            tuple(
                {name: _clean_value(value) for name, value in raw.items()}
                for raw in frame.to_dict(orient="records")
            ),
        )

    errors: list[str] = []
    records: list[dict[str, Any]] = []
    preview_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row_number, raw in enumerate(frame.to_dict(orient="records"), start=2):
        if all(_is_blank(value) for value in raw.values()):
            continue
        row = {name: _clean_value(value) for name, value in raw.items()}
        company_id = str(row.get("company_id") or "").strip()
        vehicle_id = str(row.get("vehicle_id") or "").strip()
        prefix = f"Row {row_number}"
        row_errors: list[str] = []
        if not vehicle_id:
            row_errors.append(f"{prefix}: vehicle_id is required")
        key = (company_id.casefold(), vehicle_id.casefold())
        if vehicle_id and key in seen:
            row_errors.append(
                f"{prefix}: vehicle_id must be unique within its company profile"
            )
        if vehicle_id:
            seen.add(key)
        row["company_id"] = company_id
        row["vehicle_id"] = vehicle_id

        _numeric(row, "capacity", prefix, row_errors, required=True, minimum=0, strict_min=True)
        _numeric(row, "max_speed_kmph", prefix, row_errors, minimum=0, strict_min=True)
        _boolean(row, "available", prefix, row_errors)
        _numeric(row, "fuel_consumption_per_km", prefix, row_errors, minimum=0, strict_min=True)
        _numeric(row, "fuel_price_per_unit", prefix, row_errors, minimum=0)
        _numeric(row, "driver_cost_per_hour", prefix, row_errors, minimum=0)
        _numeric(row, "available_fuel_quantity", prefix, row_errors, minimum=0)
        _integer(row, "shift_start_min", prefix, row_errors)
        _integer(row, "shift_end_min", prefix, row_errors)
        start, end = row.get("shift_start_min"), row.get("shift_end_min")
        if start is not None and end is not None and end < start:
            row_errors.append(f"{prefix}: shift_end_min must be at or after shift_start_min")

        fuel_type = row.get("fuel_type")
        if fuel_type is not None and str(fuel_type).strip():
            submitted_fuel_type = str(fuel_type).strip()
            canonical_type = canonical_fuel_type(submitted_fuel_type)
            if canonical_type is None:
                accepted = ", ".join(FUEL_OPTIONS)
                row_errors.append(
                    f"{prefix}: unsupported fuel_type {submitted_fuel_type!r}. "
                    f"Use {accepted}; Petrol is accepted as a Gasoline alias. "
                    "CNG and LPG are not supported because this model has no "
                    "fuel-unit or emissions factors for them."
                )
                row["fuel_type"] = submitted_fuel_type
            elif submitted_fuel_type.casefold() in {
                alias.casefold() for alias in FUEL_TYPE_ALIASES
            }:
                row["fuel_type"] = next(
                    alias
                    for alias in FUEL_TYPE_ALIASES
                    if alias.casefold() == submitted_fuel_type.casefold()
                )
            else:
                row["fuel_type"] = canonical_type
        else:
            row["fuel_type"] = None

        for field in ("vehicle_type",):
            row[field] = str(row.get(field) or "").strip() or None
        currency = row.get("currency")
        normalized_currency = str(currency).strip().upper() if currency is not None else ""
        if normalized_currency and not re.fullmatch(r"[A-Z]{3}", normalized_currency):
            row_errors.append(f"{prefix}: currency must be a 3-letter code")
        row["currency"] = normalized_currency or None
        preview_rows.append(row.copy())
        if row_errors:
            errors.extend(row_errors)
        else:
            records.append(row)

    if not records and not errors:
        errors.append("The file contains no vehicle rows.")
    return FleetUploadValidation(
        tuple(records) if not errors else (),
        tuple(errors),
        tuple(normalized_columns),
        tuple(preview_rows),
    )


def _clean_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return value


def _is_blank(value: Any) -> bool:
    return value is None or pd.isna(value) or (isinstance(value, str) and not value.strip())


def _numeric(
    row: dict[str, Any],
    name: str,
    prefix: str,
    errors: list[str],
    *,
    required: bool = False,
    minimum: float,
    strict_min: bool = False,
) -> None:
    value = row.get(name)
    if value is None:
        if required:
            errors.append(f"{prefix}: {name} is required")
        row[name] = None
        return
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        errors.append(f"{prefix}: {name} must be numeric")
        return
    if not isfinite(number) or (number <= minimum if strict_min else number < minimum):
        comparator = "greater than" if strict_min else "at least"
        errors.append(f"{prefix}: {name} must be {comparator} {minimum:g}")
        return
    row[name] = number


def _integer(
    row: dict[str, Any],
    name: str,
    prefix: str,
    errors: list[str],
) -> None:
    value = row.get(name)
    if value is None:
        row[name] = None
        return
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        errors.append(f"{prefix}: {name} must be an integer from 0 to 1440")
        return
    if not isfinite(number) or not number.is_integer() or not 0 <= number <= 1440:
        errors.append(f"{prefix}: {name} must be an integer from 0 to 1440")
        return
    row[name] = int(number)


def _boolean(
    row: dict[str, Any],
    name: str,
    prefix: str,
    errors: list[str],
) -> None:
    value = row.get(name)
    if value is None:
        row[name] = None
        return
    if isinstance(value, bool):
        row[name] = value
        return
    normalized = str(value).strip().casefold()
    if normalized in {"true", "yes", "1", "available"}:
        row[name] = True
    elif normalized in {
        "false",
        "no",
        "0",
        "unavailable",
        "unavailable / breakdown",
    }:
        row[name] = False
    else:
        errors.append(
            f"{prefix}: {name} must be true/false, yes/no, 1/0, or available/unavailable"
        )
