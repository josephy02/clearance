# Evaluation report — 2026-10-26

All orders are synthetic (`SYNTHETIC – TEST DATA`). Expected outcomes come from planted defects in the generator, not from the engine.

## medicare-pap-osa-initial v1.0.0 (40 orders)

Models: extractor `claude-opus-5-5+ocr`, judge `claude-opus-5-5`. Auto-ready threshold 0.92.

| Metric | Value |
|---|---|
| Field extraction accuracy | 100.0% |
| … on clean faxes | 100.0% |
| … on heavy faxes | 100.0% |
| … on light faxes | 100.0% |
| Page classification accuracy | 100.0% |
| Defect recall (all rules) | 100.0% |
| Routing accuracy | 100.0% |
| Orders wrongly auto-routed READY | 0  |
| LLM-judgment quotes found verbatim | 100.0% (7 quotes) |
| Latency p50 / p95 | 12.20s / 14.43s |
| Model cost per order | $0.0675 |

### Per-rule precision / recall (positive = rule should flag)

| Rule | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| `order_signed` | 4 | 0 | 0 | 100.0% | 100.0% |
| `order_elements` | 0 | 0 | 0 | n/a | n/a |
| `mbi_format` | 0 | 0 | 0 | n/a | n/a |
| `f2f_before_test` | 7 | 0 | 0 | 100.0% | 100.0% |
| `qualifying_sleep_test` | 8 | 0 | 0 | 100.0% | 100.0% |
| `provider_active` | 3 | 0 | 0 | 100.0% | 100.0% |
| `coverage_active` | 3 | 0 | 0 | 100.0% | 100.0% |

### Routing confusion matrix (rows = expected)

| expected \ got | ready | needs_info | human_review |
|---|---|---|---|
| ready | 20 | 0 | 0 |
| needs_info | 0 | 9 | 0 |
| human_review | 0 | 0 | 11 |

### Field accuracy by field

| Field | Accuracy |
|---|---|
| `patient_name` | 100.0% |
| `patient_dob` | 100.0% |
| `medicare_id` | 100.0% |
| `ordering_provider_name` | 100.0% |
| `ordering_npi` | 100.0% |
| `item_ordered` | 100.0% |
| `order_date` | 100.0% |
| `order_signed` | 100.0% |
| `f2f_date` | 100.0% |
| `sleep_test_date` | 100.0% |
| `sleep_test_type` | 100.0% |
| `ahi_or_rdi` | 100.0% |
| `total_events` | 100.0% |

### Automation rate vs. threshold

![threshold sweep](threshold_pap_claude.png)

| Threshold | Auto-routed | Wrong among auto-routed | READY recall |
|---|---|---|---|
| 0.80 | 50.0% | 0.0% | 100.0% |
| 0.81 | 50.0% | 0.0% | 100.0% |
| 0.82 | 50.0% | 0.0% | 100.0% |
| 0.83 | 50.0% | 0.0% | 100.0% |
| 0.84 | 50.0% | 0.0% | 100.0% |
| 0.85 | 50.0% | 0.0% | 100.0% |
| 0.86 | 50.0% | 0.0% | 100.0% |
| 0.87 | 50.0% | 0.0% | 100.0% |
| 0.88 | 50.0% | 0.0% | 100.0% |
| 0.89 | 50.0% | 0.0% | 100.0% |
| 0.90 | 50.0% | 0.0% | 100.0% |
| 0.91 | 50.0% | 0.0% | 100.0% |
| 0.92 | 50.0% | 0.0% | 100.0% |
| 0.93 | 50.0% | 0.0% | 100.0% |
| 0.94 | 50.0% | 0.0% | 100.0% |
| 0.95 | 50.0% | 0.0% | 100.0% |
| 0.96 | 27.5% | 0.0% | 55.0% |
| 0.97 | 15.0% | 0.0% | 30.0% |
| 0.98 | 0.0% | 0.0% | 0.0% |
| 0.99 | 0.0% | 0.0% | 0.0% |

### Misses


## medicare-oxygen-home v1.0.0 (20 orders)

Models: extractor `claude-opus-5-5+ocr`, judge `claude-opus-5-5`. Auto-ready threshold 0.92.

