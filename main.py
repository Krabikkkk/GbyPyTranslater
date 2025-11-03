# main.py
import sys
import math
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QTextEdit,
    QSizePolicy, QSpacerItem, QHBoxLayout, QSlider, QFrame
)
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPalette, QPainter, QBrush, QPen, QFont

# ---------------------------
# Парсер (встроенный в main.py) — читает только gcode.txt
# ---------------------------
def load_gcode_lines(filename="gcode.txt"):
    base = Path(__file__).resolve().parent
    path = base / filename
    if not path.exists():
        raise FileNotFoundError(path)
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return lines

def parse_and_build_path(lines):
    """
    Парсит G-код и возвращает список сегментов:
    {'type':'move'|'pause', 'points':[(x,y),...], 'pause':secs, 'laser':bool}
    Все координаты конвертированы в мм (если в файле были дюймы).
    G90/G91/G92 учтены. M03/M05 учитывают состояние лазера.
    """
    segments = []
    absolute = True  # G90
    units = "mm"     # default
    cur_x, cur_y = 0.0, 0.0
    laser_on = False

    def parse_val(tok):
        try:
            return float(tok[1:])
        except:
            return None

    for line in lines:
        parts = [p.upper() for p in line.split() if p]
        if not parts:
            continue
        cmd = parts[0]

        # helper getters that consider units
        def get(letter):
            for p in parts[1:]:
                if p.startswith(letter):
                    val = parse_val(p)
                    if val is None:
                        return None
                    # convert inches to mm if needed
                    return val * 25.4 if units == "inches" else val
            return None

        if cmd == "G20":
            units = "inches"
            continue
        if cmd == "G21":
            units = "mm"
            continue
        if cmd == "G90":
            absolute = True
            continue
        if cmd == "G91":
            absolute = False
            continue
        if cmd == "G92":
            gx = get("X"); gy = get("Y")
            if gx is not None:
                cur_x = gx
            if gy is not None:
                cur_y = gy
            continue
        if cmd == "G28":
            # go home
            seg = {'type':'move', 'points':[(cur_x, cur_y), (0.0, 0.0)], 'pause':0.0, 'laser': laser_on}
            segments.append(seg)
            cur_x, cur_y = 0.0, 0.0
            continue
        if cmd == "M03":
            # laser on
            laser_on = True
            continue
        if cmd == "M05":
            laser_on = False
            continue
        if cmd == "G04":
            p = get("P") or 0.0
            seg = {'type':'pause', 'points':[], 'pause': float(p)/1000.0, 'laser': laser_on}
            segments.append(seg)
            continue

        # linear / rapid moves
        if cmd in ("G00", "G01"):
            tx = get("X"); ty = get("Y")
            if tx is None: tx = cur_x
            if ty is None: ty = cur_y
            if not absolute:
                tx = cur_x + tx
                ty = cur_y + ty
            # steps proportional to distance (1 mm step)
            dist = math.hypot(tx - cur_x, ty - cur_y)
            steps = max(1, int(dist / 1.0))
            pts = []
            for i in range(1, steps + 1):
                t = i / steps
                x = cur_x + (tx - cur_x) * t
                y = cur_y + (ty - cur_y) * t
                pts.append((x, y))
            seg = {'type':'move', 'points':[(cur_x, cur_y)] + pts, 'pause':0.0, 'laser': laser_on}
            segments.append(seg)
            cur_x, cur_y = tx, ty
            continue

        # arcs G02 / G03 with I/J offsets
        if cmd in ("G02", "G03"):
            tx = get("X"); ty = get("Y")
            ioff = get("I") or 0.0
            joff = get("J") or 0.0
            if tx is None: tx = cur_x
            if ty is None: ty = cur_y
            if not absolute:
                tx = cur_x + tx
                ty = cur_y + ty
            cx = cur_x + ioff
            cy = cur_y + joff
            r = math.hypot(cur_x - cx, cur_y - cy)
            ang1 = math.atan2(cur_y - cy, cur_x - cx)
            ang2 = math.atan2(ty - cy, tx - cx)
            cw = (cmd == "G02")
            if cw:
                if ang2 >= ang1:
                    ang2 -= 2 * math.pi
                total_ang = ang1 - ang2
            else:
                if ang2 <= ang1:
                    ang2 += 2 * math.pi
                total_ang = ang2 - ang1
            segments_count = max(8, int(abs(total_ang) / (2 * math.pi) * 64))
            pts = []
            for k in range(1, segments_count + 1):
                frac = k / segments_count
                ang = ang1 - frac * total_ang if cw else ang1 + frac * total_ang
                x = cx + r * math.cos(ang)
                y = cy + r * math.sin(ang)
                pts.append((x, y))
            seg = {'type':'move', 'points':[(cur_x, cur_y)] + pts, 'pause':0.0, 'laser': laser_on}
            segments.append(seg)
            cur_x, cur_y = tx, ty
            continue

        # unknown -> ignore
    return segments

