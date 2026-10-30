"""Slow model calls must not hide completed sections or inherit desktop IPC."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from textfold import agents, ladder, llm, local, store, translate
from textfold.storage import LocalDirectoryBackend
from test_text_language import Model, CFG


class GenerationProgress(unittest.TestCase):
    def check_completion_order(self, translation):
        with tempfile.TemporaryDirectory() as root, store.use_backend(LocalDirectoryBackend(root)):
            pid = local.import_text('# Progress\n\n## First\n\nFirst paragraph.\n\n## Slow\n\nSlow paragraph.\n\n## Later\n\nLater paragraph.')
            doc = store.assemble(pid)
            self.assertEqual(len([c for c in doc['chunks'] if c['units']]),3)
            release, completed, advanced = threading.Event(),threading.Event(),threading.Event()
            errors=[]
            model=Model()
            def call(prompt,*args,**kwargs):
                if 'Slow paragraph' in prompt:
                    if not release.wait(5): raise AssertionError('test release timed out')
                elif 'Later paragraph' in prompt: completed.set()
                return model(prompt,*args,**kwargs)
            def progress(done,total):
                if done>=2:advanced.set()
            def run():
                try:
                    if translation: translate.build(doc,'zh',CFG,Path(root)/'cache',jobs=3,progress=progress,log=lambda *a:None)
                    else: ladder.build(doc,CFG,Path(root)/'cache',jobs=3,progress=progress,log=lambda *a:None)
                except BaseException as e:errors.append(e)
            with mock.patch.object(llm,'call',call):
                worker=threading.Thread(target=run);worker.start()
                try:
                    self.assertTrue(completed.wait(2))
                    self.assertTrue(advanced.wait(1),'completed sections are hidden behind an earlier slow request')
                finally:release.set();worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors,[])

    def test_ladder_reports_results_in_completion_order(self):self.check_completion_order(False)
    def test_translation_reports_results_in_completion_order(self):self.check_completion_order(True)

    def test_child_environment_excludes_desktop_control_channels(self):
        control={'CODEX_APP_TOOLS_PIPE_PATH':'fixture-pipe','CODEX_THREAD_ID':'fixture-thread',
                 'CODEX_SESSION_ID':'fixture-session','CODEX_INTERNAL_ORIGINATOR_OVERRIDE':'codex_desktop'}
        with mock.patch.dict(os.environ,{**control,'CODEX_HOME':'fixture-home','OPENAI_API_KEY':'fixture-only'}):
            env=agents.child_env()
        self.assertFalse(set(control)&env.keys(),'standalone agents inherited desktop session/IPC controls')
        self.assertEqual(env['CODEX_HOME'],'fixture-home')
        self.assertEqual(env['OPENAI_API_KEY'],'fixture-only')
