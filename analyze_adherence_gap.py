"""Calculate pipeline/CRM adherence metrics and a sequential gap bridge."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


QUARTERS = ("this_quarter", "last_quarter")


def _load_data(input_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    required = ("patient_records.csv", "pharmacy_hub_fills.csv", "crm_case_manager_notes.csv")
    missing = [name for name in required if not (input_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing generated files in {input_dir}: {', '.join(missing)}")

    patients = pd.read_csv(input_dir / required[0])
    fills = pd.read_csv(input_dir / required[1])
    notes = pd.read_csv(input_dir / required[2])
    for frame, name in ((patients, "patient_records"), (fills, "pharmacy_hub_fills"), (notes, "crm_case_manager_notes")):
        if "patient_id" not in frame:
            raise ValueError(f"{name} must contain patient_id")
    return patients, fills, notes


def _quarter_metrics(
    quarter: str,
    patients: pd.DataFrame,
    fills: pd.DataFrame,
    notes: pd.DataFrame,
) -> dict[str, float | int]:
    quarter_fills = fills.loc[fills["quarter"].eq(quarter)].copy()
    quarter_notes = notes.loc[notes["quarter"].eq(quarter)].copy()
    patient_ids = patients["patient_id"]
    hub_population = set(quarter_fills["patient_id"])

    pdc_column = f"{quarter}_pdc"
    pdc_adherent = patients[pdc_column].ge(0.80)
    pipeline_rate = pdc_adherent.mean()

    hub_on_schedule = (
        quarter_fills.groupby("patient_id")["fill_status"]
        .apply(lambda values: values.eq("on_time").all())
        .reindex(patient_ids, fill_value=False)
        .set_axis(patients.index)
    )
    crm_on_schedule = (
        quarter_notes.drop_duplicates("patient_id")
        .set_index("patient_id")["refilled_on_schedule"]
        .astype(bool)
        .reindex(patient_ids, fill_value=False)
        .set_axis(patients.index)
    )

    crm_patients = patients["patient_id"].isin(hub_population)
    crm_indexed = pd.Series(crm_patients.to_numpy(), index=patients.index)

    # Sequential decomposition:
    # 1) population: all enrolled -> CRM's observed population
    # 2) definition: PDC >= 80% -> hub on-time refill flag
    # 3) source: hub fill flag -> CRM case-manager flag
    def population_rate(values: pd.Series, population: pd.Series) -> float:
        return float(values.loc[population].mean()) if population.any() else 0.0

    rates = {
        "pipeline_observed": population_rate(pdc_adherent, pd.Series(True, index=patients.index)),
        "after_population": population_rate(pdc_adherent, crm_indexed),
        "after_definition": population_rate(hub_on_schedule, crm_indexed),
        "crm_observed": population_rate(crm_on_schedule, crm_indexed),
    }
    population_effect = rates["after_population"] - rates["pipeline_observed"]
    definition_effect = rates["after_definition"] - rates["after_population"]
    source_effect = rates["crm_observed"] - rates["after_definition"]
    return {
        "quarter": quarter,
        "enrolled_patients": int(len(patients)),
        "crm_population_patients": int(len(hub_population)),
        "crm_note_patients": int(quarter_notes["patient_id"].nunique()),
        "crm_note_coverage": (
            float(len(set(quarter_notes["patient_id"]) & hub_population) / len(hub_population))
            if hub_population
            else 0.0
        ),
        "hub_fill_patients": int(len(hub_population)),
        "pipeline_rate": rates["pipeline_observed"],
        "crm_rate": rates["crm_observed"],
        "total_gap_crm_minus_pipeline": rates["crm_observed"] - rates["pipeline_observed"],
        "population_effect": population_effect,
        "definition_effect": definition_effect,
        "source_effect": source_effect,
        "bridge_check": population_effect + definition_effect + source_effect,
    }


def _write_report(output_dir: Path, bridge: pd.DataFrame) -> None:
    bridge.to_csv(output_dir / "adherence_gap_bridge.csv", index=False)
    lines = [
        "# Adherence gap bridge",
        "",
        "Methodology: a sequential decomposition in the order **population -> measure definition -> data source**.",
        "The order is fixed so the effects sum exactly to the observed CRM-minus-pipeline gap; changing the order",
        "would change individual effects because the factors can interact.",
        "",
        "The stages are:",
        "",
        "1. Pipeline observed: all enrolled patients, hub-fill PDC >= 80%.",
        "2. Population-adjusted: patients with at least one specialty-pharmacy fill, still using hub-fill PDC >= 80%.",
        "3. Definition-adjusted: the same fill population, using a hub-derived on-time refill flag.",
        "4. CRM observed: the same fill population, using the case-manager flag; missing CRM notes remain visible in the denominator and are reported separately as missing documentation.",
        "",
        "| Quarter | Pipeline | CRM | Gap | Population effect | Definition effect | Source effect | Bridge check |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in bridge.itertuples(index=False):
        lines.append(
            f"| {row.quarter} | {row.pipeline_rate:.1%} | {row.crm_rate:.1%} | "
            f"{row.total_gap_crm_minus_pipeline:+.1%} | {row.population_effect:+.1%} | "
            f"{row.definition_effect:+.1%} | {row.source_effect:+.1%} | {row.bridge_check:+.1%} |"
        )
    lines.extend(
        [
            "",
            "Effects are percentage-point contributions to CRM minus pipeline. A positive value means that factor",
            "moves the result toward the CRM rate; a negative value widens the difference in the selected sequence.",
        ]
    )
    (output_dir / "adherence_gap_bridge.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory containing generated CSV files")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    output_dir = args.output_dir or args.input_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    patients, fills, notes = _load_data(args.input_dir)
    bridge = pd.DataFrame(
        [_quarter_metrics(quarter, patients, fills, notes) for quarter in QUARTERS]
    )
    _write_report(output_dir, bridge)
    print(bridge.to_string(index=False))
    print(f"Wrote {output_dir / 'adherence_gap_bridge.csv'}")
    print(f"Wrote {output_dir / 'adherence_gap_bridge.md'}")


if __name__ == "__main__":
    main()
