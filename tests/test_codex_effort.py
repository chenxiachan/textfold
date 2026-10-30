"""Advertised Codex efforts, with the existing request/cache identity intact.

No model, account, user's cache, or app-server process is accessed.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold import agents, llm


class CodexEffort(unittest.TestCase):
    model = 'gpt-6.1-sol'

    def catalog(self, levels, default='medium'):
        return {'agents': [{'runtime': 'codex', 'runnable': True, 'path': 'fixture',
                            'models': [{'model': 'other-model', 'efforts': ['none']},
                                       {'model': self.model, 'efforts': levels, 'defaultEffort': default}]}]}

    def spec(self, effort='off'):
        return {'kind': 'agent', 'runtime': 'codex', 'model': self.model,
                'id': 'codex/codex/' + self.model, 'effort': effort}

    @staticmethod
    def legacy_key(prompt, schema, spec):
        # This is the pre-fix cache format, independently fixed by the test.
        parts = [llm.SYSTEM, prompt, schema, spec['id']]
        if spec.get('effort'):
            parts.append(spec['effort'])
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:24] + '.json'

    def invoke(self, levels, requested='off', expected='low', default='medium'):
        calls = []
        def completed(args, **kwargs):
            calls.append(args)
            overrides = [arg.split('=', 1)[1] for arg in args if arg.startswith('model_reasoning_effort=')]
            self.assertEqual(overrides, [] if expected is None else [expected])
            Path(args[args.index('-o') + 1]).write_text('{"ok":true}', encoding='utf-8')
            return subprocess.CompletedProcess(args, 0, '', '')
        with mock.patch.dict(agents.STATE, self.catalog(levels, default)), \
                mock.patch.object(agents, 'path_of', return_value='fixture'), \
                mock.patch.object(agents, 'argv', return_value=['fixture']), \
                mock.patch.object(agents.subprocess, 'run', side_effect=completed), \
                mock.patch.dict(os.environ):
            os.environ.pop('TEXTFOLD_NO_MODELS', None)
            spec = self.spec(requested)
            before = dict(spec)
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                result, cached = llm.call('chapter fixture', {}, spec, root)
                self.assertFalse(cached)
                self.assertEqual(result['out'], {'ok': True})
                self.assertEqual(result['models'], [spec['id']] + ([requested] if requested else []))
                self.assertEqual(Path(result['file']).name, self.legacy_key('chapter fixture', {}, before))
                self.assertEqual(llm.call('chapter fixture', {}, spec, root)[1], True)
            self.assertEqual(spec, before)
        self.assertEqual(len(calls), 1)

    def test_off_selects_low_directly_when_none_is_not_advertised(self):
        self.invoke(['high', 'max', 'low', 'medium'])

    def test_off_uses_none_or_minimal_only_when_advertised(self):
        for levels, expected in ((['low', 'none', 'medium'], 'none'), (['high', 'minimal', 'low'], 'minimal')):
            with self.subTest(levels=levels):
                self.invoke(levels, expected=expected)

    def test_off_chooses_available_minimum_instead_of_assuming_low(self):
        self.invoke(['max', 'high', 'medium'], expected='medium')

    def test_supported_explicit_effort_and_cli_default_are_preserved(self):
        self.invoke(['low', 'high'], requested='high', expected='high')
        self.invoke(['low', 'high'], requested=None, expected=None)

    def test_unsupported_explicit_effort_uses_supported_lower_level(self):
        self.invoke(['high', 'low'], requested='medium', expected='low')

    def test_missing_capability_does_not_guess_none(self):
        self.invoke([], expected=None)

    def test_unknown_model_does_not_borrow_another_models_capabilities(self):
        with mock.patch.dict(agents.STATE, self.catalog(['low'])):
            self.assertIsNone(agents._codex_effort('unknown-model', 'off'))
            self.assertEqual(agents._codex_effort('unknown-model', 'high'), 'high')

    def test_stop_aware_runner_receives_the_selected_supported_effort(self):
        import threading
        from textfold import _agent_process
        stop = threading.Event()
        def completed(args, **kwargs):
            self.assertIs(kwargs['stop'], stop)
            self.assertIn('model_reasoning_effort=low', args)
            self.assertNotIn('model_reasoning_effort=none', args)
            Path(args[args.index('-o') + 1]).write_text('{"ok":true}', encoding='utf-8')
            return subprocess.CompletedProcess(args, 0, '', '')
        with mock.patch.dict(agents.STATE, self.catalog(['low', 'high'])), \
                mock.patch.object(agents, 'path_of', return_value='fixture'), \
                mock.patch.object(agents, 'argv', return_value=['fixture']), \
                mock.patch.object(_agent_process, 'run', side_effect=completed) as run:
            self.assertEqual(agents.run_codex(self.model, 'fixture', {}, '', 'off', 5, stop=stop)[0], {'ok': True})
            self.assertEqual(run.call_count, 1)

    def test_only_unfinished_chapter_runs_when_mixed_with_legacy_cached_chapters(self):
        spec = self.spec()
        def completed(args, **kwargs):
            self.assertIn('model_reasoning_effort=low', args)
            Path(args[args.index('-o') + 1]).write_text('{"chapter":"unfinished"}', encoding='utf-8')
            return subprocess.CompletedProcess(args, 0, '', '')
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(agents.STATE, self.catalog(['low', 'high'])), \
                mock.patch.object(agents, 'path_of', return_value='fixture'), \
                mock.patch.object(agents, 'argv', return_value=['fixture']), \
                mock.patch.object(agents.subprocess, 'run', side_effect=completed) as run, mock.patch.dict(os.environ):
            os.environ.pop('TEXTFOLD_NO_MODELS', None)
            root = Path(folder)
            cached_path = root / self.legacy_key('completed', {}, spec)
            raw = b'{"out":{"chapter":"completed"},"cost":null,"models":["legacy","off"]}'
            cached_path.write_bytes(raw)
            result = [llm.call(chapter, {}, spec, root) for chapter in ('completed', 'unfinished', 'completed')]
            self.assertEqual([cached for _, cached in result], [True, False, True])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(cached_path.read_bytes(), raw)
            self.assertEqual(Path(result[1][0]['file']).name, self.legacy_key('unfinished', {}, spec))

    def test_legacy_completed_chapters_are_cache_hits_without_any_dispatch(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        spec = self.spec()
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(agents.STATE, self.catalog(['low', 'medium'])), \
                mock.patch.object(llm, '_once', side_effect=AssertionError('A completed chapter was recalculated')):
            root = Path(folder)
            originals = {}
            for chapter in ('abstract', 'section one', 'section two'):
                path = root / self.legacy_key(chapter, schema, spec)
                raw = json.dumps({'out': {'ok': True, 'chapter': chapter}, 'cost': None,
                                  'models': [spec['id'], 'off'], 'note': 'Legacy low fallback'}).encode()
                path.write_bytes(raw); originals[path] = raw
                result, cached = llm.call(chapter, schema, spec, root)
                self.assertTrue(cached)
                self.assertEqual(result['out']['chapter'], chapter)
            self.assertEqual({p: p.read_bytes() for p in originals}, originals)


if __name__ == '__main__':
    unittest.main()
