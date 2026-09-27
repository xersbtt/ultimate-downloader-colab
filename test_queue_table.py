"""Exercise the real table model against the existing queue action regressions.

Run with anywidget installed to include the widget integration checks.
"""
import importlib.util
import shutil
import subprocess
import unittest

import test_queue_editing as edit_cases
import test_queue_additions_torbox as add_cases


HAS_WIDGETS = importlib.util.find_spec('anywidget') is not None


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for queue interaction checks')
class TableInteractionTests(unittest.TestCase):
    def test_row_clicks_ranges_and_keyboard_preserve_other_selections(self):
        namespace = {}
        add_cases.load_definitions(namespace, '_QUEUE_TABLE_ESM')
        source = namespace['_QUEUE_TABLE_ESM']
        # Run the actual selection functions and delegated event handlers without
        # requiring a Colab session or loading the downloader's network setup.
        selection = source[source.index('    function commitSelection'):source.index('    function renderRows')]
        harness = r"""
const assert = require('node:assert/strict');
const state = {rows: ['a', 'b', 'c', 'd', 'e'].map(id => ({id})), selected_ids: ['e']};
const model = {get: key => state[key], set: (key, value) => state[key] = value, save_changes() {}};
let anchor = null, active = null;
const rowNodes = new Map(state.rows.map(({id}) => [id, {tr: {dataset: {id}, focus() {}}}]));
const body = {}, selectAll = {}, handlers = new Map();
function listen(node, event, callback) { handlers.set(node === body ? event : 'selectAll', callback); }
function updateSelection() {}
""" + selection + r"""
function click(id, extra = {}) {
  handlers.get('click')({target: {closest: () => rowNodes.get(id)?.tr}, ...extra});
}
function key(id, key, extra = {}) {
  handlers.get('keydown')({target: {closest: () => rowNodes.get(id).tr}, key, preventDefault() {}, ...extra});
}
function selected(...ids) { assert.deepEqual(state.selected_ids, ids); }
click('a'); selected('a', 'e');
click('c'); selected('a', 'c', 'e');
click('a'); selected('c', 'e');
click('b', {shiftKey: true}); selected('a', 'b', 'c', 'e');
click('d', {ctrlKey: true}); selected('a', 'b', 'c', 'd', 'e');
click('d', {metaKey: true}); selected('a', 'b', 'c', 'e');
click('missing'); selected('a', 'b', 'c', 'e');
key('c', 'ArrowDown'); selected('a', 'b', 'c', 'e');
key('d', ' '); selected('a', 'b', 'c', 'd', 'e');
key('d', ' '); selected('a', 'b', 'c', 'e');
key('d', 'Home'); selected('a', 'b', 'c', 'e');
key('a', 'End', {shiftKey: true}); selected('a', 'b', 'c', 'd', 'e');
selectAll.checked = false; handlers.get('selectAll')(); selected();
anchor = null;
key('b', 'ArrowDown', {shiftKey: true}); selected('b', 'c');
key('c', 'a', {ctrlKey: true}); selected('a', 'b', 'c', 'd', 'e');
selectAll.checked = false; handlers.get('selectAll')(); selected();
selectAll.checked = true; handlers.get('selectAll')(); selected('a', 'b', 'c', 'd', 'e');
state.rows = state.rows.filter(row => row.id !== 'b');
commitSelection(['b', 'e']); selected('e');
"""
        result = subprocess.run([shutil.which('node'), '-e', harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def make_table():
    import ipywidgets as widgets
    namespace = dict(widgets=widgets, drive=None)
    add_cases.load_definitions(namespace, '_QUEUE_TABLE_CSS', '_QUEUE_TABLE_ESM', '_create_queue_widget')
    table = namespace['_create_queue_widget']()
    assert hasattr(table, 'set_rows'), 'Expected the table, not the fallback list'
    return table


@unittest.skipUnless(HAS_WIDGETS, 'Install anywidget to test the real queue widget')
class TableEditingTests(edit_cases.QueueEditingTests):
    def setUp(self):
        super().setUp()
        self.ns['queue_list'] = make_table()
        self.addCleanup(self.ns['queue_list'].close)
        self.ns['update_queue_display'](preserve_selection=False)
        self.select(1, 2, 3)

    def test_full_filename_columns_and_sizes_are_not_truncated(self):
        filename = 'A Very Long Show Name ' * 8 + 'S02E01.mkv'
        self.rows[1].filename = filename + ' (123.4 MB)'
        self.ns['update_queue_display']()
        row = self.ns['queue_list'].rows[1]
        self.assertEqual(row['name'], filename)
        self.assertEqual(row['size'], '123.4 MB')
        self.assertEqual(row['source'], 'Direct')
        self.assertTrue(row['destination'])

    def test_widths_and_selected_task_ids_survive_sort_edit_and_removal(self):
        table = self.ns['queue_list']
        widths = [80, 650, 120, 95, 900, 250]
        table.column_widths = widths
        selected_ids = list(table.selected_ids)
        self.ns['queue_sort_alpha']()
        self.assertCountEqual(table.selected_ids, selected_ids)
        self.ns['queue_name_input'].value = 'Edited Show'
        self.ns['apply_queue_changes']()
        self.assertCountEqual(table.selected_ids, selected_ids)
        self.assertEqual(table.column_widths, widths)
        self.ns['queue_remove_selected']()
        self.assertEqual(table.selected_ids, [])
        self.assertEqual(table.column_widths, widths)

    def test_browser_selection_uses_ids_and_ignores_removed_files(self):
        table = self.ns['queue_list']
        table.selected_ids = [self.rows[4].id, 'removed-task-id']
        self.assertEqual(self.ns['_selected_queue_indices'](), [4])
        table.options = []
        self.assertEqual((table.rows, table.selected_ids, table.value), ([], [], ()))


@unittest.skipUnless(HAS_WIDGETS, 'Install anywidget to test the real queue widget')
class TableAppendTests(add_cases.AppendQueueTests):
    def setUp(self):
        super().setUp()
        self.ns['queue_list'] = make_table()
        self.addCleanup(self.ns['queue_list'].close)
        self.ns['update_queue_display'](preserve_selection=False)
        self.ns['queue_list'].value = (self.ns['queue_list'].options[0],)

    def test_appending_keeps_resized_columns(self):
        widths = [90, 600, 130, 100, 800, 300]
        self.ns['queue_list'].column_widths = widths
        self.add([self.task('New File.mkv')])
        self.assertEqual(self.ns['queue_list'].column_widths, widths)
        self.assertEqual(self.ns['_selected_queue_indices'](), [0, 2])


if __name__ == '__main__':
    unittest.main()
