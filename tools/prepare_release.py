"""Generate application icon and Windows version metadata from APP_VERSION."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtCore import Qt, QRectF
from media_categorizer.constants import APP_VERSION
root = Path(__file__).resolve().parents[1]
app = QApplication.instance() or QApplication([])
image = QImage(256, 256, QImage.Format_ARGB32)
image.fill(Qt.transparent)
p = QPainter(image)
p.setRenderHint(QPainter.Antialiasing)
p.setPen(Qt.NoPen)
p.setBrush(QColor('#315ed1'))
p.drawRoundedRect(QRectF(8,8,240,240),52,52)
p.setPen(QPen(QColor('white'), 12, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
p.setBrush(Qt.NoBrush)
p.drawRoundedRect(QRectF(56,55,144,146),15,15)
p.drawLine(60,163,102,120)
p.drawLine(102,120,144,163)
p.drawLine(144,163,169,139)
p.drawLine(169,139,195,164)
p.drawEllipse(QRectF(146,82,19,19))
p.end()
assert image.save(str(root / 'media_categorizer/assets/app.ico'))
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
