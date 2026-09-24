"""Evaluate the actual cache-save condition without Actions, Drive or training."""
import ast
from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/dds-main-30k-prepare.yml'


def cache_write_allowed(event, ref, success, hit):
    text = WORKFLOW.read_text()
    step = text.split('      - name: Save rebuilt verified DDS3 wheel and Bazel state\n', 1)[1]
    expression = re.search(r'^        if: \$\{\{ (.+) \}\}$', step, re.MULTILINE).group(1)
    for name, value in {
        'success()': success,
        'always()': True,
        'github.event_name': event,
        'github.ref': ref,
        'steps.dds-cache.outputs.cache-hit': hit,
    }.items():
        expression = expression.replace(name, repr(value))
    tree = ast.parse(expression.replace('&&', ' and ').replace('||', ' or '), mode='eval')

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (str, bool):
            return node.value
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [evaluate(value) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = evaluate(node.left), evaluate(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        raise AssertionError('Unsupported cache-write condition; review the boundary test')

    return evaluate(tree)


class CacheWriteBoundary(unittest.TestCase):
    def test_event_ref_status_and_hit_matrix(self):
        for event in ('pull_request_target', 'pull_request', 'push', 'workflow_dispatch'):
            for ref in ('refs/heads/main', 'refs/heads/candidate', 'refs/pull/123/merge'):
                for success in (True, False):
                    for hit in ('true', 'false', ''):
                        with self.subTest(event=event, ref=ref, success=success, hit=hit):
                            expected = event == 'workflow_dispatch' and ref == 'refs/heads/main' and success and hit != 'true'
                            self.assertEqual(cache_write_allowed(event, ref, success, hit), expected)

    def test_no_second_implicit_or_explicit_cache_writer(self):
        actions = re.findall(r'^\s+uses: (actions/cache[^\s]*)', WORKFLOW.read_text(), re.MULTILINE)
        self.assertEqual(len(actions), 2)
        self.assertEqual(sum(action.startswith('actions/cache/restore@') for action in actions), 1)
        self.assertEqual(sum(action.startswith('actions/cache/save@') for action in actions), 1)


if __name__ == '__main__':
    unittest.main()
