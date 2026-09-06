"""Native video-editor smoke with isolated generated media."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM'] = 'windows'
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QMessageBox
from media_categorizer.ui import apply_theme, THEME_NAMES
from media_categorizer.video_editor import VideoEditor
from media_categorizer.video_export import tool, probe, CREATE_FLAGS
root = Path(__file__).resolve().parents[1]
output = root / 'build/qa'
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
apply_theme('dark')
def settle(seconds):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.01)
with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / 'video.mp4'
    subprocess.run([tool('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=3', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', '-c:v', 'libx264', '-c:a', 'aac', str(path)], check=True, creationflags=CREATE_FLAGS)
    editor = VideoEditor(path)
    editor.setAttribute(Qt.WA_ShowWithoutActivating)
    editor.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
    editor.show()
    editor.player.play()
    settle(1)
    editor.player.pause()
    editor.start.setValue(.5)
    editor.end.setValue(2)
    editor.volume.setValue(150)
    settle(.2)
    for theme in THEME_NAMES:
        apply_theme(theme)
        settle(.2)
        editor.grab().save(str(output / ('video-editor-' + theme + '.png')))
        frame = editor.video.videoSink().videoFrame()
        assert frame.isValid(), 'Video preview did not decode a frame'
        frame.toImage().save(str(output / 'video-editor-frame.png'))
    errors = []
    QMessageBox.warning = lambda *args: errors.append(str(args))
    editor.save()
    deadline = time.monotonic()+40
    while editor.worker and time.monotonic() < deadline:
        settle(.05)
    assert not editor.worker and not errors, errors
    assert editor.result() == VideoEditor.Accepted
    result = probe(path)
    report = dict(width=result['width'], height=result['height'], fps=result['stream']['avg_frame_rate'], duration=result['duration'], volume=150, extra_files=len(list(path.parent.iterdir()))-1)
    (output / 'video-editor-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    editor.deleteLater()
    settle(.1)
