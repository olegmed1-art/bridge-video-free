#!/usr/bin/env python3
"""Compare visual card recognition across Diana reports and build one PDF."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

RANKS = "AKQJT98765432"
SUITS = "HCDS"
SUIT_SYMBOLS = {"H": "♥", "C": "♣", "D": "♦", "S": "♠"}
SYMBOL_SUITS = {value: key for key, value in SUIT_SYMBOLS.items()}
DECK = [rank + suit for suit in SUITS for rank in RANKS]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def card_text(card: str) -> str:
    return ("10" if card[0] == "T" else card[0]) + SUIT_SYMBOLS[card[1]]


def read_analysis(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("deals"), list) else None


def analysis_identity(data: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(data.get("job_id") or ""),
        str((data.get("source") or {}).get("sha256") or ""),
        str(data.get("runtime_commit") or ""),
        str(data.get("created_at") or ""),
    )


def stats_for_analyses(values: Iterable[dict[str, Any]]) -> tuple[dict[str, Counter[str]], dict[str, Any]]:
    per_card = {card: Counter() for card in DECK}
    videos: dict[str, Any] = {}
    for data in values:
        source = data.get("source") or {}
        name = str(source.get("filename") or source.get("drive_file_id") or "UNKNOWN")
        video = videos.setdefault(name, {"deals": 0, "recognized": 0, "unrecognized": 0, "source_sha256": source.get("sha256")})
        for deal in data.get("deals") or []:
            video["deals"] += 1
            for row in deal.get("weights") or []:
                card = str(row.get("card") or "").upper().replace("10", "T")
                if card not in per_card:
                    continue
                recognized = bool(row.get("visual_recognized")) if "visual_recognized" in row else float(row.get("visual_weight", row.get("weight_median", -2))) >= 0.80
                key = "recognized" if recognized else "unrecognized"
                per_card[card][key] += 1
                video[key] += 1
    return per_card, videos


def extract_pdf_analyses(pdf: Path, destination: Path) -> tuple[list[dict[str, Any]], list[str]]:
    from pypdf import PdfReader

    destination.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(pdf))
    analyses: list[dict[str, Any]] = []
    attachment_names: list[str] = []
    for name, contents in reader.attachments.items():
        attachment_names.append(name)
        payloads = contents if isinstance(contents, list) else [contents]
        for index, payload in enumerate(payloads):
            if not isinstance(payload, bytes):
                continue
            target = destination / f"{pdf.stem}-{index}-{Path(name).name}"
            target.write_bytes(payload)
            candidate = read_analysis(target)
            if candidate is not None:
                analyses.append(candidate)
    return analyses, sorted(attachment_names)


def old_text_stats(pdf: Path) -> tuple[dict[str, Counter[str]], dict[str, Any]]:
    """Best-effort fallback for the legacy visual review without JSON receipts."""
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    stats = {card: Counter() for card in DECK}
    matches = 0
    pattern = re.compile(r"\b(A|K|Q|J|10|[2-9])\s*([♥♣♦♠HCDS])[^\n]{0,80}?(-?0\.\d{2,6})")
    for rank, suit, weight in pattern.findall(text):
        card = ("T" if rank == "10" else rank) + SYMBOL_SUITS.get(suit, suit)
        if card not in stats:
            continue
        stats[card]["recognized" if float(weight) >= 0.80 else "unrecognized"] += 1
        matches += 1
    return stats, {"method": "PDF_TEXT_WEIGHT_ROWS", "matched_card_rows": matches, "text_characters": len(text)}


def unique_analyses(paths: Iterable[Path]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    result = []
    for path in paths:
        data = read_analysis(path)
        if data is None:
            continue
        identity = analysis_identity(data)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(data)
    return result


def build_summary_pdf(comparison: dict[str, Any], target: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
    styles = getSampleStyleSheet()
    title = ParagraphStyle("TitleRu", parent=styles["Title"], fontName="DejaVu-Bold", fontSize=17, leading=21, textColor=colors.HexColor("#17324D"))
    body = ParagraphStyle("BodyRu", parent=styles["BodyText"], fontName="DejaVu", fontSize=8, leading=10)
    small = ParagraphStyle("SmallRu", parent=body, fontSize=6.5, leading=8)
    page = landscape(A4)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7)
        canvas.setFillColor(colors.HexColor("#667788"))
        canvas.drawString(12 * mm, 7 * mm, "Диана 23–26 · новый gold v2 против прежнего прогона")
        canvas.drawRightString(page[0] - 12 * mm, 7 * mm, f"Страница {doc.page}")
        canvas.restoreState()

    story: list[Any] = [Paragraph("Диана 23–26: сравнение распознавания карт", title), Spacer(1, 3 * mm)]
    new_totals = comparison["new_totals"]
    old_totals = comparison["old_totals"]
    overview = [
        ["Показатель", "Прежний прогон", "Новый прогон"],
        ["Источник/проверки", comparison["old_baseline"]["label"], "gold v2 + событийные кадры + поствизуальное достраивание"],
        ["Видео", str(old_totals.get("videos", "—")), str(new_totals["videos"])],
        ["Сдачи", str(old_totals.get("deals", "—")), str(new_totals["deals"])],
        ["Уверенно распознано", str(old_totals["recognized"]), str(new_totals["recognized"])],
        ["Не распознано визуально", str(old_totals["unrecognized"]), str(new_totals["unrecognized"])],
        ["Доля уверенных", old_totals["rate"], new_totals["rate"]],
    ]
    overview_table = Table([[Paragraph(f"<b>{x}</b>", body) if c == 0 or r == 0 else Paragraph(str(x), body) for c, x in enumerate(row)] for r, row in enumerate(overview)], colWidths=[58 * mm, 100 * mm, 100 * mm])
    overview_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C8D2DC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#EDF3F7")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [overview_table, Spacer(1, 4 * mm)]
    story.append(Paragraph("Новый прогон по видео", title))
    rows = [["Видео", "Сдачи", "Уверенно", "Не распознано", "Доля уверенных"]]
    for name, item in sorted(comparison["new_videos"].items()):
        total = item["recognized"] + item["unrecognized"]
        rate = f"{100 * item['recognized'] / total:.1f}%" if total else "—"
        rows.append([name, str(item["deals"]), str(item["recognized"]), str(item["unrecognized"]), rate])
    video_table = Table([[Paragraph(f"<b>{x}</b>", body) if r == 0 else Paragraph(str(x), body) for x in row] for r, row in enumerate(rows)], colWidths=[105 * mm, 30 * mm, 38 * mm, 45 * mm, 35 * mm])
    video_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C8D2DC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5EDF4")), ("ALIGN", (1, 1), (-1, -1), "CENTER")]))
    story += [video_table, Spacer(1, 4 * mm)]
    coverage = comparison["old_baseline"]
    story.append(Paragraph(f"Извлечение прежних данных: {coverage['method']}; строк карт: {coverage.get('matched_card_rows', 0)}; вложения: {', '.join(coverage.get('attachments', [])) or 'нет'}. Если старый PDF не содержит машиночитаемых весов, старые нули означают отсутствие извлекаемых данных, а не отсутствие ошибки.", small))
    story.append(PageBreak())
    story.append(Paragraph("Статистика по каждой карте", title))
    story.append(Paragraph("«Да» — карта визуально прошла порог; «нет» — осталась ниже порога. Поствизуальное достраивание не превращает слабую карту в визуально распознанную.", body))
    card_rows = [["Карта", "Старый: да", "Старый: нет", "Новый: да", "Новый: нет", "Изменение доли"]]
    for card in DECK:
        old = comparison["old_cards"][card]
        new = comparison["new_cards"][card]
        old_n = old["recognized"] + old["unrecognized"]
        new_n = new["recognized"] + new["unrecognized"]
        old_rate = old["recognized"] / old_n if old_n else None
        new_rate = new["recognized"] / new_n if new_n else None
        delta = "—" if old_rate is None or new_rate is None else f"{(new_rate - old_rate) * 100:+.1f} п.п."
        card_rows.append([card_text(card), str(old["recognized"]), str(old["unrecognized"]), str(new["recognized"]), str(new["unrecognized"]), delta])
    table = Table([[Paragraph(f"<b>{x}</b>", small) if r == 0 else Paragraph(str(x), small) for x in row] for r, row in enumerate(card_rows)], colWidths=[30 * mm, 35 * mm, 35 * mm, 35 * mm, 35 * mm, 45 * mm], repeatRows=1)
    commands = [("GRID", (0, 0), (-1, -1), 0.22, colors.HexColor("#D2DAE2")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("TOPPADDING", (0, 0), (-1, -1), 1.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4)]
    for row_number, card in enumerate(DECK, 1):
        if comparison["new_cards"][card]["unrecognized"]:
            commands.append(("BACKGROUND", (0, row_number), (-1, row_number), colors.HexColor("#FFF1D6")))
    table.setStyle(TableStyle(commands))
    story.append(table)
    doc = SimpleDocTemplate(str(target), pagesize=page, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=9 * mm, bottomMargin=12 * mm, title="Диана 23–26 — сравнение распознавателя", author="Bridge Video server recognizer")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def totals(stats: dict[str, Counter[str]], videos: int | None = None, deals: int | None = None) -> dict[str, Any]:
    recognized = sum(item["recognized"] for item in stats.values())
    unrecognized = sum(item["unrecognized"] for item in stats.values())
    total = recognized + unrecognized
    result: dict[str, Any] = {"recognized": recognized, "unrecognized": unrecognized, "rate": f"{100 * recognized / total:.1f}%" if total else "—"}
    if videos is not None:
        result["videos"] = videos
    if deals is not None:
        result["deals"] = deals
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new-root", required=True, type=Path)
    parser.add_argument("--old-pdf", required=True, type=Path)
    parser.add_argument("--history-root", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    new_analyses = unique_analyses(args.new_root.rglob("master_analysis.json"))
    if len(new_analyses) != 4:
        raise RuntimeError(f"expected four new analyses, got {len(new_analyses)}")
    new_stats, new_videos = stats_for_analyses(new_analyses)

    old_analyses, attachment_names = extract_pdf_analyses(args.old_pdf, args.output_dir / "old-attachments")
    if old_analyses:
        old_stats, old_videos = stats_for_analyses(old_analyses)
        old_meta = {"method": "EMBEDDED_MASTER_ANALYSIS_JSON", "attachments": attachment_names, "matched_card_rows": sum(sum(x.values()) for x in old_stats.values()), "label": "старые каталоги и старые проверки"}
        old_video_count, old_deals = len(old_videos), sum(item["deals"] for item in old_videos.values())
    else:
        old_stats, old_meta = old_text_stats(args.old_pdf)
        old_meta.update({"attachments": attachment_names, "label": "старые каталоги и старые проверки"})
        old_video_count, old_deals = None, None

    history_analyses = unique_analyses(args.history_root.rglob("*.json")) if args.history_root and args.history_root.exists() else []
    identities = {analysis_identity(item) for item in history_analyses}
    for item in new_analyses:
        if analysis_identity(item) not in identities:
            history_analyses.append(item)
            identities.add(analysis_identity(item))
    history_stats, history_videos = stats_for_analyses(history_analyses)

    comparison = {
        "schema": "diana-recognition-comparison/v1",
        "old_baseline": {**old_meta, "pdf_sha256": sha256(args.old_pdf)},
        "new_run_count": len(new_analyses),
        "new_videos": new_videos,
        "new_totals": totals(new_stats, len(new_videos), sum(item["deals"] for item in new_videos.values())),
        "old_totals": totals(old_stats, old_video_count, old_deals),
        "new_cards": {card: dict(new_stats[card]) | {"recognized": new_stats[card]["recognized"], "unrecognized": new_stats[card]["unrecognized"]} for card in DECK},
        "old_cards": {card: dict(old_stats[card]) | {"recognized": old_stats[card]["recognized"], "unrecognized": old_stats[card]["unrecognized"]} for card in DECK},
        "post_gold_v2_history": {
            "boundary_utc": "2026-09-11T21:49:40.004Z",
            "unique_runs": len(history_analyses),
            "videos": history_videos,
            "totals": totals(history_stats, len(history_videos), sum(item["deals"] for item in history_videos.values())),
            "cards": {card: {"recognized": history_stats[card]["recognized"], "unrecognized": history_stats[card]["unrecognized"]} for card in DECK},
        },
    }
    comparison_json = args.output_dir / "recognition_comparison.json"
    comparison_json.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_pdf = args.output_dir / "comparison-summary.pdf"
    build_summary_pdf(comparison, summary_pdf)

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for pdf in [summary_pdf, *sorted(args.new_root.rglob("*server v6.pdf"))]:
        for page in PdfReader(str(pdf)).pages:
            writer.add_page(page)
    writer.add_attachment("recognition_comparison.json", comparison_json.read_bytes())
    for path in sorted(args.new_root.rglob("master_analysis.json")):
        writer.add_attachment(f"{path.parent.name}-master_analysis.json", path.read_bytes())
    target = args.output_dir / "Диана 23–26 — новый прогон и сравнение — server v6.pdf"
    with target.open("wb") as handle:
        writer.write(handle)
    print(json.dumps({"status": "PASS", "pdf": str(target), "comparison": str(comparison_json), "new": comparison["new_totals"], "old": comparison["old_totals"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
