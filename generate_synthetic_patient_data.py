"""Create patient-level adherence data from a Synthea CSV export.

Synthea supplies the patient, encounter, and medication history. This script
adds synthetic specialty-pharmacy hub fills and CRM notes so the result can
support the adherence discrepancy case study without using real PHI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


THIS_QUARTER_TARGETS = {"pipeline": 0.58, "crm": 0.71}
LAST_QUARTER_TARGETS = {"pipeline": 0.64, "crm": 0.72}
QUARTER_DAYS = 91


def _date(value: object, fallback: pd.Timestamp) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    return fallback if pd.isna(parsed) else parsed.normalize()


def _stable_rank(patient_id: str, salt: str) -> float:
    digest = hashlib.sha256(f"{salt}:{patient_id}".encode()).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def _read_synthea(input_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    patients_path = input_dir / "patients.csv"
    medications_path = input_dir / "medications.csv"
    if not patients_path.exists() or not medications_path.exists():
        raise FileNotFoundError(
            "Synthea CSV export must contain patients.csv and medications.csv: "
            f"{input_dir}"
        )

    patients = pd.read_csv(patients_path, dtype={"Id": "string"})
    medications = pd.read_csv(medications_path, dtype={"PATIENT": "string"})
    patients = patients.rename(columns={"Id": "synthea_patient_id"})
    medications = medications.rename(columns={"PATIENT": "synthea_patient_id"})

    if "synthea_patient_id" not in patients or "synthea_patient_id" not in medications:
        raise ValueError("Unexpected Synthea headers: expected patients.Id and medications.PATIENT")
    return patients, medications


def _make_demo_inputs(patient_count: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create a Synthea-shaped input when Java/Synthea is not available locally."""
    if patient_count < 1:
        raise ValueError("patient_count must be positive")
    patients = pd.DataFrame(
        {
            "synthea_patient_id": [f"synthea-{index + 1:06d}" for index in range(patient_count)],
            "BIRTHDATE": ["1980-01-01"] * patient_count,
            "GENDER": ["F" if index % 2 == 0 else "M" for index in range(patient_count)],
        }
    )
    medications = pd.DataFrame(
        {
            "synthea_patient_id": patients["synthea_patient_id"],
            "START": ["2024-01-01"] * patient_count,
            "STOP": [None] * patient_count,
            "medication_name": ["Synthetic maintenance therapy"] * patient_count,
        }
    )
    return patients, medications


def _select_medications(patients: pd.DataFrame, medications: pd.DataFrame) -> pd.DataFrame:
    medications = medications.copy()
    medications["START"] = pd.to_datetime(medications.get("START"), errors="coerce")
    medications["STOP"] = pd.to_datetime(medications.get("STOP"), errors="coerce")
    medications["medication_name"] = medications.get(
        "DESCRIPTION", pd.Series("Synthea maintenance therapy", index=medications.index)
    ).fillna("Synthea maintenance therapy")
    selected = (
        medications.sort_values(["synthea_patient_id", "START"])
        .drop_duplicates("synthea_patient_id", keep="last")
        .loc[:, ["synthea_patient_id", "START", "STOP", "medication_name"]]
    )
    return patients[["synthea_patient_id"]].merge(selected, on="synthea_patient_id", how="left")


def _quarter_bounds(as_of_date: pd.Timestamp) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    this_start = as_of_date - pd.Timedelta(days=QUARTER_DAYS - 1)
    last_end = this_start - pd.Timedelta(days=1)
    return {
        "this_quarter": (this_start, as_of_date),
        "last_quarter": (last_end - pd.Timedelta(days=QUARTER_DAYS - 1), last_end),
    }


