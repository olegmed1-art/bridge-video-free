"""Emit a single read-only PostgreSQL query testing the recovery input guard.

Uses the actual candidate SQL predicate and snapshot SELECT, without installing
functions or modifying rows. Execute only as an authorized database reader.
The live baseline must still be the original FAILED_CLOSED dispatch.
"""
import json
from pathlib import Path
import re


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def query():
    source = Path(__file__).with_name('light_dispatch_1867_journal_candidate.sql').read_text()
    predicate = re.search(
        r'CREATE FUNCTION autopilot\.light_dispatch_1867_input_valid\(v jsonb\).*?AS \$\$(.*?)\$\$;',
        source, re.S).group(1).strip()
    snapshot = re.search(
        r'CREATE FUNCTION autopilot\.light_dispatch_1867_snapshot\(\).*?AS \$\$(.*?)\$\$;',
        source, re.S).group(1).strip()
    expected = json.loads(re.search(r"v @> '(.*?)'::jsonb", predicate, re.S).group(1))
    cases = [("original", "original", True), ("sql_null", "NULL::jsonb", False),
             ("json_null", "'null'::jsonb", False), ("empty", "'{}'::jsonb", False)]

    def walk(value, path=()):
        for key, item in value.items():
            current = (*path, key)
            if isinstance(item, dict):
                yield from walk(item, current)
            else:
                yield current, item

    for path, value in walk(expected):
        sql_path = literal('{' + ','.join(path) + '}') + '::text[]'
        label = '.'.join(path)
        cases.append((label + ':missing', f'original #- {sql_path}', False))
        replacement = 'null' if value is not None else '"unexpected"'
        cases.append((label + ':changed',
                      f'jsonb_set(original,{sql_path},{literal(replacement)}::jsonb)', False))
        cases.append((label + ':array',
                      f'jsonb_set(original,{sql_path},{literal(json.dumps([value]))}::jsonb)', False))
    cases.extend([
        ('legacy_mailbox', "jsonb_set(original,'{outbox,mailbox_pr}','1150')", False),
        ('attempts_as_string', "jsonb_set(original,'{outbox,attempts}','\"5\"')", False),
        ('summary_status', "jsonb_set(original,'{task,safe_summary_json,status}','\"OK\"')", False),
        ('unrelated_timestamp', "jsonb_set(original,'{outbox,updated_at}','\"different\"')", True),
    ])
    values = ',\n'.join(
        '(' + literal(label) + ',' + expression + ',' + str(expect).lower() + ')'
        for label, expression, expect in cases)
    return f"""WITH baseline AS (
 SELECT ({snapshot}) AS original
), cases AS (
 SELECT c.* FROM baseline CROSS JOIN LATERAL (VALUES {values}) c(label,v,expected)
), checked AS (
 SELECT label,expected,({predicate}) AS actual FROM cases
)
SELECT count(*) AS cases, bool_and(actual IS NOT DISTINCT FROM expected) AS passed,
 COALESCE(jsonb_agg(label ORDER BY label) FILTER (WHERE actual IS DISTINCT FROM expected),'[]') AS failures
FROM checked;
"""


if __name__ == '__main__':
    print(query())
