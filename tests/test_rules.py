from clearance.engine.pack import parse_pack
from clearance.engine.router import route
from clearance.engine.rules import evaluate, evaluate_all
from clearance.engine.types import ExtractedField, FieldStatus, Outcome, Route
from clearance.integrations.npi import names_match, npi_check_digit, npi_is_valid

from .conftest import StubCheck, make_extraction


def by_id(results):
    return {r.rule_id: r for r in results}


def test_clean_order_is_ready(pap, ctx_factory):
    results = evaluate_all(ctx_factory(make_extraction()))
    assert {r.outcome for r in results} == {Outcome.PASS}
    assert route(pap, results).route == Route.READY


def test_high_ahi_never_calls_the_judge(pap, ctx_factory):
    class Boom:
        model = "boom"

        def ask(self, *a):
            raise AssertionError("judge should not run when AHI >= 15")

    results = by_id(evaluate_all(ctx_factory(make_extraction(), judge=Boom())))
    q = results["qualifying_sleep_test"]
    assert q.outcome == Outcome.PASS
    assert q.children[1].outcome == Outcome.SKIPPED


def test_unsigned_order_needs_info(pap, ctx_factory):
    results = evaluate_all(ctx_factory(make_extraction({"order_signed": False})))
    r = route(pap, results)
    assert r.route == Route.NEEDS_INFO
    assert by_id(results)["order_signed"].outcome == Outcome.FAIL


def test_f2f_after_test(pap, ctx_factory):
    results = by_id(evaluate_all(ctx_factory(make_extraction({"f2f_date": "2026-02-25"}))))
    assert results["f2f_before_test"].outcome == Outcome.FAIL
    assert "not dated before" in results["f2f_before_test"].message


def test_mild_osa_with_documented_symptom_passes_with_quote(pap, ctx_factory):
    note = "HPI: Patient reports loud snoring.\nShe admits excessive daytime sleepiness at work."
    ex = make_extraction({"ahi_or_rdi": 9.2, "total_events": 48}, note=note)
    results = by_id(evaluate_all(ctx_factory(ex)))
    q = results["qualifying_sleep_test"]
    assert q.outcome == Outcome.PASS and q.llm_involved
    judgment = q.children[1].children[2]
    assert "daytime sleepiness" in judgment.evidence[0].quote


def test_mild_osa_without_symptom_goes_to_human(pap, ctx_factory):
    ex = make_extraction({"ahi_or_rdi": 9.2, "total_events": 48},
                         note="Patient denies daytime sleepiness. No hypertension.")
    results = evaluate_all(ctx_factory(ex))
    assert by_id(results)["qualifying_sleep_test"].outcome == Outcome.FAIL
    r = route(pap, results)
    assert r.route == Route.HUMAN_REVIEW          # never_auto: llm_judgment_failures
    assert r.needs_info                           # outreach still drafted for the reviewer


def test_too_few_events(pap, ctx_factory):
    results = by_id(evaluate_all(ctx_factory(make_extraction({"ahi_or_rdi": 18,
                                                              "total_events": 22}))))
    assert results["qualifying_sleep_test"].outcome == Outcome.FAIL


def test_missing_sleep_study_fields_need_info(pap, ctx_factory):
    ex = make_extraction({"ahi_or_rdi": None, "total_events": None, "sleep_test_date": None,
                          "sleep_test_type": None})
    results = by_id(evaluate_all(ctx_factory(ex)))
    q = results["qualifying_sleep_test"]
    assert q.outcome == Outcome.FAIL
    assert "ahi_or_rdi" in q.detail["missing_fields"]
    assert route(pap, list(results.values())).route == Route.NEEDS_INFO


def test_illegible_field_is_unverifiable_not_guessed(pap, ctx_factory):
    blurry = ExtractedField(name="ahi_or_rdi", value=8.0, confidence=0.3,
                            status=FieldStatus.FOUND, page=3)
    results = evaluate_all(ctx_factory(make_extraction(overrides={"ahi_or_rdi": blurry})))
    assert by_id(results)["qualifying_sleep_test"].outcome == Outcome.UNVERIFIABLE
    assert route(pap, results).route == Route.HUMAN_REVIEW


