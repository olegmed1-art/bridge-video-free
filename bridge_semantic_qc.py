"""Semantic post-ASR QC for Bridge Video 3.1 FREE.

Preserves raw ASR, creates analysis_text for downstream semantic analysis,
and records every automatic correction/candidate with confidence and risk.
No external/paid services.
"""
from __future__ import annotations
import re
from difflib import SequenceMatcher

SEMANTIC_QC_REVISION = "bridge-semantic-qc-r1"

# Narrow, evidence-based rules from real school recordings. They are intentionally
# not a generic autocorrect: every rewrite is recorded and raw ASR is preserved.
RULES = [
    dict(id="pass_paz", pattern=r"\bпаз\b", replacement="пас",
         contexts=("торгов", "заяв", "открыва", "дилер", "пас"), confidence=.99, critical=True,
         basis="Observed ASR confusion: «паз» in bidding context."),
    dict(id="pass_pasuyem", pattern=r"\bпосуем\b", replacement="пасуем",
         contexts=("очк", "торгов", "карта", "слаб"), confidence=.99, critical=True,
         basis="Observed ASR confusion: «посуем» → «пасуем»."),
    dict(id="points_tysyachkov", pattern=r"(?P<n>\b\d{1,2})\s+тысячков\b", replacement=r"\g<n> очков",
         contexts=("откры", "торгов", "отвеч", "диапазон"), confidence=.99, critical=True,
         basis="Observed ASR confusion after bridge point count."),
    dict(id="invite_mit", pattern=r"\bмит\b", replacement="инвит",
         contexts=("11", "12", "минимум", "максимум", "диапазон"), confidence=.94, critical=True,
         basis="Observed ASR confusion in hand-strength range explanation."),
    dict(id="game_prize_gen", pattern=r"\bпризовой\s+игры\b", replacement="гейма",
         contexts=("25 оч", "заказ", "двоих", "баланс"), confidence=.96, critical=True,
         basis="Observed ASR confusion near 25-point game discussion."),
    dict(id="game_prize_acc", pattern=r"\bпризовую\s+игру\b", replacement="гейм",
         contexts=("25 оч", "заказ", "двоих", "баланс"), confidence=.96, critical=True,
         basis="Observed ASR confusion near 25-point game discussion."),
    dict(id="game_prize_nom", pattern=r"\bпризовая\s+игра\b", replacement="гейм",
         contexts=("25 оч", "заказ", "двоих", "баланс"), confidence=.96, critical=True,
         basis="Observed ASR confusion near 25-point game discussion."),
    dict(id="three_nt_latin_c", pattern=r"\b3\s*[нn][cс]\b", replacement="3БК",
         contexts=("максим", "заказ", "без коз", "контракт", "очк"), confidence=.97, critical=True,
         basis="Normalize spoken/ASR 3NT notation to school notation 3БК."),
    dict(id="game_gi", pattern=r"\bзаказываем\s+ги\b", replacement="заказываем гейм",
         contexts=("максим", "диапазон", "очк"), confidence=.97, critical=True,
         basis="Observed clipped ASR of «гейм»."),
    dict(id="ace_lead_stuza", pattern=r"\bход\s+стуза\b", replacement="ход с туза",
         contexts=(), confidence=.99, critical=True,
         basis="Observed ASR merge of «с туза»."),
    dict(id="ace_king_garbled", pattern=r"\b(?:туск[а-]?роль|туск-роль|туска-роль|пускороль|тускороль)\b",
         replacement="туз-король", contexts=(), confidence=.99, critical=True,
         basis="Repeated observed corruption of «туз-король»."),
    dict(id="notrump_beskazyr", pattern=r"\bбесказырн(?P<e>ом|ый|ого|ому|ые|ых|ая|ую)\b",
         replacement=r"бескозырн\g<e>", contexts=("контракт", "игр", "ход", "взят"), confidence=.99, critical=False,
         basis="Observed phonetic corruption of «бескозырный»."),
    dict(id="notrump_bezkozy", pattern=r"\b(?:безкозы|без\s+козля)\b", replacement="без козыря",
         contexts=("три", "игра", "контракт", "взят", "треф", "буб"), confidence=.98, critical=True,
         basis="Observed corruption of «без козыря»."),
    dict(id="minor_minule", pattern=r"\bминуле\b", replacement="миноре",
         contexts=("открыва", "буб", "треф", "мажор"), confidence=.99, critical=True,
         basis="Observed ASR confusion of bridge term «минор»."),
    dict(id="clubs_trifovy", pattern=r"\bтрифов(?P<e>ый|ого|ому|ые|ых|ая|ую)\b",
         replacement=r"трефов\g<e>", contexts=("гейм", "контракт", "пят", "масть"), confidence=.99, critical=True,
         basis="Observed ASR corruption of «трефовый»."),
    dict(id="clubs_pyat_trev", pattern=r"\bпять\s+трев\b", replacement="пять треф",
         contexts=("гейм", "игра", "контракт", "взят"), confidence=.99, critical=True,
         basis="Observed ASR corruption of suit name «треф»."),
    dict(id="double_finesse_passport", pattern=r"\bдвойн(?:ой|ого|ому)\s+паспорт\b", replacement="двойной импас",
         contexts=("валет", "дам", "десятк", "буб", "пик", "черв", "треф"), confidence=.99, critical=True,
         basis="Observed ASR corruption of «двойной импас»."),
    dict(id="jack_valed", pattern=r"\bвалед\b", replacement="валет",
         contexts=("дам", "корол", "туз", "десятк", "карт", "пик", "буб", "черв", "треф"), confidence=.99, critical=True,
         basis="Observed ASR corruption of card honor «валет»."),
]