# ---------------------------
# Виджет рисования и анимация (масштаб + zoom + инверсия)
# ---------------------------
class DrawingWidget(QWidget):
    def __init__(self, segments, parent=None):
        super().__init__(parent)
        self.segments = segments
        self.margin = 20
        self.dot_px = 8
        # path_points: list of tuples (x, y, draw_flag)
        self.path_points = self.flatten_points(segments)
        self.index = 0
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self.step)
        self.running = False

        # transform/state
        self.user_zoom = 1.0
        self.invert_x = False
        self.invert_y = False

        # bounding box
        self.min_x = 0.0; self.max_x = 1.0; self.min_y = 0.0; self.max_y = 1.0
        if self.path_points:
            xs = [p[0] for p in self.path_points]
            ys = [p[1] for p in self.path_points]
            self.min_x, self.max_x = min(xs), max(xs)
            self.min_y, self.max_y = min(ys), max(ys)
            if abs(self.max_x - self.min_x) < 1e-6:
                self.max_x += 1.0
            if abs(self.max_y - self.min_y) < 1e-6:
                self.max_y += 1.0

        self.scale = 1.0
        self.left_pad = 0.0
        self.top_pad = 0.0

    def set_user_zoom(self, zoom_factor: float):
        self.user_zoom = max(0.01, float(zoom_factor))
        self.update()

    def toggle_invert_x(self):
        self.invert_x = not self.invert_x
        self.update()

    def toggle_invert_y(self):
        self.invert_y = not self.invert_y
        self.update()

    def flatten_points(self, segments):
        pts = []
        for seg in segments:
            if seg['type'] == 'move':
                draw_flag = bool(seg.get('laser', False))
                for p in seg['points']:
                    pts.append((p[0], p[1], draw_flag))
            elif seg['type'] == 'pause':
                if pts:
                    last = pts[-1]
                    pts.append((last[0], last[1], last[2]))
        if not pts:
            return []
        out = [pts[0]]
        for p in pts[1:]:
            if abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9 or p[2] != out[-1][2]:
                out.append(p)
        return out

    def start(self):
        if not self.path_points:
            return
        self.index = 0
        self.running = True
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.running = False

    def step(self):
        if self.index < len(self.path_points) - 1:
            self.index += 1
            self.update()
        else:
            self.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        inner = rect.adjusted(self.margin, self.margin, -self.margin, -self.margin)

        # white working area
        painter.setBrush(QBrush(Qt.white))
        painter.setPen(QPen(QColor("#bdbdbd"), 2))
        painter.drawRect(inner)

        if not self.path_points:
            return

        # recompute transform parameters
        self.compute_transform(inner)

        # Draw only the path that has been traversed up to current index.
        pen_on = QPen(QColor("#e33"), 2)
        painter.setPen(pen_on)

        prev_s = None
        prev_draw = None
        # iterate through points up to current index and draw when both endpoints have draw_flag True
        for i in range(0, self.index + 1):
            x, y, draw_flag = self.path_points[i]
            sx, sy = self.map_to_canvas((x, y), inner)
            if i > 0:
                px, py, pdraw = self.path_points[i - 1]
                psx, psy = self.map_to_canvas((px, py), inner)
                # draw line only if both previous and current have draw_flag True
                if pdraw and draw_flag:
                    painter.setPen(pen_on)
                    painter.drawLine(psx, psy, sx, sy)
            prev_s = (sx, sy)
            prev_draw = draw_flag

        # draw red/gray dot at current index
        cx, cy, draw_flag = self.path_points[self.index]
        sx, sy = self.map_to_canvas((cx, cy), inner)
        if draw_flag:
            brush = QBrush(QColor(200, 30, 30))
            pen = QPen(QColor(150, 20, 20))
        else:
            brush = QBrush(QColor(100, 100, 100))
            pen = QPen(QColor(70, 70, 70))
        painter.setBrush(brush)
        painter.setPen(pen)
        r = self.dot_px
        painter.drawEllipse(QRectF(sx - r, sy - r, r * 2, r * 2))

    def compute_transform(self, inner_rect):
        inner_w = inner_rect.width()
        inner_h = inner_rect.height()
        data_w = (self.max_x - self.min_x)
        data_h = (self.max_y - self.min_y)
        if data_w <= 0: data_w = 1.0
        if data_h <= 0: data_h = 1.0
        scale_auto = min(inner_w / data_w, inner_h / data_h) * 0.95
        self.scale = scale_auto * self.user_zoom
        drawing_w = data_w * self.scale
        drawing_h = data_h * self.scale
        self.left_pad = max(0.0, (inner_w - drawing_w) / 2.0)
        self.top_pad = max(0.0, (inner_h - drawing_h) / 2.0)

    def map_to_canvas(self, point, inner_rect):
        px, py = point
        inner_x = inner_rect.x()
        inner_y = inner_rect.y()
        inner_w = inner_rect.width()
        inner_h = inner_rect.height()
        data_w = (self.max_x - self.min_x)
        data_h = (self.max_y - self.min_y)
        x_rel = (px - self.min_x) / data_w if data_w != 0 else 0.0
        y_rel = (py - self.min_y) / data_h if data_h != 0 else 0.0
        drawing_w = data_w * self.scale
        drawing_h = data_h * self.scale
        base_x = inner_x + self.left_pad
        base_y = inner_y + self.top_pad
        if not self.invert_x:
            sx = base_x + x_rel * drawing_w
        else:
            sx = base_x + (1.0 - x_rel) * drawing_w
        if not self.invert_y:
            sy = base_y + y_rel * drawing_h
        else:
            sy = base_y + (1.0 - y_rel) * drawing_h
        return sx, sy

