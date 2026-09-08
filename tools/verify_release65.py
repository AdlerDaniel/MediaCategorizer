"""Native regression checks using generated media only and own-widget captures."""
import os,sys,tempfile,time,subprocess,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM']='windows'
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent,QTimer
from PySide6.QtGui import QImage,QColor,QWheelEvent
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
from PySide6.QtMultimedia import QMediaPlayer
from media_categorizer.video_export import tool,CREATE_FLAGS,probe,ExportOptions,VideoExport,signature
from media_categorizer.video_editor import VideoEditor
from media_categorizer.photo_editor import PhotoEditor,CropCanvas
from media_categorizer.video_crop import VideoCropDialog
from media_categorizer_v4_5 import MediaCategorizer
app=QApplication([])
out=Path('build/qa65');out.mkdir(parents=True,exist_ok=True)
def settle(seconds=.2):
 end=time.monotonic()+seconds
 while time.monotonic()<end:app.processEvents();time.sleep(.01)
def wait(predicate,seconds=15):
 end=time.monotonic()+seconds
 while not predicate() and time.monotonic()<end:settle(.05)
 assert predicate()
def ff(*args):subprocess.run([tool('ffmpeg'),'-v','error','-y',*map(str,args)],check=True,creationflags=CREATE_FLAGS)
with tempfile.TemporaryDirectory(prefix='mc-qa65-') as folder:
 root=Path(folder);os.environ['APPDATA']=str(root/'settings')
 landscape,portrait=root/'landscape.mp4',root/'portrait.mp4'
 ff('-f','lavfi','-i','testsrc2=size=640x360:rate=30:duration=4','-f','lavfi','-i','sine=duration=4','-c:v','libx264','-c:a','aac',landscape)
 ff('-display_rotation','90','-i',landscape,'-c','copy',portrait)
 assert probe(portrait)['height']>probe(portrait)['width']
 video=VideoEditor(portrait);video.setAttribute(Qt.WA_ShowWithoutActivating);video.show();settle()
 wait(lambda:not video.warming_preview)
 wait(lambda:video.player.playbackState()==QMediaPlayer.PausedState)
 assert video.video.videoSink().videoFrame().isValid()
 assert video.video.sceneRect().height()>video.video.sceneRect().width()
 video.grab().save(str(out/'portrait-initial.png'))
 def confirm_crop():
  frame=video.video.videoSink().videoFrame()

  dialog=app.activeModalWidget()
  assert isinstance(dialog,VideoCropDialog)
  assert dialog.canvas.image==frame.toImage().scaled(round(video.info["width"]),round(video.info["height"]),Qt.IgnoreAspectRatio,Qt.SmoothTransformation)
  dialog.ratios.setCurrentIndex(1)
  dialog.canvas.set_ratio(1)
  dialog.grab().save(str(out/'video-crop-dialog.png'))
  dialog.accept()
 QTimer.singleShot(150,confirm_crop)
 video.open_crop();assert video.custom_crop
 video.custom_crop=();video.update_controls()
 video.player.setPosition(1000);settle();video.trim_at_cursor(True)
 assert video.start.value()==1
 video.undo_montage();assert video.start.value()==0
 video.player.setPosition(3000);settle();video.trim_at_cursor(False)
 assert video.end.value()==3
 video.undo_montage();assert video.end.value()==4
 video.player.setPosition(1000);settle();video.split_fragment()
 video.player.setPosition(2000);settle();video.split_fragment()
 assert len(video.range_timeline.fragments())==3,video.range_timeline.fragments()
 t=video.range_timeline
 QTest.mouseClick(t,Qt.LeftButton,pos=QPoint(round(t.x(1.5)),65))
 assert t.selected==1,t.selected
 video.remove_fragment();assert video.options().segments(0,4)==[(0,1.),(2.,4.)]
 video.undo_montage();assert not video.cuts
 t.set_zoom(8);settle(.3);wait(lambda:video.assets is not None and not video.assets.isRunning())
 assert video.assets.span==.5,video.assets.span
 video.grab().save(str(out/'timeline-zoom.png'))
 wheel=QWheelEvent(QPointF(200,60),QPointF(200,60),QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
 before=t.offset;app.sendEvent(t,wheel);assert t.offset>before
 zoom=t.zoom
 wheel=QWheelEvent(QPointF(200,60),QPointF(200,60),QPoint(),QPoint(0,120),Qt.NoButton,Qt.ControlModifier,Qt.NoScrollPhase,False)
 app.sendEvent(t,wheel);assert t.zoom>zoom
 video.resize(1180,820);video.move(55,65);settle();geometry=video.geometry()
 video.reject();settle()
 again=VideoEditor(portrait);again.show();settle();assert again.size()==geometry.size(),(again.size(),geometry)
 again.showMaximized();settle();again.reject();settle()
 maximized=VideoEditor(portrait);maximized.show();settle();assert maximized.isMaximized()
 maximized.reject();settle()
 image=QImage(640,480,QImage.Format_RGB32);image.fill(QColor('orange'));image.save(str(root/'photo.png'))
 photo=PhotoEditor(root/'photo.png');photo.setAttribute(Qt.WA_ShowWithoutActivating);photo.show();settle()
 photo.canvas.set_ratio(1);photo.tools.setCurrentIndex(2);photo.angle.setValue(12)
 assert photo.canvas.image.size()!=photo.original.size()
 assert photo.canvas.selection.isEmpty() and not photo.canvas.crop_enabled
 photo.grab().save(str(out/'photo-horizon.png'))
 photo.undo();photo.tools.setCurrentIndex(0);photo.canvas.set_ratio(1)
 canvas=photo.canvas;r=canvas.image_rect();selection=canvas.selection
 x=r.x()+selection.x()*r.width()/image.width();y=r.center().y()
 QTest.mousePress(canvas,Qt.LeftButton,pos=QPoint(round(x),round(y)))
 QTest.mouseMove(canvas,QPoint(round(x+30),round(y)))
 QTest.mouseRelease(canvas,Qt.LeftButton,pos=QPoint(round(x+30),round(y)))
 assert canvas.selection.width()<selection.width()
 photo.canvas.clear_selection();photo.reject()
 main=MediaCategorizer();main.setAttribute(Qt.WA_ShowWithoutActivating);main.showNormal();main.load_folder(root,selected=landscape)
 wait(lambda:main.video_canvas.videoSink().videoFrame().isValid())
 main.pause_active_video() if hasattr(main,'pause_active_video') else main.video_slots[main.active_video_slot]['player'].pause()
 main.rotate_current_view(90);settle()
 frame=main.video_canvas.videoSink().videoFrame()
 assert main.video_canvas.sceneRect().height()>main.video_canvas.sceneRect().width()
 main.grab().save(str(out/'main-rotated.png'))
 main.rotate_current_view(-90);settle()
 assert main.video_canvas.manual_rotation==0
 assert main.video_canvas.sceneRect().width()>main.video_canvas.sceneRect().height()
 main.close();main.thread_pool.waitForDone();settle()
 for widget in (video,again,maximized,photo,main):widget.deleteLater()
 app.sendPostedEvents(None,QEvent.DeferredDelete);settle(.5)
 options=ExportOptions('source',crop_rect=(.1,.1,.6,.7))
 expected=options.geometry(probe(portrait))
 VideoExport().run(portrait,0,4,1,signature(portrait),options=options)
 info=probe(portrait);assert (info['width'],info['height'])==expected,(info,expected)
 print(json.dumps({'initial_portrait_frame':True,'split_select_delete_undo':True,'wheel_pan_ctrl_zoom':True,'dense_thumbnails':True,'geometry_restored':True,'photo_horizon_edges':True,'main_rotation':True,'custom_crop_export':expected}),flush=True)
