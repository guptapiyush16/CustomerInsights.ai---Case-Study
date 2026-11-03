# Solution Document: Adherence Reporting Reconciliation

**Prepared by:** Piyush Gupta

> **How to run:** Read [`README.md`](README.md) first for installation,
> data-generation, Gemini extraction, and dashboard startup commands.

## 1. Solution overview

The case study compares two adherence reports that disagree because they use
different patient populations, adherence definitions, and data sources. I built
a reproducible synthetic-data pipeline, a patient-level gap bridge, a Gemini
CRM-note extraction workflow, and a browser dashboard that makes the
reconciliation visible. The generated 10,000-patient demonstration reproduces
Exhibit 2: pipeline adherence of 58% this quarter and 64% last quarter, versus
CRM adherence of 71% and 72%. The dashboard also shows the contribution of
population, measure-definition, and source effects.

## 2. Approach and methodology

### Synthetic data generation

The generator accepts Synthea CSV exports containing `patients.csv` and
`medications.csv`. Because Java/Synthea was not installed in the evaluation
environment, it also provides a deterministic `--demo-patients 10000` mode
that produces a Synthea-shaped input and adds specialty-pharmacy and CRM
events.

Each generated patient has:

- A generated patient ID, enrollment date, and medication start date.
- Medication refill dates and supply-day values.
- Specialty-pharmacy hub and fill-status records.
- Quarterly case-manager CRM notes.
- PDC, missed-dose, delay, and discontinuation fields.

### Metric calculation

The pipeline metric is the proportion of all enrolled patients with pharmacy
fill-derived PDC of at least 80%. The CRM population follows the original case
definition: patients with at least one specialty-pharmacy fill. Within that
population, the CRM metric is the proportion whose case-manager flag says the
refill was on schedule. A fill-population patient with no CRM note remains
visible in the denominator and is reported separately as **missing CRM
documentation**; absence of a note is not evidence that a refill was late.
The synthetic bridge retains the original denominator so that Exhibit 2 can be
reproduced, while a production implementation should publish CRM coverage and
the missing-documentation count alongside the operational indicator.

### Gap bridge

I used a sequential decomposition in this order:

1. Start with all enrolled patients using the pipeline PDC definition.
2. Change the population to patients with at least one specialty-pharmacy fill,
   matching the original CRM definition.
3. Change the adherence measure from PDC to a hub-derived on-time refill flag.
4. Change the source from pharmacy hub records to the CRM case-manager flag.

The three effects sum to the observed CRM-minus-pipeline gap. The order is
reported explicitly because interaction effects mean that changing the order
can change individual attributions.

### LLM extraction

Gemini receives CRM note text and returns structured JSON containing missed
doses, missed duration, missed-dose reason, refill delay, delay reason,
discontinuation, discontinuation reason, and evidence. Temperature is set to
zero and a response schema is supplied to constrain the output. The API key is
read locally from `.env` and is never sent to the browser.

## 3. How you arrived at it

The main alternatives were:

- **Use only Synthea output:** rejected because Synthea provides realistic
  clinical and medication history but does not provide the case-specific
  specialty-pharmacy hub and CRM case-manager fields.
- **Use only hand-authored rows:** rejected because it would be less realistic
  and less reusable for a patient-level case study.
- **Use a single aggregate table:** rejected because it would not support
  population, definition, source, and note-level reconciliation.
- **Use a black-box LLM classification:** rejected in favor of structured JSON,
  explicit evidence, labelled evaluation, and a distinction between refill
  evidence and actual medication-taking behavior.
- **Use a Shapley decomposition immediately:** not selected for the primary
  report because the sequential bridge is easier to explain to a business
  audience and reconciles exactly to the reported gap. A Shapley analysis
  could be added as a sensitivity analysis.

The key assumptions were that a 91-day quarter is appropriate for the
demonstration and PDC is based on supply days divided by quarter days. The
demo gives every patient at least one fill and a quarterly CRM note, so the
fill-population definition and note coverage are both 100% and the population
effect is zero. That complete coverage is a limitation of the synthetic demo;
the implementation separately reports note coverage and retains missing-note
fill patients in the CRM denominator.

## 4. Data

