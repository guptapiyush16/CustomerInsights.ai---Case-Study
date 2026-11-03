"""Extract adherence events from CRM notes with Gemini and evaluate them."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd


FIELDS = (
    "missed_doses",
    "missed_duration_days",
    "missed_reason",
    "refill_delay",
    "delay_reason",
    "discontinuation",
    "discontinuation_reason",
    "evidence",
)


def _load_dotenv() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _prompt(note: str) -> str:
    return f"""Extract adherence events from this CRM note.
Return JSON only with these keys:
missed_doses (boolean), missed_duration_days (integer or null),
missed_reason (string or null),
refill_delay (boolean), delay_reason (string or null),
discontinuation (boolean), discontinuation_reason (string or null),
evidence (short quote or explicit statement supporting the extraction).
Use null when a duration or reason is not stated. Do not infer medication-taking
from a refill delay. A refill delay is not proof that a dose was missed.

CRM note:
{note}"""


def _extract_with_gemini(note: str, api_key: str, model: str) -> dict[str, object]:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": _prompt(note)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "missed_doses": {"type": "BOOLEAN"},
                    "missed_duration_days": {"type": "INTEGER", "nullable": True},
                    "missed_reason": {"type": "STRING", "nullable": True},
                    "refill_delay": {"type": "BOOLEAN"},
                    "delay_reason": {"type": "STRING", "nullable": True},
                    "discontinuation": {"type": "BOOLEAN"},
                    "discontinuation_reason": {"type": "STRING", "nullable": True},
                    "evidence": {"type": "STRING"},
                },
                "required": list(FIELDS),
            },
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if error.code == 429:
            retry_seconds = 60
            try:
                retry_seconds = int(
                    json.loads(detail)["error"]["details"][-1]["retryDelay"].rstrip("s")
                )
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                pass
            reset_at = datetime.now(timezone.utc) + timedelta(seconds=retry_seconds)
            raise RuntimeError(
                f"Gemini quota limit reached. Retry after {retry_seconds} seconds "
                f"(approximately {reset_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')})."
            ) from error
        raise RuntimeError(f"Gemini request failed ({error.code}): {detail}") from error
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    result = json.loads(text)
    return {field: result.get(field) for field in FIELDS}


def _normalise(value: object) -> object:
    if pd.isna(value):
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return None
    return value


def _equivalent(field: str, left: object, right: object) -> bool:
    left = _normalise(left)
    right = _normalise(right)
    if field == "missed_duration_days" and left in {0, 0.0} and right is None:
        return True
    if field == "missed_duration_days" and right in {0, 0.0} and left is None:
        return True
    return left == right


def _binary_metrics(expected: pd.Series, predicted: pd.Series) -> dict[str, float]:
    expected = expected.astype(bool)
    predicted = predicted.astype(bool)
    tp = int((expected & predicted).sum())
    fp = int((~expected & predicted).sum())
    fn = int((expected & ~predicted).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "agreement": float((expected == predicted).mean()),
    }


def _evaluate(records: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    metrics: dict[str, object] = {}
    for field in ("missed_doses", "refill_delay", "discontinuation"):
        expected = records[f"{field}_label"]
        predicted = records[field]
        metrics[field] = _binary_metrics(expected, predicted)

    reason_rows = []
    for field in ("missed_reason", "delay_reason", "discontinuation_reason"):
        label = f"{field}_label"
        comparable = records[label].fillna("").astype(str).str.lower().str.strip()
        extracted = records[field].fillna("").astype(str).str.lower().str.strip()
        active = comparable.ne("")
        reason_rows.append(
            {
                "field": field,
                "reason_accuracy_when_present": float((comparable[active] == extracted[active]).mean())
                if active.any()
                else 0.0,
                "labelled_cases": int(active.sum()),
            }
        )
    metrics["reason_metrics"] = reason_rows
    comparable_fields = [
        "missed_doses",
        "missed_duration_days",
        "refill_delay",
        "delay_reason",
        "discontinuation",
        "discontinuation_reason",
    ]
    exact = records.apply(
        lambda row: all(
            _normalise(row[field]) == _normalise(row[f"{field}_label"])
            for field in comparable_fields
        ),
        axis=1,
    )
    metrics["extraction_accuracy_exact_record"] = float(exact.mean())
    normalized_exact = records.apply(
        lambda row: all(
            _equivalent(field, row[field], row[f"{field}_label"])
            for field in comparable_fields
        ),
        axis=1,
    )
    metrics["extraction_accuracy_normalized_record"] = float(normalized_exact.mean())
    metrics["exact_record_definition"] = (
        "Strict exact accuracy requires literal equality; normalized accuracy treats "
        "unstated missed duration (null) and labelled zero as equivalent."
    )
    metrics["agreement_with_refill_evidence"] = {
        "refill_delay_agreement": float(
            (records["refill_delay"].astype(bool) == records["hub_delay_evidence"].astype(bool)).mean()
        ),
        "note": "Refill-derived evidence evaluates shipment/fill timing only; it is not ground truth for swallowed doses.",
    }
    return pd.DataFrame(
        [
            {"signal": field, **values}
            for field, values in metrics.items()
            if isinstance(values, dict) and "precision" in values
        ]
    ), metrics


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0, help="Offset in deterministic shuffled notes for batch evaluation")
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest"))
    parser.add_argument("--sleep-seconds", type=float, default=0.1)
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY in the environment before running this script.")
    output_dir = args.output_dir or args.input_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    notes = pd.read_csv(args.input_dir / "crm_case_manager_notes.csv")
    fills = pd.read_csv(args.input_dir / "pharmacy_hub_fills.csv")
    notes = (
        notes.sample(frac=1, random_state=20261103)
        .iloc[args.offset:args.offset + args.sample_size]
        .reset_index(drop=True)
    )
    delayed = fills.groupby(["patient_id", "quarter"])["fill_status"].apply(lambda values: values.eq("delayed").any())
    notes["hub_delay_evidence"] = [
        bool(delayed.get((row.patient_id, row.quarter), False)) for row in notes.itertuples()
    ]

    extracted = []
    try:
        for note in notes["note"]:
            extracted.append(_extract_with_gemini(note, api_key, args.model))
            time.sleep(args.sleep_seconds)
    except RuntimeError as error:
        status = {
            "status": "quota_limit" if "quota limit reached" in str(error) else "error",
            "message": str(error),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        (output_dir / "note_extraction_status.json").write_text(
            json.dumps(status, indent=2) + "\n", encoding="utf-8"
        )
        raise
    (output_dir / "note_extraction_status.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "message": f"Gemini extraction completed for {len(extracted)} labelled notes.",
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    extracted_frame = pd.DataFrame(extracted)
    results = pd.concat([notes, extracted_frame.add_suffix("_extracted")], axis=1)
    for field in FIELDS:
        results[field] = results[f"{field}_extracted"]
    results_path = output_dir / "note_extraction_results.csv"
    if args.offset > 0 and results_path.exists():
        previous = pd.read_csv(results_path)
        results = pd.concat([previous, results], ignore_index=True)
    results.to_csv(results_path, index=False)
    metric_table, metrics = _evaluate(results)
    metric_table.to_csv(output_dir / "note_extraction_metrics.csv", index=False)
    (output_dir / "note_extraction_metrics.json").write_text(
        json.dumps(metrics, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(metric_table.to_string(index=False))
    print(f"Wrote {output_dir / 'note_extraction_results.csv'}")
    print(f"Wrote {output_dir / 'note_extraction_metrics.json'}")


if __name__ == "__main__":
    main()
