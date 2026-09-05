"""Render photo/video QA images using generated media and isolated settings."""
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QWheelEvent, QMouseEvent
from PySide6.QtWidgets import QApplication
from media_categorizer_v4_5 import MediaCategorizer


def sample_image(width=800, height=600):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#173047"))
    painter = QPainter(image)
    for y in range(0, height, 100):
        for x in range(0, width, 100):
            painter.fillRect(x + 2, y + 2, 96, 96, QColor.fromHsv((x + y) % 360, 130, 190))
            painter.setPen(Qt.white)
            painter.setFont(QFont("Arial", 14))
            painter.drawText(x + 10, y + 50, f"{x},{y}")
    painter.end()
    return image


def make_test_video(path, image):
    """Small uncompressed AVI fixture, no external encoder required."""
    image = image.convertToFormat(QImage.Format_BGR888).flipped(Qt.Vertical)
    width, height = image.width(), image.height()
    frame = bytes(image.constBits())
    size, count, fps = len(frame), 20, 10
    def chunk(name, data):
        return name + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
    avih = struct.pack("<14I", 100000, size * fps, 0, 16, count, 0, 1, size, width, height, 0, 0, 0, 0)
    strh = struct.pack("<4s4sIHHIIIIIIIIhhhh", b"vids", b"DIB ", 0, 0, 0, 0, 1, fps, 0,
                       count, size, 0xFFFFFFFF, 0, 0, 0, width, height)
    strf = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, size, 0, 0, 0, 0)
    header = chunk(b"LIST", b"hdrl" + chunk(b"avih", avih) +
                   chunk(b"LIST", b"strl" + chunk(b"strh", strh) + chunk(b"strf", strf)))
    frames, index = b"", b""
    for _ in range(count):
        index += struct.pack("<4sIII", b"00db", 16, 4 + len(frames), size)
        frames += chunk(b"00db", frame)
    body = b"AVI " + header + chunk(b"LIST", b"movi" + frames) + chunk(b"idx1", index)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def main():
    output = Path(__file__).resolve().parents[1] / "build" / "qa"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        os.environ["APPDATA"] = str(directory / "config")
        media = directory / "media"
        media.mkdir()
        app = QApplication.instance() or QApplication([])
        image = sample_image()
        image.save(str(media / "a_photo.png"))
        make_test_video(media / "b_video.avi", sample_image(640, 400))
        window = MediaCategorizer()
        window.setAttribute(Qt.WA_ShowWithoutActivating)
        window.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        window.show()
        app.processEvents()
        window.showNormal()
        window.resize(1500, 950)
        def settle(milliseconds=100):
            deadline = time.monotonic() + milliseconds / 1000
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
        window.load_folder(media)
        settle()
        window.grab().save(str(output / "photo-fit.png"))
        window.toggle_theme()
        settle()
        window.grab().save(str(output / "photo-light.png"))
        window.resize(1100, 720)
        window.multi_btn.setChecked(True)
        settle()
        window.grab().save(str(output / "compact-light.png"))
        window.multi_btn.setChecked(False)
        window.resize(1500, 950)
        window.toggle_theme()
        settle()
        window.set_zoom_percent(230)
        canvas = window.image_label
        canvas.view.pan(100, -80, *canvas._area_and_source())
        canvas.update()
        settle()
        window.grab().save(str(output / "photo-pan.png"))
        window.next_file()
        settle(1500)
        player = window._active_player()
        player.pause()
        frame = window.video_canvas.videoSink().videoFrame()
        native_video = next((child for child in app.allWindows()
                             if child.metaObject().className() == "QVideoWindow"), None)
        if native_video is not None:
            point = QPoint(native_video.width() // 2, native_video.height() // 2)
            wheel = QWheelEvent(QPointF(point), QPointF(native_video.mapToGlobal(point)), QPoint(),
                               QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
            before_zoom = window.zoom_slider.value()
            app.sendEvent(native_video, wheel)
            assert window.zoom_slider.value() == before_zoom + 10, "Native wheel was not delivered"
            start = window.video_host.mapToGlobal(QPoint(window.video_host.width() // 2,
                                                       window.video_host.height() // 2))
            before_pan = window.video_canvas.view.offset_x
            for kind, position, button, buttons in (
                (QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
                (QEvent.MouseMove, start + QPoint(20, 0), Qt.NoButton, Qt.LeftButton),
                (QEvent.MouseButtonRelease, start + QPoint(20, 0), Qt.LeftButton, Qt.NoButton),
            ):
                event = QMouseEvent(kind, QPointF(native_video.mapFromGlobal(position)), QPointF(position),
                                    button, buttons, Qt.NoModifier)
                app.sendEvent(native_video, event)
            assert abs(window.video_canvas.view.offset_x - before_pan - 20) < 0.1, "Native drag failed"
        assert frame.isValid(), "No decoded video frame"
        assert player.duration() > 0, "Video duration unavailable"
        window.reset_zoom()
        settle()
        window.grab().save(str(output / "video-fit.png"))
        window.set_zoom_percent(230)
        canvas = window.video_canvas
        canvas.view.pan(-110, 65, *canvas._area_and_source())
        canvas.fit_to_parent()
        settle()
        window.grab().save(str(output / "video-pan.png"))
        if native_video is not None:
            origin = window.mapToGlobal(QPoint(0, 0))
            capture = window.screen().grabWindow(0, origin.x(), origin.y(), window.width(), window.height())
            capture.save(str(output / "video-native.png"))
            if os.environ.get("QT_QPA_PLATFORM") == "windows":
                # QWidget.grab excludes native video, so toolbar pixels must
                # match the real screen even while the video is enlarged.
                actual, reference = capture.toImage(), window.grab().toImage()
                for panel in (window.top_widget, window.scroll):
                    point = panel.mapTo(window, QPoint(panel.width() // 2, 2))
                    scale = capture.devicePixelRatio()
                    x, y = round(point.x() * scale), round(point.y() * scale)
                    assert actual.pixelColor(x, y) == reference.pixelColor(x, y), "Video overlaps controls"
        frame.toImage().save(str(output / "video-decoded.png"))
        report = {"video_frame": [frame.width(), frame.height()], "video_duration_ms": player.duration(),
                  "native_wheel_and_drag": native_video is not None,
                  "photo_viewport": [window.image_label.width(), window.image_label.height()],
                  "video_viewport": [window.video_host.width(), window.video_host.height()]}
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        window.close()
        settle()


if __name__ == "__main__":
    main()