The preferred source is Synthea, an open-source synthetic patient simulator.
The generator uses its patient and medication exports as source lineage and
adds synthetic pharmacy and CRM events required by the case.

The included demo command is:

```powershell
python .\generate_synthetic_patient_data.py `
  --demo-patients 10000 `
  --output-dir .\data\generated
```

This produced:

- 10,000 patient records.
- 52,200 pharmacy-hub fill records.
- 20,000 quarterly CRM notes.
- Exhibit 2 metrics and bridge outputs.

The data is synthetic and contains no real patient information. Synthea and
the demo generator do not model actual medication ingestion. A pharmacy fill
indicates medication availability or dispensing activity, not that a patient
swallowed a dose.

## 5. Evaluation

### Exhibit 2 reproduction

| Metric | This quarter | Last quarter |
|---|---:|---:|
| Your pipeline | 58% | 64% |
| Client CRM | 71% | 72% |

### Demonstration gap bridge

On the 10,000-patient generated dataset, the sequential bridge produced:

| Quarter | Observed gap | Population effect | Definition effect | Source effect |
|---|---:|---:|---:|---:|
| This quarter | +13.0 pp | 0.0 pp | +6.41 pp | +6.59 pp |
| Last quarter | +8.0 pp | 0.0 pp | +1.35 pp | +6.65 pp |

The population effect is zero in this demo because every generated patient has
at least one specialty-pharmacy fill. The demo also happens to have a CRM note
for every fill-population patient, but that is note coverage, not the
definition of the CRM population. In a real extract, patients with no fill are
outside the CRM denominator, while fill-population patients with no CRM note
remain in the denominator and receive a false/not-on-schedule CRM signal.

### LLM extraction evaluation

The labelled sample compares Gemini output with synthetic note labels using
precision, recall, F1, binary agreement, reason accuracy, and exact-record
accuracy. It also compares the extracted refill-delay signal with delayed
pharmacy fills. Strict exact-record accuracy and normalized exact-record
accuracy are reported separately from refill-evidence agreement. The observed
five-note evaluation result was:

| Signal / measure | Result | Interpretation |
|---|---:|---|
| Missed-dose F1 | 100.0% | 1 labelled positive; the model matched it |
| Refill-delay F1 | 85.7% | 3 labelled positives; one false positive reduced precision |
| Discontinuation F1 | 0.0% | 0 labelled positives; recall was not estimable |
| Strict exact-record accuracy | 20.0% | Only 1 of 5 rows matched every compared field literally |
| Normalized exact-record accuracy | 80.0% | Null/zero duration equivalence applied before row comparison |
| Refill-evidence agreement | 80.0% | Agreement with delayed hub-fill evidence, not ingestion |

These results are intentionally reported without hiding the weaker outcomes.

#### Why discontinuation performed poorly

The discontinuation F1 of 0% does **not** show that Gemini failed to detect a
known discontinuation case. The five-note sample contained zero positive
discontinuation labels and zero predicted discontinuations. Therefore:

- True positives = 0, false positives = 0, and false negatives = 0.
- Accuracy/agreement was 100%, because every row was correctly classified as
  negative.
- Precision, recall, and F1 are not substantively estimable for this sample;
  the evaluator reports F1 as 0 when there are no positive cases.
- No discontinuation reason was present in the sample, so reason accuracy for
  that field was also based on zero labelled cases.

This is a **sampling/design limitation**, not evidence of good or bad
discontinuation sensitivity. The evaluation sample was randomly selected
without stratifying for rare events. A valid next test must oversample or
explicitly include discontinuation-positive notes and report support counts.

#### Why exact-record accuracy was only 20%

Exact-record accuracy requires every field in a row to match simultaneously,
so one field mismatch makes the entire row incorrect. Two additional issues
make this metric conservative:

1. The five-note sample is too small for a stable estimate.
2. The synthetic labels use `0` for “no missed duration,” while the LLM schema
   correctly uses `null` when no duration is stated; these are semantically
   equivalent for this task but fail a literal comparison.

The refill-delay false positive is also informative: one note said the refill
was completed on schedule but separately said the case manager documented a
late refill. Gemini treated the explicit “late refill” phrase as a delay,
while the label was generated from the hub-fill status. This exposes a
real source/definition ambiguity rather than a simple parsing error.