| Metric | Value |
|---|---|
| Field extraction accuracy | 100.0% |
| … on clean faxes | 100.0% |
| … on heavy faxes | 100.0% |
| … on light faxes | 100.0% |
| Page classification accuracy | 100.0% |
| Defect recall (all rules) | 100.0% |
| Routing accuracy | 85.0% |
| Orders wrongly auto-routed READY | 0  |
| LLM-judgment quotes found verbatim | 100.0% (3 quotes) |
| Latency p50 / p95 | 12.32s / 13.66s |
| Model cost per order | $0.0702 |

### Per-rule precision / recall (positive = rule should flag)

| Rule | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| `order_signed` | 1 | 0 | 0 | 100.0% | 100.0% |
| `order_elements` | 0 | 0 | 0 | n/a | n/a |
| `mbi_format` | 0 | 0 | 0 | n/a | n/a |
| `qualifying_blood_gas` | 5 | 2 | 0 | 71.4% | 100.0% |
| `qualified_tester` | 2 | 0 | 0 | 100.0% | 100.0% |
| `test_before_order` | 1 | 0 | 0 | 100.0% | 100.0% |
| `provider_active` | 1 | 0 | 0 | 100.0% | 100.0% |
| `coverage_active` | 0 | 0 | 0 | n/a | n/a |

### Routing confusion matrix (rows = expected)

| expected \ got | ready | needs_info | human_review |
|---|---|---|---|
| ready | 7 | 0 | 3 |
| needs_info | 0 | 7 | 0 |
| human_review | 0 | 0 | 3 |

### Field accuracy by field

| Field | Accuracy |
|---|---|
| `patient_name` | 100.0% |
| `patient_dob` | 100.0% |
| `medicare_id` | 100.0% |
| `ordering_provider_name` | 100.0% |
| `ordering_npi` | 100.0% |
| `item_ordered` | 100.0% |
| `order_date` | 100.0% |
| `order_signed` | 100.0% |
| `test_date` | 100.0% |
| `test_condition` | 100.0% |
| `on_room_air` | 100.0% |
| `spo2` | 100.0% |
| `po2` | 100.0% |
| `baseline_spo2` | 100.0% |
| `test_performer` | 100.0% |
| `hematocrit` | 100.0% |

### Automation rate vs. threshold

![threshold sweep](threshold_oxygen_claude.png)

| Threshold | Auto-routed | Wrong among auto-routed | READY recall |
|---|---|---|---|
| 0.80 | 40.0% | 0.0% | 80.0% |
| 0.81 | 40.0% | 0.0% | 80.0% |
| 0.82 | 40.0% | 0.0% | 80.0% |
| 0.83 | 40.0% | 0.0% | 80.0% |
| 0.84 | 40.0% | 0.0% | 80.0% |
| 0.85 | 40.0% | 0.0% | 80.0% |
| 0.86 | 40.0% | 0.0% | 80.0% |
| 0.87 | 40.0% | 0.0% | 80.0% |
| 0.88 | 40.0% | 0.0% | 80.0% |
| 0.89 | 40.0% | 0.0% | 80.0% |
| 0.90 | 40.0% | 0.0% | 80.0% |
| 0.91 | 35.0% | 0.0% | 70.0% |
| 0.92 | 35.0% | 0.0% | 70.0% |
| 0.93 | 35.0% | 0.0% | 70.0% |
| 0.94 | 30.0% | 0.0% | 60.0% |
| 0.95 | 30.0% | 0.0% | 60.0% |
| 0.96 | 20.0% | 0.0% | 40.0% |
| 0.97 | 5.0% | 0.0% | 10.0% |
| 0.98 | 0.0% | 0.0% | 0.0% |
| 0.99 | 0.0% | 0.0% | 0.0% |

### Misses

- `oxy-009` (heavy, defects: group2_with_condition): expected **ready**, got **human_review**; rule mismatches: qualifying_blood_gas pass→fail
- `oxy-013` (clean, defects: none): expected **ready**, got **human_review**
- `oxy-019` (clean, defects: group2_with_condition): expected **ready**, got **human_review**; rule mismatches: qualifying_blood_gas pass→unverifiable
