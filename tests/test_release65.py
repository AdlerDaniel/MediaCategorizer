import os,tempfile,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtCore import Qt,QPoint,QPointF,QRect,QEvent
from PySide6.QtGui import QImage,QWheelEvent
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtTest import QTest
from media_categorizer.photo_editor import CropCanvas,PhotoEditor
from media_categorizer.editor_widgets import PreviewHost
from media_categorizer.video_export import ExportOptions
from media_categorizer.timeline import RangeTimeline

class Release65Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def test_crop_geometry_and_invalid_bounds(self):
  info={'width':360,'height':640}
  options=ExportOptions('source',crop_rect=(.1,.1,.6,.7))
  self.assertEqual(options.crop_geometry(info),(216,448,36,64))
  self.assertEqual(options.dimensions(info),(216,448))
  for crop in ((.9,0,.2,1),(0,0,0,1),(float('nan'),0,1,1)):
   with self.assertRaises(ValueError):ExportOptions(crop_rect=crop).validate()
 def test_fragment_selection_and_wheel_modifiers(self):
  t=RangeTimeline(10);t.resize(800,160);t.splits=[2,6];t.show();self.app.processEvents()
  self.assertEqual(t.fragments(),[(0.,2),(2,6),(6,10.)])
  QTest.mouseClick(t,Qt.LeftButton,pos=QPoint(round(t.x(4)),70))
  self.assertEqual(t.selected,1)
  t.set_zoom(4)
  for modifier in (Qt.NoModifier,Qt.ControlModifier):
   oldzoom,oldoffset=t.zoom,t.offset
   wheel=QWheelEvent(QPointF(300,60),QPointF(300,60),QPoint(),QPoint(0,-120),Qt.NoButton,modifier,Qt.NoScrollPhase,False)
   self.app.sendEvent(t,wheel)
   if modifier==Qt.NoModifier:self.assertEqual(t.zoom,oldzoom);self.assertGreater(t.offset,oldoffset)
   else:self.assertLess(t.zoom,oldzoom)
  t.close();t.deleteLater()
 def test_crop_side_handles_resize_without_moving_opposite_edge(self):
  image=QImage(400,300,QImage.Format_RGB32);image.fill(Qt.red)
  for side,start,end in [('left',QPoint(50,150),QPoint(80,150)),('right',QPoint(350,150),QPoint(320,150)),('top',QPoint(200,50),QPoint(200,80)),('bottom',QPoint(200,250),QPoint(200,220))]:
   canvas=CropCanvas(image);canvas.resize(400,300);canvas.selection=QRect(50,50,300,200);canvas.show();self.app.processEvents()
   QTest.mousePress(canvas,Qt.LeftButton,pos=start);QTest.mouseMove(canvas,end);QTest.mouseRelease(canvas,Qt.LeftButton,pos=end)
   self.assertEqual(canvas.selection.width() if side in ('left','right') else canvas.selection.height(),270 if side in ('left','right') else 170)
   canvas.close();canvas.deleteLater()
 def test_horizon_preview_undo_restores_source(self):
  with tempfile.TemporaryDirectory() as d:
   image=QImage(400,300,QImage.Format_RGB32);image.fill(Qt.red);path=Path(d)/'photo.png';image.save(str(path))
   editor=PhotoEditor(path);editor.canvas.set_ratio(1);editor.tools.setCurrentIndex(2);editor.angle.setValue(10)
   self.assertNotEqual(editor.canvas.image.size(),image.size())
   self.assertFalse(editor.canvas.crop_enabled);self.assertTrue(editor.canvas.selection.isEmpty())
   editor.undo();self.assertEqual(editor.canvas.image,image);self.assertEqual(editor.angle.value(),0)
   editor.reject();editor.deleteLater()
 def test_fixed_and_floating_controls(self):
  for floating in (False,True):
   host=PreviewHost(QWidget(),floating=floating);host.resize(600,400);host.show();self.app.processEvents();host.reveal()
   self.assertAlmostEqual(host.effect.opacity(),.5 if floating else 1)
   if floating:self.assertFalse(host.bar.mask().isEmpty())
   else:self.assertEqual(host.layout().indexOf(host.bar),1);host.fade();self.assertEqual(host.effect.opacity(),1)
   host.close();host.deleteLater()