def test_confidence_between_floor_and_bar_blocks_auto_ready(pap, ctx_factory):
    results = evaluate_all(ctx_factory(make_extraction(conf=0.9)))
    assert {r.outcome for r in results} == {Outcome.PASS}
    r = route(pap, results)
    assert r.route == Route.HUMAN_REVIEW and r.min_confidence == 0.9
    assert route(pap, results, threshold=0.85).route == Route.READY


def test_bad_npi_check_digit_skips_registry(pap, ctx_factory):
    provider = StubCheck()
    results = by_id(evaluate_all(ctx_factory(make_extraction({"ordering_npi": "1234567890"}),
                                             provider=provider)))
    assert results["provider_active"].outcome == Outcome.FAIL
    assert provider.calls == []


def test_inactive_eligibility_routes_to_human(pap, ctx_factory):
    results = evaluate_all(ctx_factory(make_extraction(),
                                       eligibility=StubCheck(ok=False, message="inactive")))
    assert route(pap, results).route == Route.HUMAN_REVIEW


def test_unreachable_service_is_unverifiable(pap, ctx_factory):
    results = by_id(evaluate_all(ctx_factory(make_extraction(), provider=StubCheck(ok=None))))
    assert results["provider_active"].outcome == Outcome.UNVERIFIABLE


def test_bad_mbi_format(pap, ctx_factory):
    results = by_id(evaluate_all(ctx_factory(make_extraction({"medicare_id": "123-45-6789A"}))))
    assert results["mbi_format"].outcome == Outcome.FAIL


GENERIC = """
pack_id: g
version: 0.0.1
item: {name: thing, hcpcs: [X0000]}
payer: test
sources: [{id: s, title: S, url: https://example.org}]
extraction:
  document_types: [{id: d, title: D}]
  fields:
    - {name: a, type: date}
    - {name: b, type: date}
    - {name: kind, type: enum, values: [rest, sleep, exercise]}
    - {name: spo2, type: number}
rules:
  - {id: window, type: date_window, a: a, b: b, min_days: 0, max_days: 30, source: s, on_fail: needs_info}
  - {id: kind_ok, type: in, field: kind, values: [rest, sleep], source: s, on_fail: needs_info}
  - {id: low_sat, type: numeric, field: spo2, op: "<=", value: 88, source: s, on_fail: needs_info}
"""


def test_generic_rule_types(ctx_factory):
    pack = parse_pack(GENERIC)
    ex = make_extraction({"a": "2026-01-01", "b": "2026-01-20", "kind": "REST", "spo2": 87})
    out = {r.rule_id: r.outcome for r in evaluate_all(ctx_factory(ex, pack=pack))}
    assert out == {"window": "pass", "kind_ok": "pass", "low_sat": "pass"}
    ex = make_extraction({"a": "2026-01-01", "b": "2026-03-20", "kind": "exercise", "spo2": 90})
    out = {r.rule_id: r.outcome for r in evaluate_all(ctx_factory(ex, pack=pack))}
    assert out == {"window": "fail", "kind_ok": "fail", "low_sat": "fail"}


def test_rule_message_overrides_only_on_fail(pap, ctx_factory):
    rule = next(r for r in pap.rules if r.id == "order_signed")
    ok = evaluate(rule, ctx_factory(make_extraction()))
    assert "not signed" not in ok.message


def test_npi_helpers():
    assert npi_is_valid("1234567893") and not npi_is_valid("1234567890")
    assert npi_is_valid("123456789" + npi_check_digit("123456789"))
    assert names_match("Dr. Dana Whitfield, MD", "Dana Whitfield")
    assert names_match("D. Whitfield", "Dana Whitfield")
    assert not names_match("Dana Smith", "Dana Whitfield")