# Observed errors that are too ambiguous to rewrite silently.
CANDIDATE_RULES = [
    dict(id="discard_smosit", pattern=r"\bсмосить\b", candidate="снести",
         contexts=("пику", "черв", "буб", "треф", "старш"), confidence=.82, critical=True,
         basis="Possible ASR corruption of play action; requires audio/visual context."),
    dict(id="suit_gubu", pattern=r"\bстаршую\s+губу\b", candidate="старшую бубну",
         contexts=("пику", "снести", "старш"), confidence=.82, critical=True,
         basis="Possible suit-name corruption; must not become FACT without recheck."),
    dict(id="ace_trump_tuskozyrny", pattern=r"\bтускозырн\w*\b", candidate="туз / козырный — требуется прослушивание",
         contexts=("взят", "козыр", "отдаем"), confidence=.55, critical=True,
         basis="Garbled card/contract phrase; insufficient evidence for deterministic rewrite."),
]

CANONICAL_FUZZY = (
    "пас","пасуем","очки","очков","инвит","гейм","туз","король","дама","валет","десятка",
    "бескозырный","бескозырном","минор","мажор","трефа","трефовый","бубна","черви","пики",
    "импас","экспас","контра","реконтра","контракт","дилер","открывающий","отвечающий",
    "разыгрывающий","фоска","синглет","ренонс",
)


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def _context_ok(context, needles):
    if not needles:
        return True
    low = context.lower()
    return any(n.lower() in low for n in needles)


def _apply_rule(text, context, rule):
    if not _context_ok(context, rule.get("contexts")):
        return text, []
    events = []
    rx = re.compile(rule["pattern"], re.I)

    def repl(match):
        replacement = match.expand(rule["replacement"])
        events.append({
            "rule_id": rule["id"],
            "raw": match.group(0),
            "replacement": replacement,
            "confidence": rule["confidence"],
            "critical_bridge_meaning": bool(rule.get("critical")),
            "basis": rule["basis"],
            "status": "AUTO_CORRECTED",
        })
        return replacement

    return rx.sub(repl, text), events