The correct response is to add a manually adjudicated, stratified test set
and define null/zero equivalence before scoring. The dashboard reports this as
**Normalized exact-record accuracy**: an unstated duration (`null`) and a
labelled zero are treated as equivalent because both mean that no missed-dose
duration was recorded. This prevents a representation difference from being
counted as a substantive extraction error. It is distinct from strict
exact-record accuracy and from refill-evidence agreement.

The refill comparison is explicitly an operational-evidence agreement measure.
It is not a measure of whether a patient actually took medication.

The configured Gemini endpoint was:

- API version: `v1beta`
- Model: `gemini-flash-lite-latest`
- Authentication: `x-goog-api-key`

A five-note test completed successfully. A larger run encountered the
provider's free-tier request-per-minute limit and the website now displays the
retry/reset message from `note_extraction_status.json`.

The website supports controlled **Evaluate next 15** batches through
`dashboard_server.py`. Each click evaluates at most 15 additional labelled
notes, appends them to the cumulative result, and recomputes the metrics. This
is why the initial view showed five notes: five was a connectivity smoke test,
not the intended final evaluation size.

## 6. Tech stack

### Data generation and analytical processing

- **Python 3.14** is used for the reproducible command-line data and
  analytical workflows.
- **Pandas** is used to load, transform, join, validate, and aggregate patient,
  pharmacy-fill, CRM-note, and extraction-result tables.
- **NumPy** supports deterministic random generation of dates, adherence
  patterns, refill events, and case-specific synthetic attributes.
- **Synthea CSV exports** provide the preferred source-shaped patient,
  encounter, and medication history. The deterministic demo mode is used when
  a local Synthea runtime is unavailable.

### Adherence analytics

- `generate_synthetic_patient_data.py` creates the 10,000-patient dataset and
  normalized event tables.
- `analyze_adherence_gap.py` calculates pipeline PDC, the CRM operational
  indicator, Exhibit 2 outputs, and the sequential population/definition/source
  bridge.
- PDC and bridge calculations are performed at patient level before
  aggregation, making the result inspectable and reproducible.
- CSV and Markdown outputs provide portable analytical artifacts that can be
  reviewed without a database or frontend build step.

### LLM integration

- **Google Gemini `gemini-flash-lite-latest`** extracts structured missed-dose,
  refill-delay, discontinuation, reason, and evidence fields from CRM notes.
- The integration uses the **Gemini `v1beta` REST API** with the
  `x-goog-api-key` header.
- Python standard-library **`urllib`** handles HTTPS requests, so no additional
  SDK is required.
- A constrained JSON response schema, deterministic generation settings,
  labelled evaluation records, quota status, and cumulative 15-note batches
  support controlled evaluation.
- The API key is loaded from local `.env` configuration and is never exposed to
  the browser.

### Dashboard and local serving

- **Vanilla HTML, CSS, and JavaScript** implement the browser dashboard without
  a frontend framework or build pipeline.
- The UI uses browser `fetch`, local CSV/JSON parsing, responsive CSS, filters,
  quarter toggles, KPI cards, tables, gap-bridge visualizations, and note-level
  evidence rendering.
- `dashboard_server.py` uses Python's `http.server` to serve the dashboard and
  exposes a local POST endpoint for the next Gemini evaluation batch.
- The browser loads generated files from `data/generated/`; it does not read
  `.env` or call Gemini directly.

### Portability and validation

- The project uses **CSV** for tabular records and **JSON** for extraction
  metrics and quota status, keeping outputs easy to inspect and exchange.
- Python `py_compile`, deterministic generation, bridge reconciliation checks,
  and local HTTP smoke tests provide lightweight validation.
- The implementation has no database, frontend package manager, external
  browser JavaScript dependency, or production hosting requirement.

## 7. Use of LLMs

### LLMs and tools used during development

- **ChatGPT:** planning, requirements interpretation, and analytical discussion.
- **GitHub Copilot:** programming, implementation, debugging, and validation support.
- **Google Stitch:** dashboard and website design exploration.
- **Google Gemini:** runtime extraction of structured adherence signals from CRM
  notes.

