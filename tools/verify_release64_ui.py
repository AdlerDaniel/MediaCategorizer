"""Native own-widget captures and actual edited-video playback/save verification."""
import os
import sys
import json
import tempfile
import time
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM'] = 'windows'
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QMessageBox
from media_categorizer_v4_5 import MediaCategorizer
from media_categorizer.video_editor import VideoEditor
from media_categorizer.photo_editor import PhotoEditor
from media_categorizer.appearance import AppearanceDialog
from media_categorizer.ui import apply_theme, THEME_NAMES, UI_SIZES
from media_categorizer.video_export import tool, probe, CREATE_FLAGS

app = QApplication([])
output = Path(__file__).resolve().parents[1]/'build/qa64'
output.mkdir(parents=True, exist_ok=True)

def settle(seconds=.15):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.01)

with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    os.environ['APPDATA'] = str(folder/'settings')
    photo_path, video_path = folder/'photo.png', folder/'video.mp4'
    image = QImage(800, 600, QImage.Format_RGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            image.setPixelColor(x,y,QColor(x*255//800,y*255//600,140))
    image.save(str(photo_path))
    subprocess.run([tool('ffmpeg'), '-v','error','-y','-f','lavfi','-i','testsrc2=size=640x360:rate=30:duration=4', '-f','lavfi','-i','sine=duration=4','-c:v','libx264','-c:a','aac',str(video_path)], check=True, creationflags=CREATE_FLAGS)
    main = MediaCategorizer()
    main.load_folder(folder, selected=photo_path)
    photo = PhotoEditor(photo_path, main)
    video = VideoEditor(video_path, main)
    appearance = AppearanceDialog(main)
    windows = [('main',main),('photo',photo),('video',video),('appearance',appearance)]
    for name, widget in windows:
        widget.setAttribute(Qt.WA_ShowWithoutActivating)
        widget.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
    main.showNormal()
    settle()
    video.show()
    video.player.play()
    settle(1)
    video.player.pause()
    assert video.video.videoSink().videoFrame().isValid()
    video.cut_start.setValue(1)
    video.cut_end.setValue(2)
    video.add_cut()
    video.rotation.setCurrentIndex(1)
    video.crop_ratio.setCurrentIndex(2)
    video.mirror.setChecked(True)
    video.normalize.setChecked(True)
    video.range_timeline.set_zoom(4)
    photo.canvas.set_ratio(.8)
    sizes = {}
    for size in UI_SIZES:
        for theme in THEME_NAMES:
            apply_theme(theme, size, size == 'large')
            for name, widget in windows:
                widget.resize(1366, 768) if name == 'main' else widget.resize(1000, 780) if name in ('photo','video') else None
                widget.showNormal()
                settle(.08)
                sizes[f'{name}-{size}-{theme}'] = [widget.width(),widget.height(),widget.minimumSizeHint().width(),widget.minimumSizeHint().height()]
                assert widget.width() <= 1366, (name,size,widget.width())
                assert widget.grab().save(str(output/f'{name}-{size}-{theme}.png'))
                if name != 'main':
                    widget.hide()
    video.show()
    for index in (1,2):
        video.tabs.setCurrentIndex(index)
        settle()
        video.grab().save(str(output/f'video-tab-{index}.png'))
    errors = []
    QMessageBox.warning = lambda *args: errors.append(str(args))
    main.stop_all_video()  # Same release of preloaded handles as the main Edit action.
    video.save()
    end = time.monotonic()+40
    while video.worker and time.monotonic() < end:
        settle(.05)
    assert not video.worker and not errors, errors
    assert video.result() == VideoEditor.Accepted
    result = probe(video_path)
    assert (result['width'],result['height']) == (720,1280)
    assert abs(result['duration']-3) < .1
    (output/'report.json').write_text(json.dumps(dict(sizes=sizes, video=[result['width'],result['height'],result['duration']]),indent=2))
    photo.canvas.clear_selection()
    for name, widget in reversed(windows):
        widget.close()
    main.thread_pool.waitForDone()
    settle()
print('All six themes, three sizes, labels, native transformed video preview and atomic video save: OK')