def _make_records(
    patients: pd.DataFrame,
    medication_choices: pd.DataFrame,
    as_of_date: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(20261103)
    bounds = _quarter_bounds(as_of_date)
    patient_rows: list[dict[str, object]] = []
    fill_rows: list[dict[str, object]] = []
    note_rows: list[dict[str, object]] = []

    medication_choices = medication_choices.set_index("synthea_patient_id")
    patient_ids = [f"PAT-{index + 1:06d}" for index in range(len(patients))]

    def selected_ids(salt: str, target: float) -> set[str]:
        count = round(len(patient_ids) * target)
        ranked = sorted(patient_ids, key=lambda patient_id: _stable_rank(patient_id, salt))
        return set(ranked[:count])

    pipeline_ids = {
        "this_quarter": selected_ids("pipeline-this", THIS_QUARTER_TARGETS["pipeline"]),
        "last_quarter": selected_ids("pipeline-last", LAST_QUARTER_TARGETS["pipeline"]),
    }
    crm_ids = {
        "this_quarter": selected_ids("crm-this", THIS_QUARTER_TARGETS["crm"]),
        "last_quarter": selected_ids("crm-last", LAST_QUARTER_TARGETS["crm"]),
    }
    for index, source_row in patients.reset_index(drop=True).iterrows():
        synthea_id = str(source_row["synthea_patient_id"])
        patient_id = patient_ids[index]
        med = medication_choices.loc[synthea_id] if synthea_id in medication_choices.index else None
        source_start = _date(med["START"], as_of_date - pd.Timedelta(days=365)) if med is not None else as_of_date
        enrollment_date = max(source_start, as_of_date - pd.Timedelta(days=540))
        medication_start = _date(med["START"], enrollment_date) if med is not None else enrollment_date
        medication_name = med["medication_name"] if med is not None else "Synthea maintenance therapy"

        patient_fills: list[dict[str, object]] = []
        pdc_values: dict[str, float] = {}
        for quarter_name, (quarter_start, quarter_end) in bounds.items():
            pipeline_target = (
                THIS_QUARTER_TARGETS["pipeline"]
                if quarter_name == "this_quarter"
                else LAST_QUARTER_TARGETS["pipeline"]
            )
            crm_target = (
                THIS_QUARTER_TARGETS["crm"]
                if quarter_name == "this_quarter"
                else LAST_QUARTER_TARGETS["crm"]
            )
            pipeline_adherent = patient_id in pipeline_ids[quarter_name]
            crm_on_schedule = patient_id in crm_ids[quarter_name]
            hub_on_schedule = _stable_rank(patient_id, f"hub-{quarter_name}") < 0.65
            missed_doses = _stable_rank(patient_id, f"missed-{quarter_name}") < 0.12
            refill_delayed = not hub_on_schedule
            discontinuation = _stable_rank(patient_id, f"discontinued-{quarter_name}") < 0.04
            missed_duration_days = (
                int(1 + (_stable_rank(patient_id, f"duration-{quarter_name}") * 6))
                if missed_doses
                else 0
            )
            missed_reason = (
                ["travel", "side_effects", "forgot", "cost"][
                    int(_stable_rank(patient_id, f"missed-reason-{quarter_name}") * 4)
                ]
                if missed_doses
                else ""
            )
            delay_reason = (
                ["pharmacy shipment", "insurance authorization", "inventory shortage"][
                    int(_stable_rank(patient_id, f"delay-reason-{quarter_name}") * 3)
                ]
                if refill_delayed
                else ""
            )
            discontinuation_reason = (
                ["adverse event", "patient choice", "insurance or cost"][
                    int(_stable_rank(patient_id, f"discontinue-reason-{quarter_name}") * 3)
                ]
                if discontinuation
                else ""
            )
            fill_count = 3 if pipeline_adherent else 2
            for refill_number in range(fill_count):
                scheduled = quarter_start + pd.Timedelta(days=refill_number * 30)
                delay = 0 if hub_on_schedule else int(rng.integers(8, 24))
                refill_date = min(scheduled + pd.Timedelta(days=delay), quarter_end)
                supply_days = 30 if pipeline_adherent else (30 if refill_number == 0 else 15)
                record = {
                    "patient_id": patient_id,
                    "quarter": quarter_name,
                    "refill_number": refill_number + 1,
                    "refill_date": refill_date.date().isoformat(),
                    "scheduled_refill_date": scheduled.date().isoformat(),
                    "supply_days": supply_days,
                    "pharmacy_hub": f"HUB-{(index % 4) + 1:02d}",
                    "fill_status": "on_time" if delay == 0 else "delayed",
                    "delay_days": delay,
                    "medication_name": medication_name,
                }
                fill_rows.append(record)
                patient_fills.append(record)
            pdc_values[quarter_name] = min(
                sum(fill["supply_days"] for fill in patient_fills if fill["quarter"] == quarter_name)
                / QUARTER_DAYS,
                1.0,
            )

            note_rows.append(
                {
                    "patient_id": patient_id,
                    "quarter": quarter_name,
                    "case_manager": f"CM-{(index % 8) + 1:02d}",
                    "refilled_on_schedule": crm_on_schedule,
                    "missed_doses_label": missed_doses,
                    "missed_duration_days_label": missed_duration_days,
                    "missed_reason_label": missed_reason,
                    "refill_delay_label": refill_delayed,
                    "delay_reason_label": delay_reason,
                    "discontinuation_label": discontinuation,
                    "discontinuation_reason_label": discontinuation_reason,
                    "note": " ".join(
                        part
                        for part in [
                            (
                                f"Patient reported missing medication for {missed_duration_days} "
                                f"days due to {missed_reason}."
                                if missed_doses
                                else "Patient did not report missed doses."
                            ),
                            (
                                f"Refill delayed because of {delay_reason}."
                                if refill_delayed
                                else "Refill was completed on schedule."
                            ),
                            (
                                f"Patient discontinued treatment due to {discontinuation_reason}."
                                if discontinuation
                                else "No discontinuation reported."
                            ),
                            (
                                "Patient reports refills on schedule; continue monthly outreach."
                                if crm_on_schedule
                                else "Case manager documented a late refill and reinforced refill reminders."
                            ),
                        ]
                    ),
                }
            )

        this_pdc = pdc_values["this_quarter"]
        discontinuation_reason = "None"
        discontinuation_rank = _stable_rank(patient_id, "discontinuation")
        if discontinuation_rank < 0.03:
            discontinuation_reason = "adverse_event"
        elif discontinuation_rank < 0.06:
            discontinuation_reason = "patient_choice"
        elif discontinuation_rank < 0.10:
            discontinuation_reason = "insurance_or_cost"
        patient_rows.append(
            {
                "patient_id": patient_id,
                "synthea_patient_id": synthea_id,
                "enrollment_date": enrollment_date.date().isoformat(),
                "medication_start_date": medication_start.date().isoformat(),
                "medication_name": medication_name,
                "medication_refill_dates": "|".join(fill["refill_date"] for fill in patient_fills),
                "medication_supply_days": "|".join(str(fill["supply_days"]) for fill in patient_fills),
                "pharmacy_hub_fill_records": json.dumps(patient_fills, separators=(",", ":")),
                "crm_case_manager_notes": " | ".join(
                    note["note"] for note in note_rows if note["patient_id"] == patient_id
                ),
                "adherence_status": "adherent" if this_pdc >= 0.80 else "non_adherent",
                "this_quarter_pdc": round(this_pdc, 4),
                "last_quarter_pdc": round(pdc_values["last_quarter"], 4),
                "missed_dose_count": sum(fill["delay_days"] > 0 for fill in patient_fills),
                "delay_days_total": sum(fill["delay_days"] for fill in patient_fills),
                "discontinuation_reason": discontinuation_reason,
            }
        )

    return pd.DataFrame(patient_rows), pd.DataFrame(fill_rows), pd.DataFrame(note_rows)


def _write_outputs(
    output_dir: Path,
    patient_records: pd.DataFrame,
    fill_records: pd.DataFrame,
    crm_notes: pd.DataFrame,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    patient_records.to_csv(output_dir / "patient_records.csv", index=False)
    fill_records.to_csv(output_dir / "pharmacy_hub_fills.csv", index=False)
    crm_notes.to_csv(output_dir / "crm_case_manager_notes.csv", index=False)

    metrics = pd.DataFrame(
        [
            {"metric": "Your pipeline", "this_quarter": 0.58, "last_quarter": 0.64},
            {"metric": "Client CRM", "this_quarter": 0.71, "last_quarter": 0.72},
        ]
    )
    metrics.to_csv(output_dir / "exhibit_2_metrics.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthea-dir", type=Path, help="Directory containing Synthea CSV exports")
    parser.add_argument("--demo-patients", type=int, default=None, help="Generate a reproducible Synthea-shaped demo dataset")
    parser.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--as-of-date", default="2025-06-30", help="End date for the current quarter (YYYY-MM-DD)")
    args = parser.parse_args()

    as_of_date = pd.Timestamp(args.as_of_date).normalize()
    if args.synthea_dir:
        patients, medications = _read_synthea(args.synthea_dir)
    elif args.demo_patients:
        patients, medications = _make_demo_inputs(args.demo_patients)
    else:
        parser.error("Provide --synthea-dir or --demo-patients")
    choices = _select_medications(patients, medications)
    patient_records, fill_records, crm_notes = _make_records(patients, choices, as_of_date)
    _write_outputs(args.output_dir, patient_records, fill_records, crm_notes)
    print(f"Wrote {len(patient_records):,} patient records to {args.output_dir}")
    print("Exhibit 2 targets: pipeline 58%/64%; CRM 71%/72% (this/last quarter).")


if __name__ == "__main__":
    main()
