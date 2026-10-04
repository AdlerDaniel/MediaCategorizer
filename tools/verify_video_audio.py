"""Native viewer checks: waveform, seeking, all themes, and one-click silent video."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM'] = 'windows'
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from media_categorizer_v4_5 import MediaCategorizer
from media_categorizer.ui import apply_theme, THEME_NAMES, UI_SIZES
from media_categorizer.video_audio import AudioWaveform
from media_categorizer.video_export import CREATE_FLAGS, probe, signature, tool

app = QApplication([])
output = Path(__file__).resolve().parents[1] / 'build/qa-video-audio'
output.mkdir(parents=True, exist_ok=True)


def settle(seconds=.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)


def wait_for(condition, seconds=30):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        settle(.03)
    assert condition(), 'Native check timed out'


with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    os.environ['APPDATA'] = str(folder / 'config')
    path = folder / 'video.mp4'
    subprocess.run([tool('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=640x360:rate=30:duration=5', '-f', 'lavfi', '-i',
                    r'aevalsrc=if(lt(t\,1)\,0\,if(lt(t\,3)\,0.2\,0.65))*sin(2*PI*440*t):d=5',
                    '-c:v', 'libx264', '-c:a', 'aac', str(path)], check=True, creationflags=CREATE_FLAGS)
    window = MediaCategorizer()
    window.setAttribute(Qt.WA_ShowWithoutActivating)
    window.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
    window.load_folder(folder, selected=path)
    window.showNormal()
    wait_for(lambda: bool(window.waveform.peaks))
    player = window._active_player()
    wait_for(lambda: player.duration() > 0 and window.video_canvas.videoSink().videoFrame().isValid())
    player.pause()
    layouts = {}
    for size in UI_SIZES:
        for theme in THEME_NAMES:
            apply_theme(theme, size, size == 'large')
            for width in (900, 1366):
                window.resize(width, 768)
                settle(.08)
                assert window.width() <= width
                assert window.waveform.width() == window.timeline.width()
                assert window.waveform.mapToGlobal(QPoint()).y() > window.timeline.mapToGlobal(QPoint()).y()
                for toolbar in (window.top_widget, window.tools_widget):
                    if width == 1366:
                        assert toolbar.horizontalScrollBar().maximum() == 0
                rect = window.waveform.waveform_rect()
                QTest.mouseClick(window.waveform, Qt.LeftButton, pos=QPoint(round(rect.center().x()), round(rect.center().y())))
                settle(.03)
                assert abs(player.position() - 2500) < 120
                assert window.grab().save(str(output / f'{theme}-{size}-{width}.png'))
                layouts[f'{theme}-{size}-{width}'] = [window.waveform.width(), window.waveform.height()]
    apply_theme('green', 'normal', False)
    window.resize(1366, 768)
    settle()
    window.remove_audio_btn.click()
    assert window.render_progress.isVisible()
    assert not window.edit_photo_btn.isEnabled()
    assert window.grab().save(str(output / 'render-progress.png'))
    wait_for(lambda: window.active_audio_removal is None)
    assert not probe(path)['audio']
    wait_for(lambda: window._active_player() is not None and window.video_canvas.videoSink().videoFrame().isValid())
    assert window.waveform.message == 'В видео нет звуковой дорожки'
    assert abs(window._active_player().position() - 2500) < 120
    assert window.grab().save(str(output / 'without-audio.png'))
    window.close()
    window.thread_pool.waitForDone()
    settle()
    timings = {}
    for duration in (5, 30, 120):
        benchmark = folder / f'bench-{duration}.mp4'
        subprocess.run([tool('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        f'color=size=160x120:rate=1:duration={duration}', '-f', 'lavfi', '-i',
                        f'sine=duration={duration}', '-c:v', 'libx264', '-c:a', 'aac', str(benchmark)],
                       check=True, creationflags=CREATE_FLAGS)
        started = time.monotonic()
        peaks, _ = AudioWaveform().run(benchmark, signature(benchmark))
        timings[duration] = round(time.monotonic() - started, 3)
        assert max(peaks) > .05
    (output / 'report.json').write_text(json.dumps(dict(layouts=layouts, waveform_seconds=timings), indent=2))
print('Native waveform seeking, all themes and sizes, silent replacement and reload: OK')
