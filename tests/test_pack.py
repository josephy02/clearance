import pytest

from clearance.engine.pack import PackError, list_packs, load_pack, parse_pack

MINIMAL = """
pack_id: t
version: 0.0.1
item: {name: thing, hcpcs: [X0000]}
payer: test
sources: [{id: lcd, title: LCD, url: https://example.org}]
extraction:
  document_types: [{id: order, title: Order}]
  fields:
    - {name: ahi, type: number}
    - {name: when, type: date}
rules:
%s
"""


def test_shipped_packs_load():
    ids = {p.pack_id for p in list_packs()}
    assert "medicare-pap-osa-initial" in ids


def test_rule_fields_cover_nested_rules():
    pack = load_pack("medicare-pap-osa-initial")
    assert {"ahi_or_rdi", "total_events", "ordering_npi", "medicare_id"} <= pack.rule_fields()


def test_unknown_field_suggests_close_match():
    rules = "  - {id: r, type: numeric, field: ahii, op: '>=', value: 1, source: lcd, on_fail: needs_info}"
    with pytest.raises(PackError) as err:
        parse_pack(MINIMAL % rules)
    assert "field 'ahii' is not defined" in str(err.value)
    assert "did you mean 'ahi'" in str(err.value)


def test_type_mismatch_is_reported():
    rules = "  - {id: r, type: numeric, field: when, op: '>=', value: 1, source: lcd, on_fail: needs_info}"
    with pytest.raises(PackError, match="'when' is date, but numeric needs a number"):
        parse_pack(MINIMAL % rules)


def test_top_level_rules_need_id_source_and_on_fail():
    rules = "  - {type: present, field: ahi}"
    with pytest.raises(PackError) as err:
        parse_pack(MINIMAL % rules)
    text = str(err.value)
    assert "need an 'id'" in text and "cite a 'source'" in text and "'on_fail'" in text


def test_unknown_source_and_rule_type():
    rules = "  - {id: r, type: present, field: ahi, source: lcdd, on_fail: needs_info}"
    with pytest.raises(PackError, match="unknown source 'lcdd'"):
        parse_pack(MINIMAL % rules)
    with pytest.raises(PackError):
        parse_pack(MINIMAL % "  - {id: r, type: magic, field: ahi, source: lcd, on_fail: needs_info}")


def test_missing_pack_lists_available():
    with pytest.raises(PackError, match="available: .*medicare-pap-osa-initial"):
        load_pack("nope")


def test_bad_yaml():
    with pytest.raises(PackError, match="not valid YAML"):
        parse_pack("a: [", "x.yaml")
