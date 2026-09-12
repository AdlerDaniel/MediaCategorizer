import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMessageBox
from media_categorizer.file_operations import perform_file_operation, remove_unchanged_copy, file_state
from media_categorizer.library import processed_paths, path_key
from media_categorizer.video_editor import VideoEditor
from media_categorizer.video_export import ExportOptions, VideoExport, probe, signature
from media_categorizer.batch import BatchDialog
from media_categorizer_v4_5 import oriented_video_frame_image
from test_gui import TestWindow, HeldQueue
from test_video_editor import ffmpeg


class DataSafetyTests(unittest.TestCase):
    def test_copy_receipt_protects_edited_and_replaced_files(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d)/'source', Path(d)/'copy'
            source.write_bytes(b'original')
            receipt = {}
            perform_file_operation('copy', source, target, receipt=receipt)
            self.assertEqual(file_state(target), receipt['result_state'])
            target.write_bytes(b'edited original')
            with self.assertRaises(OSError):
                remove_unchanged_copy(target, receipt['result_state'])
            self.assertEqual(target.read_bytes(), b'edited original')
            target.unlink()
            target.write_bytes(b'replacement')
            with self.assertRaises(OSError):
                remove_unchanged_copy(target, receipt['result_state'])
            self.assertEqual(target.read_bytes(), b'replacement')
            target.unlink()
            perform_file_operation('copy', source, target, receipt=receipt)
            remove_unchanged_copy(target, receipt['result_state'])
            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), b'original')

    def test_cross_volume_external_writer_cannot_corrupt_move(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d)/'source', Path(d)/'target'
            original = b'A'*(20*1024*1024)
            source.write_bytes(original)
            attempted = []
            def mutate(*_):
                if attempted: return
                attempted.append(True)
                try: source.write_bytes(b'B'*len(original))
                except PermissionError: attempted.append('blocked')
            with patch('media_categorizer.file_operations._same_device', return_value=False):
                if os.name == 'nt':
                    perform_file_operation('move', source, target, mutate)
                    self.assertIn('blocked', attempted)
                    self.assertEqual(target.read_bytes(), original)
                    self.assertFalse(source.exists())
                else:
                    with self.assertRaises(OSError): perform_file_operation('move', source, target, mutate)
                    self.assertTrue(source.exists())
                    self.assertFalse(target.exists())
            self.assertFalse(list(Path(d).glob('*.part')))

    @unittest.skipUnless(os.name == 'nt', 'Windows share-mode protection')
    def test_already_open_writer_prevents_cross_volume_move(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d)/'source', Path(d)/'target'
            source.write_bytes(b'original')
            with source.open('r+b') as writer, patch('media_categorizer.file_operations._same_device', return_value=False):
                with self.assertRaises(OSError): perform_file_operation('move', source, target)
                writer.seek(0)
                self.assertEqual(writer.read(), b'original')
            self.assertFalse(target.exists())


class AuditGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'APPDATA': str(self.root/'config')}); self.env.start()
        self.windows = []
        self.photo = self.root/'photo.png'
        im=QImage(400,300,QImage.Format_RGB32); im.fill(Qt.red); im.save(str(self.photo))
    def tearDown(self):
        for w in reversed(self.windows):
            with patch.object(QMessageBox,'question',return_value=QMessageBox.Discard): w.close()
            w.deleteLater()
        self.app.sendPostedEvents(None,QEvent.DeferredDelete); self.app.processEvents()
        self.env.stop(); self.temp.cleanup()
    def wait(self,predicate):
        end=time.monotonic()+30
        while not predicate() and time.monotonic()<end:
            self.app.processEvents(); time.sleep(.01)
        self.assertTrue(predicate())
    def main(self):
        w=TestWindow(); w.thread_pool=HeldQueue(); self.windows.append(w)
        w.load_folder(self.root,selected=self.photo)
        return w
    def video(self,rotated=False):
        path=self.root/'source.mp4'
        ffmpeg('-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=2',
               '-f','lavfi','-i','sine=duration=2','-c:v','libx264','-c:a','aac',path)
        if rotated:
            target=self.root/'rotated.mp4'
            ffmpeg('-display_rotation','90','-i',path,'-c','copy',target); path=target
        v=VideoEditor(path); self.windows.append(v); v.show()
        return v
    def test_main_undo_retains_modified_copy_and_history(self):
        w=self.main();target=self.root/'copy.png'
        w.enqueue_file_operation('copy',self.photo,target,'COPY',0,False)
        w.thread_pool.tasks.pop(0).run(); self.app.processEvents()
        self.assertIsNotNone(w.undo_stack[-1]['result_state'])
        target.write_bytes(b'new user content')
        with patch.object(QMessageBox,'critical') as warning: w.undo_last_action()
        self.assertTrue(warning.called); self.assertTrue(w.undo_stack)
        self.assertEqual(target.read_bytes(),b'new user content')
    def test_cache_invalidates_external_replacement(self):
        w=self.main(); im=QImage(str(self.photo)); w._cache_image(self.photo,im)
        self.assertFalse(w._take_cached_image(self.photo).isNull())
        im.fill(Qt.blue); im.save(str(self.photo))
        self.assertTrue(w._take_cached_image(self.photo).isNull())
        w._request_image_preload(self.photo)
        self.assertIn(str(self.photo),w.pending_image_loads)
        for task in w.thread_pool.tasks: task.run()
        self.app.processEvents()
        self.assertEqual(w._take_cached_image(self.photo).pixelColor(0,0).name(),'#0000ff')
    def test_original_crop_and_unsaved_close_choices(self):
        v=self.video();v.custom_crop=(.1,.1,.5,.5);v.update_controls()
        v.ratio_cards.buttons[0].click();self.assertEqual(v.custom_crop,())
        v.volume.setValue(30)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Cancel):v.reject()
        self.assertTrue(v.isVisible())
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Save), patch.object(v,'save') as save:v.reject()
        save.assert_called_once()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Discard):v.reject()
        self.assertFalse(v.isVisible())
    def test_early_zoom_still_loads_waveform_and_native_rotation(self):
        v=self.video(rotated=True);v.range_timeline.set_zoom(4)
        v.refresh_assets()
        self.wait(lambda:not v.range_timeline.waveform.isNull() and not v.asset_timer.isActive() and not v.assets.isRunning())
        self.wait(lambda:not v.warming_preview)
        frame=v.video.videoSink().videoFrame()
        self.assertEqual(oriented_video_frame_image(frame).size(),frame.toImage().size())
        self.assertLess(frame.toImage().width(),frame.toImage().height())
    def test_batch_can_prepare_and_run_second_operation(self):
        w=self.main(); b=BatchDialog([self.photo],w);self.windows.append(b)
        b.mode.setCurrentIndex(2);b.prepare();b.start();self.wait(lambda:b.worker is None)
        self.assertTrue(b.mode.isEnabled());self.assertTrue(b.prepare_btn.isEnabled())
        self.assertFalse(b.start_btn.isEnabled())
        b.prepare();self.assertTrue(b.start_btn.isEnabled());b.start();self.wait(lambda:b.worker is None)
        self.assertEqual(len(b.results),1);self.assertEqual(b.results[0]['status'],'OK')
    def test_skips_and_undo_do_not_count_as_processing(self):
        for action in ('SKIP','UNDO','QUEUE'):
            self.assertNotIn(path_key(self.photo),processed_paths([dict(action=action,status='OK',source=str(self.photo))]))
        for action in ('COPY','RENAME','EDIT','EDIT_VIDEO','BATCH_FLIP_H'):
            self.assertIn(path_key(self.photo),processed_paths([dict(action=action,status='OK',source=str(self.photo))]))
    def test_square_export_matches_preview_aspect(self):
        path=self.root/'square.mp4'
        ffmpeg('-f','lavfi','-i','color=red:size=240x240:rate=30:duration=1','-c:v','libx264',path)
        VideoExport().run(path,0,1,1,signature(path))
        result=probe(path);self.assertEqual((result['width'],result['height']),(720,720))
        for w,h in ((640,480),(480,640),(1920,1080),(1080,1920)):
            x,y=ExportOptions().dimensions(dict(width=w,height=h))
            self.assertAlmostEqual(x/y,w/h,delta=.003)