def _candidate_events(text, context):
    out = []
    for rule in CANDIDATE_RULES:
        if not _context_ok(context, rule.get("contexts")):
            continue
        for match in re.finditer(rule["pattern"], text, re.I):
            out.append({
                "rule_id": rule["id"],
                "raw": match.group(0),
                "candidate": rule["candidate"],
                "confidence": rule["confidence"],
                "critical_bridge_meaning": bool(rule.get("critical")),
                "basis": rule["basis"],
                "status": "UNRESOLVED_CANDIDATE",
            })
    return out


def _fuzzy_candidates(text):
    # Advisory only: fuzzy matching never changes the transcript.
    out = []
    canonical_lower = {x.lower() for x in CANONICAL_FUZZY}
    tokens = re.findall(r"[А-Яа-яЁёA-Za-z-]{4,}", text.lower())
    for token in tokens:
        if token in canonical_lower:
            continue
        best = None
        for canonical in CANONICAL_FUZZY:
            score = SequenceMatcher(None, token, canonical.lower()).ratio()
            if score >= .80 and (best is None or score > best[0]):
                best = (score, canonical)
        if best and token != best[1].lower():
            out.append({
                "rule_id": "fuzzy_bridge_vocabulary",
                "raw": token,
                "candidate": best[1],
                "confidence": round(best[0], 3),
                "critical_bridge_meaning": False,
                "basis": "Token is close to known bridge vocabulary; advisory only.",
                "status": "FUZZY_CANDIDATE",
            })
    unique = {}
    for event in out:
        unique[(event["raw"], event["candidate"])] = event
    return list(unique.values())


def semantic_normalize_segments(segments):
    """Return copied segments and semantic-QC summary.

    `raw_text`/`text` preserve provenance. `analysis_text` is the deterministic
    normalized layer used downstream. Unresolved critical candidates make the
    segment unreliable and may never be promoted to FACT automatically.
    """
    src = [dict(s) for s in segments]
    count = len(src)
    all_events = []
    for i, segment in enumerate(src):
        raw = _norm(segment.get("text", ""))
        context = " ".join(_norm(src[j].get("text", "")) for j in range(max(0, i - 1), min(count, i + 2)))
        normalized = raw
        corrections = []
        for rule in RULES:
            normalized, events = _apply_rule(normalized, context, rule)
            corrections.extend(events)
        candidates = _candidate_events(raw, context)
        fuzzy = _fuzzy_candidates(raw)
        segment["raw_text"] = raw
        segment["analysis_text"] = normalized
        segment["normalized_text"] = normalized
        segment["semantic_corrections"] = corrections
        segment["semantic_candidates"] = candidates + fuzzy
        segment["semantic_qc"] = "CORRECTED" if corrections else ("WARNING" if candidates else "PASS")
        if candidates:
            segment["unreliable"] = True
        for event in corrections + candidates + fuzzy:
            item = dict(event)
            item["segment_id"] = segment.get("segment_id")
            item["start"] = segment.get("start")
            item["end"] = segment.get("end")
            all_events.append(item)

    auto = [e for e in all_events if e["status"] == "AUTO_CORRECTED"]
    unresolved = [e for e in all_events if e["status"] != "AUTO_CORRECTED"]
    critical_auto = [e for e in auto if e.get("critical_bridge_meaning")]
    critical_unresolved = [e for e in unresolved if e.get("critical_bridge_meaning")]
    summary = {
        "revision": SEMANTIC_QC_REVISION,
        "segments": len(src),
        "auto_corrections": len(auto),
        "critical_auto_corrections": len(critical_auto),
        "unresolved_candidates": len(unresolved),
        "critical_unresolved": len(critical_unresolved),
        "status": "PASS" if not critical_unresolved else "PASS_WITH_WARNINGS",
        "events": all_events,
        "policy": {
            "raw_asr_preserved": True,
            "semantic_analysis_uses_analysis_text": True,
            "unresolved_critical_candidate_not_fact": True,
            "single_asr_similarity_qc_is_not_semantic_truth": True,
        },
    }
    return src, summary
