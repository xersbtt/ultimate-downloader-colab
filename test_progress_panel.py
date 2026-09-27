"""Individual progress remains connected with the custom queue widget manager."""
import importlib.util
import unittest
from types import SimpleNamespace
from typing import List

from test_queue_additions_torbox import load_definitions


@unittest.skipUnless(importlib.util.find_spec('anywidget'), 'Requires anywidget')
class ProgressPanelTests(unittest.TestCase):
    def setUp(self):
        import ipywidgets as widgets
        self.widgets = widgets
        self.box = widgets.VBox()
        self.fallback = widgets.Accordion(children=[self.box])
        self.ns = dict(widgets=widgets, queue_list=SimpleNamespace(set_rows=lambda rows: None),
                       List=List, DownloadTask=object, _per_task_bars={}, _per_task_done_at={},
                       _per_task_box=self.box, _PER_TASK_LINGER=2,
                       _colab_live_status_active=False,
                       last_display_speed=0, batch_start_time=None, download_stats={},
                       progress_bar=widgets.FloatProgress(), status_label=widgets.HTML(),
                       _disk_free_gb=lambda: float('inf'), time=SimpleNamespace(time=lambda: 100))
        load_definitions(self.ns, '_DOWNLOAD_PROGRESS_ESM', '_DOWNLOAD_PROGRESS_CSS',
                         '_create_download_progress_panel', '_clear_per_task_bars',
                         '_ProgressRow', 'update_progress_display', 'reset_progress')
        self.panel = self.ns['_create_download_progress_panel'](self.fallback)
        self.ns['_per_task_accordion'] = self.panel
        self.assertTrue(hasattr(self.panel, 'set_bars'))
        self.tasks = [SimpleNamespace(id=str(i), filename=f'File {i}.mkv',
                                      url='https://test/file', status='downloading') for i in range(2)]
        self.addCleanup(self.close)

    def close(self):
        self.ns['_clear_per_task_bars']()
        for widget in (self.panel, self.box, self.ns['progress_bar'], self.ns['status_label']):
            widget.close()

    def test_active_updates_keep_expansion_and_individual_values(self):
        self.panel.expanded = True
        self.ns['download_stats'] = {'0': {'pct': 42, 'speed_mbs': 2}, '1': {'pct': 71, 'speed_mbs': 5}}
        self.ns['update_progress_display'](self.tasks)
        self.assertEqual(self.panel.layout.display, 'block')
        self.assertEqual([bar['value'] for bar in self.panel.bars], [42, 71])
        self.assertTrue(all(isinstance(bar, self.ns['_ProgressRow'])
                            for bar in self.ns['_per_task_bars'].values()))
        self.assertIn('2 active downloads', self.panel.title)
        self.assertIn('7.0 MB/s', self.panel.title)
        self.assertTrue(self.panel.expanded)
        self.panel.expanded = False
        self.ns['download_stats']['0']['pct'] = 50
        self.ns['update_progress_display'](self.tasks)
        self.assertFalse(self.panel.expanded)
        self.assertEqual(self.panel.bars[0]['value'], 50)

    def test_moving_and_finished_bars_linger_then_clear_without_resetting_expansion(self):
        self.panel.expanded = True
        self.ns['update_progress_display'](self.tasks)
        self.tasks[0].status = 'moving'
        self.tasks[1].status = 'failed'
        self.ns['update_progress_display'](self.tasks)
        self.assertEqual([bar['state'] for bar in self.panel.bars], ['info', 'danger'])
        self.assertIn('📤', self.panel.bars[0]['description'])
        self.tasks[0].status = 'done'
        self.ns['update_progress_display'](self.tasks)
        self.ns['time'].time = lambda: 103
        self.ns['update_progress_display'](self.tasks)
        self.assertEqual(self.panel.bars, [])
        self.assertEqual(self.panel.layout.display, 'none')
        self.assertTrue(self.panel.expanded)

    def test_reset_clears_all_progress_and_allows_next_batch(self):
        self.ns['update_progress_display'](self.tasks)
        self.ns['reset_progress']()
        self.assertEqual(self.panel.bars, [])
        self.assertEqual(self.ns['_per_task_bars'], {})
        self.assertEqual(self.panel.layout.display, 'none')
        self.ns['update_progress_display'](self.tasks)
        self.assertEqual(len(self.panel.bars), 2)
        self.assertEqual(self.panel.layout.display, 'block')

    def test_standard_renderer_keeps_its_accordion_fallback(self):
        self.ns['queue_list'] = SimpleNamespace()
        fallback = self.widgets.Accordion()
        self.addCleanup(fallback.close)
        self.assertIs(self.ns['_create_download_progress_panel'](fallback), fallback)


if __name__ == '__main__':
    unittest.main()
