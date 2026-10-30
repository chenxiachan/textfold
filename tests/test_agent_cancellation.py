"""Only disposable Python children are started; never a real model or bridge."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from textfold import _agent_process, agents, llm


class AgentCancellation(unittest.TestCase):
    def test_cancellation_closes_only_owned_tree_and_preserves_unrelated_process(self):
        self.check_owned_tree()

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object startup')
    def test_cancellation_contains_children_started_during_job_assignment(self):
        original = _agent_process._WindowsJob
        def delayed_job(process):
            time.sleep(1)  # widen Popen -> job assignment: no child may run yet
            return original(process)
        with mock.patch.object(_agent_process, '_WindowsJob', side_effect=delayed_job):
            self.check_owned_tree()

    @unittest.skipUnless(os.name == 'nt', 'Windows suspended startup')
    def test_failed_job_assignment_or_resume_leaves_no_suspended_child(self):
        original_job, original_popen = _agent_process._WindowsJob, subprocess.Popen
        for stage in ('assignment', 'resume'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                marker, children = Path(folder) / 'unexpected', []
                def spawn(*args, **kwargs):
                    process = original_popen(*args, **kwargs)
                    children.append(process)
                    return process
                def job(process):
                    if stage == 'assignment': raise OSError('fixture setup failure')
                    owned = original_job(process)
                    owned.resume = mock.Mock(side_effect=OSError('fixture setup failure'))
                    return owned
                with mock.patch.object(_agent_process.subprocess, 'Popen', side_effect=spawn), \
                        mock.patch.object(_agent_process, '_WindowsJob', side_effect=job):
                    with self.assertRaisesRegex(OSError, 'fixture setup failure'):
                        _agent_process.run([sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                                           stop=threading.Event(), timeout=5, text=True, encoding='utf-8')
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll(), 'failed startup left a suspended child')
                self.assertFalse(marker.exists(), 'agent ran before its job was ready')

    @unittest.skipUnless(os.name == 'nt', 'Windows suspended startup')
    def test_stop_during_job_assignment_never_resumes_the_child(self):
        original_job, original_popen = _agent_process._WindowsJob, subprocess.Popen
        stop, children = threading.Event(), []
        def spawn(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            children.append(process)
            return process
        def job(process):
            owned = original_job(process)
            stop.set()
            return owned
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'unexpected'
            with mock.patch.object(_agent_process.subprocess, 'Popen', side_effect=spawn), \
                    mock.patch.object(_agent_process, '_WindowsJob', side_effect=job):
                with self.assertRaises(llm.Stopped):
                    _agent_process.run([sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                                       stop=stop, timeout=5, text=True, encoding='utf-8')
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            self.assertFalse(marker.exists())

    def check_owned_tree(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); ready=root/'ready'; late=root/'late'
            survivor=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            stop=threading.Event(); errors=[]
            child="import pathlib,time;time.sleep(2);pathlib.Path("+repr(str(late))+").write_text('unexpected')"
            parent="import sys,subprocess,pathlib,time;sys.stdin.read();subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);pathlib.Path("+repr(str(ready))+").write_text('ready');time.sleep(30)"
            def run():
                try:_agent_process.run([sys.executable,'-c',parent],stop=stop,timeout=15,input='',text=True,encoding='utf-8')
                except BaseException as e:errors.append(e)
            worker=threading.Thread(target=run);worker.start()
            try:
                deadline=time.monotonic()+5
                while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(ready.exists())
                stop.set();worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(errors),1)
                self.assertIsInstance(errors[0],llm.Stopped)
                time.sleep(2.1)
                self.assertFalse(late.exists(),'owned grandchild survived cancellation')
                self.assertIsNone(survivor.poll(),'unrelated bridge-like process was terminated')
            finally:
                stop.set();worker.join(5);survivor.terminate();survivor.wait(timeout=5)

    def test_success_returns_output_and_timeout_is_bounded(self):
        result=_agent_process.run([sys.executable,'-c','print("done")'],stop=threading.Event(),timeout=5,text=True,encoding='utf-8')
        self.assertEqual(result.stdout.strip(),'done')
        with self.assertRaises(subprocess.TimeoutExpired):
            _agent_process.run([sys.executable,'-c','import time;time.sleep(30)'],stop=threading.Event(),timeout=.3,text=True,encoding='utf-8')

    def test_large_prompt_does_not_block_stop_when_child_never_reads_stdin(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            _agent_process.run([sys.executable,'-c','import time;time.sleep(30)'],stop=threading.Event(),timeout=.3,
                               input='x'*1000000,text=True,encoding='utf-8')

    def test_cancel_waiting_codex_request_never_starts_another_process(self):
        entered, release, stopped = threading.Event(),threading.Event(),threading.Event()
        errors=[]
        def fake(*args):entered.set();release.wait(4);return {},None
        def first():agents.run_codex('fixture','first',{},'',None,5)
        def second():
            try:agents.run_codex('fixture','second',{},'',None,5,stop=stopped)
            except BaseException as e:errors.append(e)
        with mock.patch.object(agents,'_codex',side_effect=fake) as run:
            a=threading.Thread(target=first);b=threading.Thread(target=second);a.start()
            self.assertTrue(entered.wait(2));b.start();stopped.set()
            try:
                b.join(2)
                self.assertFalse(b.is_alive())
                self.assertEqual(run.call_count,1)
                self.assertIsInstance(errors[0],llm.Stopped)
            finally:release.set();a.join(3);b.join(3)

    def test_rate_limit_and_stop_do_not_trigger_effort_fallback_retry(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ):
            os.environ.pop('TEXTFOLD_NO_MODELS',None)
            spec={'kind':'agent','runtime':'codex','model':'fixture','id':'fixture/codex','effort':'high'}
            for error in (RuntimeError('429 Too Many Requests'),llm.Stopped()):
                with self.subTest(error=type(error).__name__), mock.patch.object(llm,'_once',side_effect=error) as once:
                    with self.assertRaises(type(error)):llm.call('fixture',{},spec,Path(root))
                    self.assertEqual(once.call_count,1)

    def test_cancel_signal_reaches_codex_runner_through_real_llm_dispatch(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ):
            os.environ.pop('TEXTFOLD_NO_MODELS',None)
            stop=threading.Event()
            spec={'kind':'agent','runtime':'codex','model':'fixture','id':'fixture/codex','effort':'high'}
            with mock.patch.object(agents,'run_codex',side_effect=llm.Stopped()) as run:
                with self.assertRaises(llm.Stopped):llm.call('fixture',{},spec,Path(root),stop=stop)
            self.assertIs(run.call_args.kwargs['stop'],stop)

    def test_rate_limit_does_not_retry_codex_with_lower_effort(self):
        with mock.patch.object(agents,'path_of',return_value='fixture'), \
             mock.patch.object(agents,'argv',return_value=['fixture']), \
             mock.patch.object(agents.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','429 Too Many Requests')) as run:
            with self.assertRaisesRegex(RuntimeError,'429'):
                agents.run_codex('fixture','prompt',{},'', 'off', 5)
            self.assertEqual(run.call_count,1)
