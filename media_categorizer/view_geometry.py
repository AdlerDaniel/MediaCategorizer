"""Pan/zoom geometry shared by photo and native video canvases."""
from dataclasses import dataclass


@dataclass
class PanZoom:
    percent: int = 100
    offset_x: float = 0.0
    offset_y: float = 0.0

    def reset_pan(self):
        self.offset_x = self.offset_y = 0.0

    def rectangle(self, area_width, area_height, source_width, source_height):
        aw, ah = max(1, area_width), max(1, area_height)
        sw, sh = max(1, source_width), max(1, source_height)
        fit = min(aw / sw, ah / sh)
        width, height = sw * fit * self.percent / 100, sh * fit * self.percent / 100
        limit_x, limit_y = max(0, (width - aw) / 2), max(0, (height - ah) / 2)
        self.offset_x = max(-limit_x, min(limit_x, self.offset_x))
        self.offset_y = max(-limit_y, min(limit_y, self.offset_y))
        return ((aw - width) / 2 + self.offset_x,
                (ah - height) / 2 + self.offset_y, width, height)

    def zoom(self, percent, area, source, anchor=None):
        percent = max(25, min(400, int(percent)))
        self.rectangle(*area, *source)
        if percent == self.percent:
            return
        ax, ay = anchor if anchor is not None else (area[0] / 2, area[1] / 2)
        ratio = percent / self.percent
        self.offset_x = (ax - area[0] / 2) * (1 - ratio) + self.offset_x * ratio
        self.offset_y = (ay - area[1] / 2) * (1 - ratio) + self.offset_y * ratio
        self.percent = percent
        self.rectangle(*area, *source)

    def pan(self, dx, dy, area, source):
        self.offset_x += dx
        self.offset_y += dy
        self.rectangle(*area, *source)
