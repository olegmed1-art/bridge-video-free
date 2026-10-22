"""Build inert consumer forks from exact saved source bytes; no code execution."""
import ast
import hashlib

SCORER_SHAS = frozenset(('16fa38973e7cc97685185bd1ad40b8db6e9b9a99502a704b50739eb22456c3b2',
                        '51d9cd5016cb437f4a14a3801aee9318358602903177d16b35ee19409ef0f494'))
MARGIN_SHA = '9e5e5b4a67ed42006a297175b34bb048df8b537a6228152b03b560e9393d96d9'
UNIFIED_SHA = '9cfd28a6478577209d4abd80c0495c730473672e5772309874a94292e6faf0cf'
MARGIN_VERSION = 'diana20-same-roi-component-margin-candidate-r2-tie-v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _replace(source, before, after):
    if source.count(before) != 1:
        raise ValueError('EXACT_SOURCE_MARKER_REQUIRED')
    return source.replace(before, after)


def fork_scorer(raw):
    if sha(raw) not in SCORER_SHAS:
        raise ValueError('UNSUPPORTED_SCORER_SOURCE_SHA')
    source = raw.decode('utf-8')
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'score_unknown')
    old = ast.get_source_segment(source, node)
    new = _replace(old, "    stored=unknown.get('factor_diagnostic')\n",
        "    stored=unknown.get('factor_diagnostic')\n"
        "    tie_view=None\n"
        "    if stored is not None:\n"
        "        from bridge_vision.factor_identity_compat import factor_parity_views\n"
        "        tie_view=factor_parity_views(stored,rebuilt,at['rank_scores'],at['suit_scores'])\n"
        "        if tie_view['check'] is not None:\n"
        "            checks['factor_tie_protocol']=tie_view['check']\n"
        "        stored=tie_view['stored_legacy']\n")
    new = _replace(new, "    if stored is not None:\n        for k in ('best_card','gates','roi_xyxy','roi_bgr_sha256'):",
        "    if stored is not None and tie_view['check'] is not False:\n"
        "        for k in ('best_card','gates','roi_xyxy','roi_bgr_sha256'):")
    new = _replace(new, "    return dict(schema='diana20-saved-roi-registration-diagnostic/v1'",
                        "    result=dict(schema='diana20-saved-roi-registration-diagnostic/v1'")
    new += ("\n    if tie_view is not None and tie_view['current_recomputed'] is not None:\n"
            "        result['current_recomputed_diagnostic']=tie_view['current_recomputed']\n"
            "    return result")
    result = _replace(source, old, new)
    parsed = ast.parse(result)
    # All other functions, including the export's global failure veto, survive.
    others = lambda t: [ast.dump(n, include_attributes=False) for n in t.body
                        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name != 'score_unknown']
    if others(tree) != others(parsed):
        raise ValueError('UNRELATED_SOURCE_CHANGED')
    return result.encode('utf-8')


def fork_margin(raw):
    if sha(raw) != MARGIN_SHA:
        raise ValueError('UNSUPPORTED_MARGIN_SOURCE_SHA')
    source = raw.decode('utf-8')
    source = _replace(source, "VERSION = 'diana20-same-roi-component-margin-candidate-r1'",
                              'VERSION = ' + repr(MARGIN_VERSION))
    source = _replace(source, "    gates=factor.get('gates')\n",
        "    current=diagnostic.get('current_recomputed_diagnostic')\n"
        "    if (factor.get('identity_status')=='TIE' or\n"
        "            (type(current) is dict and current.get('identity_status')=='TIE')):\n"
        "        return refuse('FACTOR_IDENTITY_TIE')\n"
        "    gates=factor.get('gates')\n")
    source = _replace(source, "            (pending if d['status']=='ADDITIONAL_MACHINE_CANDIDATE' else unknown_decisions).append(d)",
        "            if d['status']=='ADDITIONAL_MACHINE_CANDIDATE':\n"
        "                need(d.get('card') in CARDS and u.get('factor_diagnostic',{}).get('identity_status')!='TIE',\n"
        "                     'NULL_OR_TIE_CANNOT_BE_ADDITIONAL_CARD')\n"
        "            (pending if d['status']=='ADDITIONAL_MACHINE_CANDIDATE' else unknown_decisions).append(d)")
    ast.parse(source)
    return source.encode('utf-8')


def fork_unified(raw, margin_sha):
    if sha(raw) != UNIFIED_SHA or len(margin_sha) != 64 or any(c not in '0123456789abcdef' for c in margin_sha):
        raise ValueError('UNSUPPORTED_UNIFIED_OR_MARGIN_SHA')
    source = raw.decode('utf-8')
    source = _replace(source, "MARGIN_SHA = '"+MARGIN_SHA+"'", 'MARGIN_SHA = '+repr(margin_sha))
    source = _replace(source, "MARGIN_VERSION = 'diana20-same-roi-component-margin-candidate-r1'",
                              'MARGIN_VERSION = '+repr(MARGIN_VERSION))
    source = _replace(source, "'CREDIBLE_DIFFERENT_IDENTITY_AT_OTHER_REGISTRATION'))",
        "'CREDIBLE_DIFFERENT_IDENTITY_AT_OTHER_REGISTRATION', 'FACTOR_IDENTITY_TIE'))")
    source = _replace(source, "    factor = u.get('factor_diagnostic', {})\n",
        "    factor = u.get('factor_diagnostic', {})\n"
        "    _need(type(factor) is dict and factor.get('identity_status') != 'TIE'\n"
        "          and factor.get('best_card') is not None, 'MARGIN_TIE_CANNOT_BE_PROPOSED')\n")
    ast.parse(source)
    return source.encode('utf-8')
