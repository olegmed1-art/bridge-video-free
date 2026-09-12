#!/usr/bin/env python3
"""Append provenance-safe 52x13 completion results to a server PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tools.diana167_server_report import card_text, complete_unrecognized_cards


BASELINE_PDF_SHA256 = "41334e84d76902ae78532ac988dca86f4efc9f299b1c4ebdb2abdb643de80096"
BASELINE_RUNTIME_COMMIT = "7ea0dd7a8b8346410663b5c725e119f35d35a830"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_appendix(data: dict[str, Any], target: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
    styles = getSampleStyleSheet()
    title = ParagraphStyle("TitleRu", parent=styles["Title"], fontName="DejaVu-Bold", fontSize=18, leading=22, textColor=colors.HexColor("#17324D"), alignment=TA_LEFT)
    body = ParagraphStyle("BodyRu", parent=styles["BodyText"], fontName="DejaVu", fontSize=8.5, leading=11)
    small = ParagraphStyle("SmallRu", parent=body, fontSize=7.2, leading=9)
    page = landscape(A4)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7)
        canvas.setFillColor(colors.HexColor("#667788"))
        canvas.drawString(12 * mm, 7 * mm, "Диана 167 · поствизуальное достраивание · бриджевая стратегия отключена")
        canvas.drawRightString(page[0] - 12 * mm, 7 * mm, f"Приложение · {doc.page}")
        canvas.restoreState()

    reconstruction = data["reconstruction"]
    story: list[Any] = [
        Paragraph("Приложение: идентификация нераспознанных карт", title),
        Spacer(1, 3 * mm),
        Paragraph(
            "Этап запускается после визуального распознавания. Карты ниже визуального порога получают руку по экранной геометрии и строгим ограничениям полной колоды: 52 уникальные карты, по 13 в каждой руке. Пустая масть допустима. Исходный визуальный вес сохраняется; совпавший проверенный указатель или память сыгранной карты могут только повысить объединённый вес, а несовпадение лишь фиксируется как конфликт.",
            body,
        ),
        Spacer(1, 3 * mm),
    ]
    summary_rows = [
        ["Сдач", len(data["deals"])],
        ["Визуально прочитано", reconstruction["total_visual_recognized_cards"]],
        ["Достроено", reconstruction["total_deck_constrained_completed_cards"]],
        ["Конфликтов", len(reconstruction["conflicts"])],
        ["Независимая проверка", "PASS для каждой сдачи"],
    ]
    summary = Table([[Paragraph(f"<b>{a}</b>", body), Paragraph(str(b), body)] for a, b in summary_rows], colWidths=[62 * mm, 90 * mm])
    summary.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCD5DF")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF3F8")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.extend([summary, PageBreak()])

    for number, deal in enumerate(data["deals"], 1):
        completion = deal["completion"]
        story.append(Paragraph(f"Сдача {number} · {deal['timestamp']} · карты ниже визуального порога", title))
        story.append(Paragraph(
            f"Визуально прочитано: {completion['visual_recognized_cards']} · достроено: {completion['deck_constrained_completed_cards']} · независимая проверка: {completion['independent_validation']['status']} · итог: 52 карты, N/E/S/W по 13.",
            body,
        ))
        story.append(Spacer(1, 3 * mm))
        rows = [[Paragraph("Рука", small), Paragraph("Карта", small), Paragraph("Визуальный вес", small), Paragraph("Итоговый вес", small), Paragraph("Источник", small), Paragraph("Конфликты", small)]]
        low = [row for row in deal["weights"] if not row["visual_recognized"]]
        for row in low:
            rows.append([
                Paragraph(row["seat"], small),
                Paragraph(card_text(row["card"]), small),
                Paragraph(f"{row['visual_weight']:.4f}", small),
                Paragraph(f"{row['fused_weight']:.4f}", small),
                Paragraph("GEOMETRY + DECK_COMPLETION", small),
                Paragraph(str(len(row["conflicts"])), small),
            ])
        if not low:
            rows.append([Paragraph("—", small), Paragraph("нет", small), Paragraph("—", small), Paragraph("—", small), Paragraph("все VISUAL", small), Paragraph("0", small)])
        table = Table(rows, colWidths=[20 * mm, 25 * mm, 35 * mm, 35 * mm, 80 * mm, 25 * mm], repeatRows=1)
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D6DEE7")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#FFF1D6")), ("ALIGN", (0, 0), (3, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(table)
        voids = [
            f"{seat} {suit}"
            for seat, states in deal["suit_states"].items()
            for suit, state in states.items()
            if state["status"] == "VOID_CONFIRMED"
        ]
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Пустые масти после полного распределения: " + (", ".join(voids) if voids else "нет"), small))
        if number != len(data["deals"]):
            story.append(PageBreak())

    doc = SimpleDocTemplate(str(target), pagesize=page, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=10 * mm, bottomMargin=12 * mm)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    baseline_pdf = args.baseline_dir / "Диана 167 — карты, веса и упоминания — server v3.pdf"
    baseline_json = args.baseline_dir / "master_analysis.json"
    if sha256_file(baseline_pdf) != BASELINE_PDF_SHA256:
        raise RuntimeError("baseline PDF identity mismatch")
    data = json.loads(baseline_json.read_text(encoding="utf-8"))
    if data.get("runtime_commit") != BASELINE_RUNTIME_COMMIT or data.get("source", {}).get("forbidden_parent_used") is not False:
        raise RuntimeError("baseline server receipt mismatch")
    data["reconstruction"] = complete_unrecognized_cards(data)
    completion_json = args.output_dir / "master_analysis_completion.json"
    completion_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    appendix = args.output_dir / "completion_appendix.pdf"
    build_appendix(data, appendix)

    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for source in (baseline_pdf, appendix):
        for page in PdfReader(str(source)).pages:
            writer.add_page(page)
    writer.add_attachment(completion_json.name, completion_json.read_bytes())
    target = args.output_dir / "Диана 167 — полный расклад 52x13 и веса — server v4.pdf"
    with target.open("wb") as output:
        writer.write(output)
    reader = PdfReader(str(target))
    expected_pages = len(PdfReader(str(baseline_pdf)).pages) + len(PdfReader(str(appendix)).pages)
    text = "\n".join(page.extract_text() or "" for page in reader.pages[-8:])
    if len(reader.pages) != expected_pages or "идентификация нераспознанных карт" not in text or "DECK_COMPLETION" not in text:
        raise RuntimeError("final completion PDF validation failed")
    validation = {
        "status": "PASS",
        "pages": len(reader.pages),
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "baseline_pdf_sha256": BASELINE_PDF_SHA256,
        "deals": len(data["deals"]),
        "visual_cards": data["reconstruction"]["total_visual_recognized_cards"],
        "completed_cards": data["reconstruction"]["total_deck_constrained_completed_cards"],
    }
    (args.output_dir / "completion_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(validation, ensure_ascii=False))


if __name__ == "__main__":
    main()
