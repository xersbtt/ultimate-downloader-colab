"""Exercise OK.ru metadata extraction without the notebook UI or network."""
import ast
import html
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest.mock import Mock, patch
from urllib.parse import urlparse

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.odnoklassniki import OdnoklassnikiIE
except ImportError:
    YoutubeDL = None


SOURCE = Path(__file__).with_name('ultimate_downloader.py')
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))


def load_functions():
    names = {'_configure_okru_extractor', '_url_host', 'url_matches_host',
             '_classify_url', 'get_youtube_title', 'resolve_youtube_playlist',
             'get_youtube_subtitles'}
    nodes = [node for node in TREE.body if isinstance(node, ast.FunctionDef)
             and node.name in names]
    nodes.extend(node for node in TREE.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'STREAMING_HOSTS'
                         for t in node.targets))
    scope = dict(urlparse=urlparse, List=List, DownloadTask=SimpleNamespace,
                 sanitize_filename=lambda name: name, print=Mock())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope


class OkRuCompatibilityTests(unittest.TestCase):
    def test_other_hosts_and_urls_containing_okru_do_not_modify_extractors(self):
        configure = load_functions()['_configure_okru_extractor']
        ydl = Mock()
        for url in ('https://youtube.com/watch?v=test', 'https://vimeo.com/123',
                    'https://example.test/?next=https://ok.ru/video/123',
                    'https://notok.ru/video/123'):
            configure(ydl, url)
        ydl.get_info_extractor.assert_not_called()

    def test_legacy_parser_receives_original_arguments(self):
        configure = load_functions()['_configure_okru_extractor']
        parser = Mock(return_value={'legacy': True})
        extractor = SimpleNamespace(_parse_json=parser)
        configure(Mock(get_info_extractor=Mock(return_value=extractor)),
                  'https://ok.ru/video/123')
        metadata = {'movie': {'title': 'Video'}}
        self.assertIs(extractor._parse_json(metadata, '123'), metadata)
        parser.assert_not_called()
        self.assertEqual(extractor._parse_json('{"legacy":true}', '123', fatal=False),
                         {'legacy': True})
        parser.assert_called_once_with('{"legacy":true}', '123', fatal=False)

    def test_okru_alias_and_embed_links_use_streaming_route(self):
        classify = load_functions()['_classify_url']
        for url in ('https://ok.ru/video/123', 'https://www.ok.ru/videoembed/123',
                    'https://odnoklassniki.ru/video/123'):
            for has_debrid in (False, True):
                self.assertEqual(classify(url, has_debrid), 'stream')


@unittest.skipIf(YoutubeDL is None, 'yt-dlp is not installed')
class RealOkRuExtractorTests(unittest.TestCase):
    url = 'https://ok.ru/video/123456'

    def setUp(self):
        self.scope = load_functions()
        self.metadata = {
            'provider': 'UPLOADED_ODKL',
            'movie': {'title': 'OK.ru fixture video', 'duration': '12',
                      'subtitleTracks': [{'language': 'en',
                                          'url': 'https://example.test/subs.vtt'}]},
            'videos': [{'name': 'hd', 'url': 'https://example.test/video.mp4'}],
        }

    def webpage(self, metadata):
        player = {'flashvars': {'videoId': '123456', 'metadata': metadata}}
        return '<div data-options="{}"></div>'.format(
            html.escape(json.dumps(player), quote=True))

    def test_real_extractor_reads_dict_and_legacy_string_metadata(self):
        for metadata in (self.metadata, json.dumps(self.metadata)):
            with self.subTest(metadata_type=type(metadata).__name__), \
                    YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl, \
                    patch.object(OdnoklassnikiIE, '_download_webpage',
                                 return_value=self.webpage(metadata)):
                self.scope['_configure_okru_extractor'](ydl, self.url)
                info = ydl.extract_info(self.url, download=False)
                self.assertEqual(info['title'], self.metadata['movie']['title'])
                self.assertEqual(info['formats'][0]['url'], self.metadata['videos'][0]['url'])
                self.assertEqual(info['subtitles']['en'][0]['url'],
                                 self.metadata['movie']['subtitleTracks'][0]['url'])

    def test_queue_title_and_subtitle_lookup_use_real_fixed_extractor(self):
        with patch.object(OdnoklassnikiIE, '_download_webpage',
                          return_value=self.webpage(self.metadata)):
            self.assertEqual(self.scope['get_youtube_title'](self.url), 'OK.ru fixture video')
            tasks = self.scope['resolve_youtube_playlist'](self.url)
            self.assertEqual(tasks[0].filename, 'OK.ru fixture video.mkv')
            self.assertEqual(tasks[0].url, self.url)
            self.assertEqual(self.scope['get_youtube_subtitles'](self.url), {'en': 'en'})

    def test_parser_change_is_confined_to_one_downloader_instance(self):
        with YoutubeDL({'quiet': True}) as fixed, YoutubeDL({'quiet': True}) as plain:
            self.scope['_configure_okru_extractor'](fixed, self.url)
            self.assertIs(fixed.get_info_extractor('Odnoklassniki')._parse_json(
                self.metadata, '123456'), self.metadata)
            with self.assertRaises(TypeError):
                plain.get_info_extractor('Odnoklassniki')._parse_json(self.metadata, '123456')

    def test_malformed_json_still_raises(self):
        with YoutubeDL({'quiet': True}) as ydl:
            self.scope['_configure_okru_extractor'](ydl, self.url)
            with self.assertRaises(Exception) as raised:
                ydl.get_info_extractor('Odnoklassniki')._parse_json('{bad json', '123456')
            self.assertIn('Failed to parse JSON', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
