"""Generate application icon and Windows version metadata from APP_VERSION."""
import sys
import struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtCore import Qt, QBuffer, QByteArray, QIODevice
from media_categorizer.constants import APP_VERSION
root = Path(__file__).resolve().parents[1]
app = QApplication.instance() or QApplication([])
source = QImage(str(root / 'media_categorizer/assets/app-source.png'))
assert not source.isNull(), 'Application icon source is missing or invalid'
sizes = (16, 24, 32, 48, 64, 128, 256)
frames = []
for size in sizes:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    scaled = source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    painter = QPainter(image)
    painter.drawImage((size-scaled.width())//2, (size-scaled.height())//2, scaled)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, 'PNG')
    frames.append(bytes(data))
offset = 6 + 16*len(frames)
directory = bytearray(struct.pack('<HHH', 0, 1, len(frames)))
for size, frame in zip(sizes, frames):
    directory.extend(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(frame), offset))
    offset += len(frame)
(root / 'media_categorizer/assets/app.ico').write_bytes(directory + b''.join(frames))
version = tuple(map(int, APP_VERSION.split('.'))) + (0,)
(root / 'installer/version.txt').write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version}, prodvers={version}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'Media Categorizer'),
    StringStruct('FileVersion', '{APP_VERSION}'),
    StringStruct('ProductName', 'Media Categorizer'),
    StringStruct('ProductVersion', '{APP_VERSION}'),
    StringStruct('OriginalFilename', 'MediaCategorizer.exe')
  ])]), VarFileInfo([VarStruct('Translation', [1033,1200])])]
)
""", encoding='utf-8')
print(APP_VERSION)