The LLM is used only for extracting structured events from unstructured CRM
notes. It is not used to calculate Exhibit 2, determine PDC, assign the gap
bridge, or replace pharmacy data.

The extraction is grounded by:

- A fixed prompt with explicit field definitions.
- A JSON response schema.
- Temperature zero.
- Labelled synthetic notes for evaluation.
- Evidence text returned with every extraction.
- Separate reporting of refill-derived agreement versus medication-taking
  behavior.

Known failure modes include hallucinated reasons, incorrect duration
extraction, incomplete evidence quotes, API timeouts, unavailable models, and
rate limits. The extractor surfaces API errors and writes a status file rather
than silently presenting a successful result.

## 8. Limitations and next steps

For a real client deployment, I would:

- Replace demo assumptions with approved client definitions for enrollment,
  observation windows, PDC, discontinuation, and CRM eligibility.
- Use de-identified or governed client data and apply least-privilege access.
- Add PHI controls, encryption, audit logging, retention rules, and vendor
  review before sending any text to an external LLM.
- Use a managed secrets store instead of a local `.env` file.
- Add retries with exponential backoff and quota-aware batching.
- Validate the Gemini model and endpoint during application startup.
- Add human review for low-confidence or clinically consequential extractions.
- Evaluate on a manually adjudicated sample, not only synthetic labels.
- Add a Shapley decomposition as an attribution-order sensitivity analysis.
- Add automated tests for CSV schema, metric reconciliation, and dashboard
  loading.

The current dashboard is a local review tool, not a production clinical
decision-support system.

## 9. Submission index

| File | Purpose | How to run or review |
|---|---|---|
| `generate_synthetic_patient_data.py` | Generates Synthea-based or 10,000-patient demo records | Run the demo command in Section 4 |
| `analyze_adherence_gap.py` | Calculates metrics and the sequential gap bridge | Run with `--input-dir .\data\generated` |
| `extract_note_signals.py` | Calls Gemini and evaluates labelled CRM-note extraction | Run with `--input-dir .\data\generated --sample-size 5` |
| `reconciliation_view.html` | Full browser reconciliation website | Serve with `python -m http.server 8000` |
| `requirements.txt` | Python dependencies | Run `python -m pip install -r requirements.txt` |
| `README.md` | Setup and operating instructions | Read before running |
| `data/generated/patient_records.csv` | Patient-level synthetic records | Generated output |
| `data/generated/pharmacy_hub_fills.csv` | Pharmacy fill-level records | Generated output |
| `data/generated/crm_case_manager_notes.csv` | CRM notes and labels | Generated output |
| `data/generated/exhibit_2_metrics.csv` | Exhibit 2 figures | Generated output |
| `data/generated/adherence_gap_bridge.csv` | Machine-readable bridge | Generated output |
| `data/generated/adherence_gap_bridge.md` | Human-readable bridge | Generated output |
| `data/generated/note_extraction_results.csv` | Gemini note-level results | Generated after Task 3 run |
| `data/generated/note_extraction_metrics.json` | Task 3 metrics | Generated after Task 3 run |
| `data/generated/note_extraction_status.json` | Gemini completion/quota status | Generated after Task 3 run |

To open the website:

```powershell
python -m http.server 8000
```

Then visit `http://localhost:8000/reconciliation_view.html`.

## 10. Answers to the five case-study points

### Point 1 — Generate realistic synthetic patient-level data

Completed with Synthea-compatible input handling and a deterministic
10,000-patient demo mode. The output includes patient identifiers, enrollment
and medication dates, refills, supply days, hub fills, CRM notes, adherence,
missed doses, delays, and discontinuation reasons. Synthea is the preferred
source; the demo mode preserves its source-shaped schema when Synthea Java
execution is unavailable.

### Point 2 — Calculate both metrics and explain the gap

Completed using the original definitions:

- Pipeline: all enrolled patients with pharmacy-fill PDC >=80%.
- CRM: patients with at least one specialty-pharmacy fill, using the
  case-manager refill-on-schedule flag.

