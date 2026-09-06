import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from media_categorizer.library import scan_files, filter_records, processed_paths, LibraryDialog
from media_categorizer.batch import plan_category, BatchWorker, BatchDialog
from media_categorizer.photo_editor import PhotoEditor
from media_categorizer.timeline import RangeTimeline, TimelineAssets
from media_categorizer.video_export import ExportOptions, VideoExport, signature, probe
from media_categorizer.settings import normalize_categories
from media_categorizer import updates
from media_categorizer_v4_5 import MediaCategorizer, CategoriesDialog
from test_video_editor import ffmpeg


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'APPDATA': str(self.root / 'settings')})
        self.env.start()
        self.path = self.root / 'a.png'
        image = QImage(80, 60, QImage.Format_RGB32)
        image.fill(Qt.red)
        image.save(str(self.path))

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def settle(self, condition, timeout=15):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(condition())

    def test_library_recursive_search_filter_and_sort(self):
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'b.mp4').write_bytes(b'video')
        self.assertEqual(len(scan_files(self.root)), 1)
        records = scan_files(self.root, recursive=True)
        self.assertEqual(len(records), 2)
        self.assertEqual(filter_records(records, 'B.MP4')[0]['path'].name, 'b.mp4')
        self.assertEqual(len(filter_records(records, kind='photo')), 1)
        self.assertEqual(filter_records(records, sort='size')[0]['path'].name, 'b.mp4')
        processed = processed_paths([{'status':'OK', 'result':str(self.path)}])
        self.assertEqual(len(filter_records(records, kind='processed', processed=processed)), 1)
        self.assertFalse(scan_files(self.root, cancelled=lambda: True))

    def test_batch_rename_collision_and_source_change(self):
        category = dict(name='TAG', action='rename', destination='')
        plans = plan_category([self.path], category, [category], '{tags}_{original}')
        self.assertEqual(plans[0]['target'].name, 'TAG_a.png')
        plans[0]['target'].write_bytes(b'external')
        collision = plan_category([self.path], category, [category], '{tags}_{original}')
        self.assertTrue(collision[0]['error'])
        results = []
        worker = BatchWorker(plans, ExportOptions(), 1)
        worker.completed.connect(results.append)
        worker.run()
        self.assertEqual(results[0]['status'], 'ERROR')
        self.assertEqual(plans[0]['target'].read_bytes(), b'external')
        self.assertTrue(self.path.exists())

    def test_batch_copy_and_cancel(self):
        category = dict(name='COPY', action='copy', destination=str(self.root / 'out'))
        plans = plan_category([self.path], category, [category], None)
        worker = BatchWorker(plans, ExportOptions(), 1)
        worker.run()
        self.assertEqual(plans[0]['target'].read_bytes(), self.path.read_bytes())
        next_plan = plan_category([self.path], category, [category], None)
        worker = BatchWorker(next_plan, ExportOptions(), 1)
        worker.cancel()
        worker.run()
        self.assertFalse(next_plan[0]['target'].exists())

    def test_batch_dialog_refreshes_main_and_releases_reservation(self):
        main = MediaCategorizer()
        main.load_folder(self.root)
        dialog = BatchDialog([self.path], main)
        dialog.prepare()
        target = dialog.plans[0]['target']
        dialog.start()
        self.settle(lambda: dialog.worker is None)
        self.assertTrue(target.exists())
        self.assertEqual(main.current_file, target)
        self.assertFalse(main.file_reservations.is_busy(self.path, target))
        self.assertEqual(main.load_log_records()[-1]['status'], 'OK')
        dialog.reject()
        main.close()
        main.thread_pool.waitForDone()

    def test_photo_ratio_move_rotate_undo(self):
        editor = PhotoEditor(self.path)
        editor.rotate(90)
        self.assertEqual(editor.canvas.image.size().width(), 60)
        editor.undo()
        self.assertEqual(editor.canvas.image, editor.original)
        self.assertFalse(editor.dirty)
        canvas = editor.canvas
        canvas.resize(400, 300)
        canvas.set_ratio(1)
        self.assertEqual(canvas.selection.width(), canvas.selection.height())
        before = canvas.selection
        canvas.show()
        self.app.processEvents()
        center = QPoint(200, 150)
        QTest.mousePress(canvas, Qt.LeftButton, pos=center)
        QTest.mouseMove(canvas, center + QPoint(30, 0))
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=center + QPoint(30, 0))
        self.assertEqual(canvas.selection.size(), before.size())
        self.assertGreaterEqual(canvas.selection.left(), before.left())
        editor.dirty = False
        canvas.clear_selection()
        editor.reject()

    def test_timeline_drags_and_clamps(self):
        timeline = RangeTimeline(10)
        timeline.resize(1024, 152)
        timeline.show()
        self.app.processEvents()
        QTest.mousePress(timeline, Qt.LeftButton, pos=QPoint(12, 30))
        QTest.mouseRelease(timeline, Qt.LeftButton, pos=QPoint(512, 30))
        self.assertAlmostEqual(timeline.start, 5)
        QTest.mousePress(timeline, Qt.LeftButton, pos=QPoint(1012, 30))
        QTest.mouseRelease(timeline, Qt.LeftButton, pos=QPoint(20, 30))
        self.assertGreater(timeline.end, timeline.start)
        timeline.close()

    def test_category_appearance_profiles_and_order(self):
        cat = dict(name='X', action='rename', shortcut='1', destination='', color='#ff0000', icon='film')
        self.assertEqual(normalize_categories([cat])[0], cat)
        main = MediaCategorizer()
        original = [c['name'] for c in main.categories]
        main.reorder_category(original[0], '')
        self.assertEqual(main.categories[-1]['name'], original[0])
        dialog = CategoriesDialog([cat], main)
        self.assertEqual(dialog.snapshot()[0]['color'], '#ff0000')
        self.assertEqual(dialog.snapshot()[0]['icon'], 'film')
        dialog.reject()
        main.close()

    def test_new_video_options_and_timeline_assets(self):
        path = self.root / 'video.mp4'
        ffmpeg('-f','lavfi','-i','testsrc2=size=320x180:rate=24:duration=1',
               '-f','lavfi','-i','sine=duration=1','-c:v','libx264','-c:a','aac',path)
        assets = TimelineAssets(path, 1, True)
        assets.start()
        self.assertTrue(assets.wait(15000))
        self.assertTrue(assets.images)
        self.assertFalse(assets.waveform.isNull())
        for options, size, fps in [(ExportOptions('1080',24,'compact'),(1920,1080),'24/1'),
                                   (ExportOptions('source',0,'high'),(1920,1080),'24/1')]:
            VideoExport().run(path, 0, .5, 1, signature(path), options=options)
            info = probe(path)
            self.assertEqual((info['width'],info['height']), size)
            self.assertEqual(info['stream']['avg_frame_rate'], fps)
        info['stream']['avg_frame_rate'] = '0/0'
        self.assertGreater(ExportOptions(fps=0).estimate_bytes(info, 1), 0)

    def test_update_validation_download_checksum_and_cancel(self):
        payload = b'fixture executable'
        release = dict(version='6.0.0', filename='MediaCategorizer-Update-6.0.0.exe', url='https://example.com/update', sha256=hashlib.sha256(payload).hexdigest())
        def response(*args):
            stream = io.BytesIO(payload)
            stream.headers = {'Content-Length':str(len(payload))}
            return stream
        with patch.object(updates, 'request', response):
            downloaded = Path(updates.download_release(release))
            self.assertEqual(downloaded.read_bytes(), payload)
            with self.assertRaises(ValueError):
                updates.download_release(dict(release, sha256='0'*64))
            self.assertEqual(downloaded.read_bytes(), payload)
            with self.assertRaises(ValueError):
                updates.download_release(release, cancelled=lambda: True)
            self.assertFalse(list(downloaded.parent.glob('*.part')))
        with self.assertRaises(ValueError):
            updates.download_release(dict(release, version='../../escape'))
        with self.assertRaises(ValueError):
            updates.request('http://example.com')
        self.assertGreater(updates.version_tuple('6.0.0'), updates.version_tuple('5.2.0'))

    def test_update_release_discovery(self):
        checksum = 'a'*64
        data = {'tag_name':'v6.0.0', 'body':'Release notes', 'assets':[
            {'name':name,'browser_download_url':'https://example.com/'+name}
            for name in ('MediaCategorizer-Update-6.0.0.exe','SHA256SUMS.txt')]}
        with patch.object(updates, 'request', side_effect=[io.BytesIO(json.dumps(data).encode()), io.BytesIO((checksum+'  MediaCategorizer-Update-6.0.0.exe').encode())]):
            result = updates.latest_release('AdlerDaniel/MediaCategorizer')
        self.assertEqual(result['sha256'], checksum)
        self.assertEqual(result['notes'], 'Release notes')
