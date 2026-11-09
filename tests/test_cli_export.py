import json
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from openpyxl import load_workbook

from helpers import fixture, user
from x_list_discovery import cli, pipeline
from x_list_discovery.export import report
from x_list_discovery.storage import read_csv, read_json, write_json
from x_list_discovery.workspace import Workspace, initialize


class CLITests(unittest.TestCase):
    def test_invalid_parameters_are_rejected_before_paid_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            ws, _, _ = fixture(temp)
            args = cli.parser().parse_args(['--root', str(ws.path.parent), 'run', 'demo', '--fetch', '--mode', 'cofollow', '--max-followers', '100'])
            with patch('x_list_discovery.cli.TwitterAPI') as client:
                with self.assertRaisesRegex(ValueError, 'only to gems'):
                    cli.execute(args)
                client.assert_not_called()

    def test_nonpositive_thresholds_fail_during_argument_parsing(self):
        for options in [['init', 'demo', '--list', '123', '--min-common', '0'],
                        ['run', 'demo', '--fetch', '--top', '-1'],
                        ['fetch', 'demo', '--workers', '0']]:
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.parser().parse_args(options)

    def test_offline_both_reports_and_list_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            ws, _, graph = fixture(temp, ready=False)
            env = {k: v for k, v in os.environ.items() if k not in {'API', 'API2', 'TWITTERAPI_IO_KEY'}}
            def run(*args, code=0):
                result = subprocess.run([sys.executable, '-m', 'x_list_discovery', '--root', str(ws.path.parent), *args],
                                        cwd=temp, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                return json.loads(result.stdout) if code == 0 else result.stderr
            self.assertIn('Missing API key', run('fetch', 'demo', code=1))
            run('import-graph', 'demo', str(graph))
            result = run('run', 'demo', '--min-common', '2', '--max-followers', '5000')
            self.assertEqual(set(result['reports']), {'cofollow', 'gems'})
            self.assertEqual(result['reports']['cofollow']['selected_accounts'], 2)
            self.assertEqual(result['reports']['gems']['selected_accounts'], 0)
            for m in ('cofollow', 'gems'):
                self.assertTrue(Path(result['reports'][m]['workbook']).is_file())
                self.assertTrue(Path(result['reports'][m]['csv']).is_file())
            self.assertEqual(run('status', 'demo')['rankings']['cofollow'], 2)
            run('init', 'another', '--members', str(Path(temp) / 'demo-members.json'))
            self.assertEqual(run('status', 'another')['voting_seeds'], 3)
            self.assertEqual(run('lists')['workspaces'], ['another', 'demo'])
            self.assertIn('Incomplete source', run('run', 'another', code=1))
            self.assertIn('only to gems', run('rank', 'demo', '--mode', 'cofollow', '--max-followers', '5000', code=1))

    def test_username_only_csv_works_without_profile_or_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'members.csv'
            path.write_text('username\nAlice\nBob\n')
            ws = Workspace(temp, 'plain')
            initialize(ws, path)
            self.assertEqual([u['userName'] for u in ws.seeds()], ['Alice', 'Bob'])
            self.assertFalse(ws.config.individuals_only)

    def test_list_url_init_uses_api_snapshot_without_leaking_temporary_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            args = cli.parser().parse_args(['--root', temp, 'init', 'online', '--list', 'https://x.com/i/lists/123'])
            with patch('x_list_discovery.cli.TwitterAPI') as client:
                client.return_value.list_members.return_value = [user('Alice', 1), user('Bob', 2)]
                cli.execute(args)
                client.return_value.list_members.assert_called_once_with('123')
            ws = Workspace(temp, 'online')
            self.assertEqual(len(ws.seeds()), 2)
            self.assertTrue(ws.file('input/source.json').is_file())
            self.assertEqual(read_json(ws.file('state/origin.json'))['source'], 'https://x.com/i/lists/123')

    def test_empty_remote_list_does_not_create_a_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            args = cli.parser().parse_args(['--root', temp, 'init', 'empty', '--list', '123'])
            with patch('x_list_discovery.cli.TwitterAPI') as client:
                client.return_value.list_members.return_value = []
                with self.assertRaisesRegex(ValueError, 'no members'):
                    cli.execute(args)
            self.assertFalse((Path(temp) / 'empty').exists())

    def test_original_member_exclusion_cannot_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            ws, _, _ = fixture(temp)
            config = read_json(ws.config_path)
            config['exclude_members'] = False
            write_json(ws.config_path, config)
            with self.assertRaisesRegex(ValueError, 'always be excluded'):
                pipeline.rank(ws)


class ExportTests(unittest.TestCase):
    def test_safe_text_source_counts_and_complete_selected_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            ws, _, _ = fixture(temp)
            pipeline.rank(ws, minimum=2)
            result = report(ws, 'cofollow', top=1)
            self.assertEqual(result['sheets']['Top accounts'], 1)
            self.assertEqual(result['sheets']['Shared-follow evidence'], 2)
            self.assertEqual(len(read_csv(result['csv'])), 2)
            with zipfile.ZipFile(result['workbook']) as archive:
                self.assertFalse(any('externalLinks/' in n for n in archive.namelist()))
            wb = load_workbook(result['workbook'])
            try:
                sheet = wb['Top accounts']
                headers = [c.value for c in sheet[1]]
                self.assertNotIn('Total followers (snapshot)', headers)
                name = sheet.cell(2, headers.index('Name') + 1)
                self.assertEqual(name.value, '=HYPERLINK("bad")')
                self.assertEqual(name.data_type, 's')
                link = sheet.cell(2, headers.index('X profile') + 1)
                self.assertEqual(link.hyperlink.target, 'https://x.com/Alpha')
                self.assertFalse(wb._external_links)
            finally:
                wb.close()
            self.assertTrue(read_csv(result['csv'])[0]['name'].startswith("'="))

    def test_gem_export_includes_total_followers_and_score(self):
        with tempfile.TemporaryDirectory() as temp:
            ws, _, _ = fixture(temp)
            pipeline.rank(ws, mode='gems', minimum=2)
            result = report(ws, 'gems')
            rows = read_csv(result['csv'])
            self.assertEqual([r['userName'] for r in rows], ['Gamma'])
            self.assertEqual(rows[0]['followers_count'], '10000')
            self.assertEqual(float(rows[0]['gem_index']), 1.91)