The sequential bridge reconciles the CRM-minus-pipeline gap. For this quarter,
the gap is +13.0 percentage points; for last quarter, it is +8.0 points.
Population, definition, and source contributions are separately reported.

### Point 3 — Extract information from free-text CRM notes with an LLM

Completed with Gemini structured JSON extraction for missed doses, missed
duration, missed-dose reasons, refill delays, delay reasons, discontinuation,
discontinuation reasons, and evidence. The evaluation reports precision,
recall, F1, agreement, reason accuracy, strict exact-record accuracy, and
normalized exact-record accuracy.

The results are reported honestly: missed-dose F1 was 100%, refill-delay F1
was 85.7%, discontinuation F1 was 0% on a sample with no positive
discontinuation cases, and exact-record accuracy was 20%. Refill-derived
evidence is not treated as ground truth for actual medication-taking behavior.

### Point 4 — Build the reconciliation view

Completed in `reconciliation_view.html`. The website includes Exhibit 2,
quarter toggles, cohort and operational filters, a patient-level table, the
population/definition/source bridge, CRM-note extraction metrics, note-level
evidence, and Gemini quota-limit/reset status.

The browser never reads `.env` or calls Gemini directly. It only loads
generated CSV/JSON outputs from the local data folder.

### Task 4 — Unified adherence definition and data-quality implementation

The two existing systems should continue to show their operational measures,
but future primary reporting should use one approved pharmacy-based definition.
I propose:

> A patient is adherent when their pharmacy-based PDC is at least 80% during
> the approved quarterly measurement period.

The calculation is:

`PDC = eligible medication-covered days / eligible days in the period × 100`

The unified standard should use eligible enrolled patients who have initiated
the relevant therapy, an approved calendar quarter, and validated
specialty-pharmacy fill records as the primary source. Patients without
sufficient fill information should be reported as **insufficient data**, not
automatically classified as adherent or non-adherent. Discontinuations and
clinician-directed treatment holds should be handled through client-approved
eligibility and exclusion rules.

#### Implementation workflow

1. Identify eligible enrolled patients and therapy initiation dates.
2. Retrieve specialty-pharmacy fills within the approved measurement period.
3. Validate patient identifiers, fill dates, and days-supply values.
4. Remove duplicate transactions and resolve overlapping coverage without
   double-counting covered days.
5. Calculate patient-level PDC and classify PDC >=80% as adherent.
6. Keep insufficient-data patients in a separately reported category.
7. Aggregate the patient-level classifications into the quarterly metric.

CRM case-manager notes remain supporting evidence for missed doses, refill
delays, and discontinuation reasons. LLM extraction can structure those notes,
but it does not replace the pharmacy-based PDC calculation or convert
documentation into proof of medication consumption.

#### Automated data-quality checks

The production pipeline should check for duplicate patient IDs, duplicate
fills, missing identifiers, missing fill dates, zero/negative or implausible
days supply, overlapping coverage, unmatched hub and CRM patients, missing CRM
notes, stale notes, and unexpected quarter-over-quarter PDC changes. A
reconciliation check should confirm that patient-level classifications
reproduce the reported aggregate figures.

#### Monitoring and governance

Each quarter, compare eligible-patient counts, fill completeness, CRM-note
coverage, unmatched records, PDC classifications, and changes in population,
definition, or source. Material differences should create a data-quality
warning and be investigated before management or board reporting. This
standard creates a consistent, reproducible, and auditable primary measure
while preserving CRM information as useful operational context.

### Point 5 — Produce a defensible submission and explain limitations

Completed through this document, the generated bridge report, and the
dashboard. The submission states assumptions, sources, definitions, evaluation
results, weak discontinuation performance, exact-record limitations, CRM
population handling, refill-evidence limitations, API quota behavior, and
production next steps.

**Board recommendation:** Report 58% as the provisional pharmacy-based PDC
result and 71% separately as the CRM-reported refill-on-schedule indicator.
Disclose the 13-percentage-point difference and explain the definition and
data-source contributions. Adopt PDC >=80% as the proposed primary adherence
standard, subject to client approval and validation using real data.

The main conclusion is that the discrepancy is the combined result of
population eligibility, different adherence definitions, and differences
between pharmacy records and CRM documentation—not one unexplained data error.
