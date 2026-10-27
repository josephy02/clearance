from clearance.engine.pack import load_pack
from clearance.engine.types import OcrLine, Page
from clearance.extract.claude import _same
from clearance.extract.fields import OcrFieldExtractor, _find_label, parse_date, parse_value

OXY = load_pack("medicare-oxygen-home")
PAP = load_pack("medicare-pap-osa-initial")


def line(text, conf=0.98, bbox=(0.1, 0.1, 0.5, 0.12)):
    return OcrLine(text=text, confidence=conf, bbox=list(bbox))


def test_dates_survive_ocr_noise():
    assert parse_date("Apri1.25,2026").isoformat() == "2026-04-25"
    assert parse_date("May9.2026.").isoformat() == "2026-05-09"
    assert parse_date("August:2,. 2026").isoformat() == "2026-08-02"
    assert parse_date("12-Mar-2026").isoformat() == "2026-03-12"


def test_numbers_repair_confusable_digits():
    assert parse_value(PAP.field("ahi_or_rdi"), "1l.l events/hour") == (11.1, 0.85)
    assert parse_value(PAP.field("total_events"), ".225")[0] == 225


def test_labels_tolerate_ocr_edits_and_stray_colons():
    assert _find_label(line("Date:of Visit: 01/18/2026"), "Date of Visit")
    assert _find_label(line("Item n Ordered: E0601 CPAP"), "Item Ordered")
    assert _find_label(line("Prescriber NPI: 1234567893"), "Prescriber") is None


def test_extractor_reads_by_pack_labels():
    page = Page(number=1, lines=[
        line("Sleep Study Interpretation", bbox=(0.1, 0.05, 0.5, 0.07)),
        line("Polysomnography report", bbox=(0.1, 0.08, 0.5, 0.09)),
        line("AHI:", bbox=(0.1, 0.3, 0.15, 0.32)),
        line("22.4 /hr", bbox=(0.16, 0.3, 0.25, 0.32)),
    ])
    ex = OcrFieldExtractor().extract(PAP, [page])
    assert page.document_type == "sleep_study_report"
    assert ex.fields["ahi_or_rdi"].value == 22.4
    assert ex.fields["f2f_date"].status == "missing"


def test_two_readers_agree_despite_ocr_noise_but_ids_stay_exact():
    item = OXY.field("item_ordered")
    assert _same(item, "E1390 Oxygen concentrator, stationary; E0431 portable gaseous system",
                 "E1390 0 Oxygen concentrator, stationary; E043l portable gaseous .system")
    assert not _same(OXY.field("medicare_id"), "1AA2CC3DD45", "1AA2CC3DD46")
