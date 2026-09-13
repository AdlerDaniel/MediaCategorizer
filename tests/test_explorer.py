import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QEvent,QItemSelectionModel
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication,QInputDialog,QMessageBox
from PySide6.QtTest import QTest
from media_categorizer.explorer import ExplorerDialog,shell_transfer,valid_name
from test_gui import TestWindow,HeldQueue

class ExplorerTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.env=patch.dict(os.environ,{'APPDATA':str(self.root/'config')});self.env.start()
  self.folder=self.root/'files';self.folder.mkdir();(self.folder/'nested').mkdir()
  self.photo=self.folder/'photo.png';im=QImage(40,30,QImage.Format_RGB32);im.fill(Qt.red);im.save(str(self.photo))
  (self.folder/'notes.txt').write_text('original');self.main=TestWindow();self.main.thread_pool=HeldQueue();self.main.settings['last_folder']=str(self.folder)
  self.dialog=ExplorerDialog(self.main);self.dialog.show();self.wait(lambda:self.count()==3)
 def tearDown(self):
  if self.dialog.worker:self.wait(lambda:self.dialog.worker is None)
  self.dialog.reject();self.dialog.deleteLater();self.main.close();self.main.deleteLater()
  self.app.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents();self.env.stop();self.temp.cleanup()
 def count(self):
  t=self.dialog.current();return t.proxy.rowCount(t.view().rootIndex())
 def wait(self,fn):
  end=time.monotonic()+15
  while not fn() and time.monotonic()<end:self.app.processEvents();time.sleep(.01)
  self.assertTrue(fn())
 def select(self,path):
  t=self.dialog.current();index=t.proxy.mapFromSource(t.model.index(str(path)))
  self.assertTrue(index.isValid());t.view().setFocus();t.view().selectionModel().select(index,QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
  self.app.processEvents()
 def test_navigation_search_tabs_and_address_enter(self):
  d=self.dialog;d.search.setText('photo');self.assertEqual(self.count(),1)
  d.navigate(self.folder/'nested');self.assertEqual(d.current().folder,self.folder/'nested')
  d.travel(-1);self.wait(lambda:self.count()==3);d.travel(1);self.assertEqual(d.current().folder,self.folder/'nested')
  d.add_tab(self.folder);self.assertEqual(d.tabs.count(),2);d.close_tab(1);self.assertEqual(d.tabs.count(),1)
  d.address.setFocus();d.address.setText(str(self.folder));QTest.keyClick(d.address,Qt.Key_Return)
  self.assertEqual(d.current().folder,self.folder)
 def test_selection_survives_view_switch(self):
  self.select(self.photo);self.dialog.set_mode(True);self.assertEqual(self.dialog.current().selected(),[self.photo])
  self.dialog.set_mode(False);self.assertEqual(self.dialog.current().selected(),[self.photo])
 def test_open_media_in_main_viewer(self):
  self.select(self.photo);self.dialog.open_selected();self.assertEqual(self.main.current_file,self.photo)
  self.assertEqual(self.dialog.result(),ExplorerDialog.Accepted)
 def test_create_and_rename_refuse_overwrite(self):
  d=self.dialog
  with patch.object(QInputDialog,'getText',return_value=('new folder',True)):d.create_folder()
  self.assertTrue((self.folder/'new folder').is_dir())
  self.select(self.folder/'notes.txt')
  with patch.object(QInputDialog,'getText',return_value=('photo.png',True)),patch.object(QMessageBox,'warning') as warning:d.rename_selection()
  self.assertTrue(warning.called);self.assertEqual((self.folder/'notes.txt').read_text(),'original')
  self.select(self.folder/'notes.txt')
  with patch.object(QInputDialog,'getText',return_value=('renamed.txt',True)):d.rename_selection()
  self.assertEqual((self.folder/'renamed.txt').read_text(),'original')
 def test_clipboard_copy_and_paste(self):
  self.select(self.folder/'notes.txt');self.dialog.copy_selection(False)
  mime=self.app.clipboard().mimeData();self.assertTrue(mime.hasUrls())
  self.assertEqual(bytes(mime.data('Preferred DropEffect')),b'\x01\x00\x00\x00')
  self.dialog.navigate(self.folder/'nested');self.dialog.current().view().setFocus();self.dialog.paste_selection()
  self.wait(lambda:self.dialog.worker is None)
  self.assertEqual((self.folder/'nested/notes.txt').read_text(),'original')
 def test_native_windows_cut_format_moves_file(self):
  from PySide6.QtCore import QMimeData,QUrl
  source=self.folder/'notes.txt';mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(source))])
  mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',(2).to_bytes(4,'little'))
  self.app.clipboard().setMimeData(mime);self.dialog.navigate(self.folder/'nested');self.dialog.current().view().setFocus()
  self.dialog.paste_selection();self.wait(lambda:self.dialog.worker is None)
  self.assertFalse(source.exists());self.assertEqual((self.folder/'nested/notes.txt').read_text(),'original')
 def test_text_clipboard_shortcut_is_not_file_command(self):
  self.dialog.address.setFocus();self.dialog.address.setText('text to copy');self.dialog.address.selectAll()
  QTest.keyClick(self.dialog.address,Qt.Key_C,Qt.ControlModifier)
  self.assertEqual(self.app.clipboard().text(),'text to copy')
 def test_recycle_requires_confirmation(self):
  self.select(self.folder/'notes.txt')
  with patch.object(QMessageBox,'question',return_value=QMessageBox.No):self.dialog.trash_selection()
  self.assertTrue((self.folder/'notes.txt').exists())
 def test_invalid_names(self):
  for name in ('','..','NUL.txt','COM1','name.','name ','a/b','a\\b','a:b'):
   with self.subTest(name=name),self.assertRaises(ValueError):valid_name(name)
  self.assertEqual(valid_name('Фото 1.png'),'Фото 1.png')

@unittest.skipUnless(os.name=='nt','Windows shell')
class ShellTests(unittest.TestCase):
 def test_recursive_copy_collision_and_move(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'source';source.mkdir();(source/'sub').mkdir();(source/'sub/file.txt').write_text('important')
   destination=root/'destination';destination.mkdir();(destination/'source').mkdir();(destination/'source/existing.txt').write_text('keep')
   shell_transfer([source],destination)
   self.assertEqual((destination/'source/existing.txt').read_text(),'keep')
   self.assertEqual(len(list(destination.rglob('file.txt'))),1)
   self.assertEqual(len(list(destination.iterdir())),2)
   moved=root/'moved';moved.mkdir();shell_transfer([source],moved,True)
   self.assertFalse(source.exists());self.assertEqual((moved/'source/sub/file.txt').read_text(),'important')
 def test_recursive_self_copy_refused(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):shell_transfer([Path(d)],Path(d))