# ---------------------------
# UI: main menu and visualization window (с элементами управления)
# ---------------------------
class VisualizationWindow(QWidget):
    def __init__(self, segments, raw_text):
        super().__init__()
        self.segments = segments
        self.raw_text = raw_text
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Laser App - Визуализация")
        self.setGeometry(180, 120, 1100, 700)
        p = QPalette()
        p.setColor(QPalette.Window, QColor(200, 200, 200))
        self.setAutoFillBackground(True)
        self.setPalette(p)

        title = QLabel("Процесс визуализации", self)
        title.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        title.setStyleSheet("font-size:28px; font-weight:bold; color:#222;")
        title.setFixedHeight(60)

        # Drawing widget
        self.drawing = DrawingWidget(self.segments)
        drawing_frame = QFrame()
        drawing_frame.setLayout(QVBoxLayout())
        drawing_frame.layout().addWidget(self.drawing)

        # Controls: zoom slider + invert buttons
        controls_layout = QVBoxLayout()
        zoom_label = QLabel("Zoom: 100%")
        zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label = zoom_label

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(10)
        slider.setMaximum(400)
        slider.setValue(100)
        slider.setTickInterval(10)
        slider.valueChanged.connect(self.on_zoom_change)
        self.zoom_slider = slider

        invert_x_btn = QPushButton("Invert X")
        invert_y_btn = QPushButton("Invert Y")
        invert_x_btn.setCheckable(True)
        invert_y_btn.setCheckable(True)
        invert_x_btn.clicked.connect(lambda _: (self.drawing.toggle_invert_x(), self.drawing.update()))
        invert_y_btn.clicked.connect(lambda _: (self.drawing.toggle_invert_y(), self.drawing.update()))

        # Raw text box
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.raw_text)
        text.setStyleSheet("background-color: white; color: black;")
        text.setFixedWidth(360)

        controls_layout.addWidget(zoom_label)
        controls_layout.addWidget(slider)
        controls_layout.addWidget(invert_x_btn)
        controls_layout.addWidget(invert_y_btn)
        controls_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))
        controls_layout.addWidget(QLabel("G-code (raw):"))
        controls_layout.addWidget(text)

        # layout: drawing left, controls right
        hbox = QHBoxLayout()
        hbox.addWidget(drawing_frame, stretch=1)
        hbox.addLayout(controls_layout)

        main_layout = QVBoxLayout()
        main_layout.addWidget(title)
        main_layout.addLayout(hbox)
        self.setLayout(main_layout)

    def on_zoom_change(self, value):
        pct = value
        self.zoom_label.setText(f"Zoom: {pct}%")
        self.drawing.set_user_zoom(pct / 100.0)

    def showEvent(self, event):
        super().showEvent(event)
        self.drawing.start()

class MainMenu(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Laser App - Главное меню")
        self.setGeometry(240, 160, 600, 420)
        self.play_button = QPushButton("Играть")
        self.exit_button = QPushButton("Выход")
        self.play_button.clicked.connect(self.on_play)
        self.exit_button.clicked.connect(self.close)
        style = """
            QPushButton { background-color: #444; color:white; font-size:18px; padding:12px; border-radius:8px; min-width:160px;}
            QPushButton:hover { background-color:#666; }
        """
        self.play_button.setStyleSheet(style)
        self.exit_button.setStyleSheet(style)
        layout = QVBoxLayout()
        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))
        layout.addWidget(self.play_button, alignment=Qt.AlignHCenter)
        layout.addWidget(self.exit_button, alignment=Qt.AlignHCenter)
        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))
        self.setLayout(layout)

    def on_play(self):
        try:
            lines = load_gcode_lines("gcode.txt")
            segments = parse_and_build_path(lines)
            raw = "\n".join(lines)
        except Exception as e:
            raw = f"Ошибка чтения gcode.txt: {e}"
            segments = []
        self.close()
        self.vw = VisualizationWindow(segments, raw)
        self.vw.show()

# ---------------------------
# Запуск
# ---------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    mm = MainMenu()
    mm.show()
    sys.exit(app.exec())
