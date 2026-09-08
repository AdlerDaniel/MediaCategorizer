"""Interactive crop dialog using the same edge handles as the photo editor."""
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QImage, QTransform
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QComboBox,QLabel
from .photo_editor import CropCanvas
from .editor_widgets import RatioCards
from .ui import IconButton

class VideoCropDialog(QDialog):
    def __init__(self, image, crop=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle('Кадрирование видео')
        self.resize(850,700)
        self.canvas=CropCanvas(image)
        self.ratios=QComboBox()
        for label,value in [('Исходный',image.width()/image.height()),('Свободно',None),('16:9',16/9),('9:16',9/16),('1:1',1),('4:5',.8)]:
            self.ratios.addItem(label,value)
        self.ratios.hide()
        self.ratios.currentIndexChanged.connect(self.choose_ratio)
        layout=QVBoxLayout(self)
        layout.addWidget(RatioCards(self.ratios))
        layout.addWidget(self.canvas,1)
        layout.addWidget(QLabel('Перетаскивайте стороны и углы рамки. Внутри рамки — перемещение.'))
        row=QHBoxLayout()
        cancel,apply=IconButton('x','Отмена'),IconButton('check','Применить')
        apply.setProperty('primary',True)
        cancel.clicked.connect(self.reject)
        apply.clicked.connect(self.accept)
        row.addStretch();row.addWidget(cancel);row.addWidget(apply)
        layout.addLayout(row)
        if crop:
            x,y,w,h=crop
            self.ratios.setCurrentIndex(1)
            self.canvas.selection=QRect(round(x*image.width()),round(y*image.height()),round(w*image.width()),round(h*image.height()))
        else:
            self.choose_ratio()

    def choose_ratio(self):
        if self.ratios.currentIndex()==0:
            self.canvas.ratio=self.ratios.currentData()
            self.canvas.selection=self.canvas.image.rect()
            self.canvas.update()
        else:
            self.canvas.set_ratio(self.ratios.currentData())
            if self.canvas.selection.isEmpty(): self.canvas.selection=self.canvas.image.rect()

    def crop_rect(self):
        r=self.canvas.selection if not self.canvas.selection.isEmpty() else self.canvas.image.rect()
        w,h=self.canvas.image.width(),self.canvas.image.height()
        return r.x()/w,r.y()/h,r.width()/w,r.height()/h
