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
# Чтение и парсер G-code (встроенный)
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
    Возвращает список сегментов:
      {'type':'move'|'pause', 'points':[(x,y),...], 'pause':secs, 'laser':bool, 'feedrate': mm/min, 'rapid':bool}
    - Конвертация единиц (G20->inches -> mm)
    - G90/G91/G92 учтены
    - M03/M05 устанавливают laser_on
    - Вся логика парсинга внутри main.py (нет внешних зависимостей)
    """
    segments = []
    absolute = True
    units = "mm"
    cur_x, cur_y = 0.0, 0.0
    laser_on = False
    current_feed = None  # modal feed (mm/min)
    default_cut_feed = 1000.0  # mm/min if none specified for cutting moves
    default_rapid_feed = 3000.0  # mm/min for rapid moves if not specified

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

        def get(letter):
            for p in parts[1:]:
                if p.startswith(letter):
                    val = parse_val(p)
                    if val is None:
                        return None
                    return val * 25.4 if units == "inches" else val
            return None

        # Units
        if cmd == "G20":
            units = "inches"
            continue
        if cmd == "G21":
            units = "mm"
            continue
        # Positioning modes
        if cmd == "G90":
            absolute = True
            continue
        if cmd == "G91":
            absolute = False
            continue
        # Set position
        if cmd == "G92":
            gx = get("X"); gy = get("Y")
            if gx is not None:
                cur_x = gx
            if gy is not None:
                cur_y = gy
            continue
        # Home
        if cmd == "G28":
            seg = {'type':'move', 'points':[(cur_x, cur_y), (0.0, 0.0)], 'pause':0.0, 'laser': laser_on,
                   'feedrate': current_feed, 'rapid': True}
            segments.append(seg)
            cur_x, cur_y = 0.0, 0.0
            continue
        # Laser state
        if cmd == "M03":
            # optional S parameter can set power but not needed for timeline; set laser on
            laser_on = True
            # M03 does not change feedrate
            continue
        if cmd == "M05":
            laser_on = False
            continue
        # Pause
        if cmd == "G04":
            p = get("P") or 0.0
            seg = {'type':'pause', 'points':[], 'pause': float(p)/1000.0, 'laser': laser_on}
            segments.append(seg)
            continue

        # Get feed if present (modal)
        fval = get("F")
        if fval is not None:
            # feed is given in current units (converted in get); it's modal for next moves
            current_feed = fval

        # Moves
        if cmd in ("G00", "G01"):
            tx = get("X"); ty = get("Y")
            if tx is None: tx = cur_x
            if ty is None: ty = cur_y
            if not absolute:
                tx = cur_x + tx
                ty = cur_y + ty
            # choose feed: for G00 prefer explicit F or default_rapid; for G01 prefer current_feed or default_cut_feed
            if cmd == "G00":
                chosen_feed = current_feed if current_feed is not None else default_rapid_feed
                rapid_flag = True
            else:
                chosen_feed = current_feed if current_feed is not None else default_cut_feed
                rapid_flag = False
            # generate interpolation points (1 mm step approx)
            dist = math.hypot(tx - cur_x, ty - cur_y)
            steps = max(1, int(dist / 1.0))
            pts = []
            for i in range(1, steps+1):
                t = i / steps
                x = cur_x + (tx - cur_x) * t
                y = cur_y + (ty - cur_y) * t
                pts.append((x, y))
            seg = {'type':'move', 'points':[(cur_x, cur_y)] + pts, 'pause':0.0, 'laser': laser_on,
                   'feedrate': chosen_feed, 'rapid': rapid_flag}
            segments.append(seg)
            cur_x, cur_y = tx, ty
            continue

        # Arcs G02/G03
        if cmd in ("G02", "G03"):
            tx = get("X"); ty = get("Y")
            ioff = get("I") or 0.0
            joff = get("J") or 0.0
            if tx is None: tx = cur_x
            if ty is None: ty = cur_y
            if not absolute:
                tx = cur_x + tx
                ty = cur_y + ty
            # default feed for arc uses current_feed or default_cut_feed
            chosen_feed = current_feed if current_feed is not None else default_cut_feed
            cx = cur_x + ioff
            cy = cur_y + joff
            r = math.hypot(cur_x - cx, cur_y - cy)
            ang1 = math.atan2(cur_y - cy, cur_x - cx)
            ang2 = math.atan2(ty - cy, tx - cx)
            cw = (cmd == "G02")
            if cw:
                if ang2 >= ang1:
                    ang2 -= 2*math.pi
                total_ang = ang1 - ang2
            else:
                if ang2 <= ang1:
                    ang2 += 2*math.pi
                total_ang = ang2 - ang1
            segments_count = max(8, int(abs(total_ang) / (2*math.pi) * 64))
            pts = []
            for k in range(1, segments_count+1):
                frac = k/segments_count
                ang = ang1 - frac*total_ang if cw else ang1 + frac*total_ang
                x = cx + r * math.cos(ang)
                y = cy + r * math.sin(ang)
                pts.append((x, y))
            seg = {'type':'move', 'points':[(cur_x, cur_y)] + pts, 'pause':0.0, 'laser': laser_on,
                   'feedrate': chosen_feed, 'rapid': False}
            segments.append(seg)
            cur_x, cur_y = tx, ty
            continue

        # unknown commands are ignored
    return segments

# ---------------------------
# DrawingWidget: строит timeline с учётом F и G04, анимация по времени
# ---------------------------
class DrawingWidget(QWidget):
    def __init__(self, segments, parent=None):
        super().__init__(parent)
        self.segments = segments
        self.margin = 20
        self.dot_px = 8

        # timeline: список точек {'x', 'y', 'draw'(bool), 'dt' seconds to next}
        self.timeline = self.build_timeline(segments)
        self.index = 0

        # timer single-shot (we schedule variable intervals)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)
        self.running = False

        # transform/state
        self.user_zoom = 1.0
        self.invert_x = False
        self.invert_y = False

        # bounding box (from timeline)
        self.min_x = 0.0; self.max_x = 1.0; self.min_y = 0.0; self.max_y = 1.0
        if self.timeline:
            xs = [p['x'] for p in self.timeline]
            ys = [p['y'] for p in self.timeline]
            self.min_x, self.max_x = min(xs), max(xs)
            self.min_y, self.max_y = min(ys), max(ys)
            if abs(self.max_x - self.min_x) < 1e-6:
                self.max_x += 1.0
            if abs(self.max_y - self.min_y) < 1e-6:
                self.max_y += 1.0

        self.scale = 1.0
        self.left_pad = 0.0
        self.top_pad = 0.0

    def build_timeline(self, segments):
        timeline = []
        # minimum dt to avoid zero intervals (ms)
        MIN_DT = 0.01  # seconds
        for seg in segments:
            if seg['type'] == 'move':
                pts = seg['points']
                feed = seg.get('feedrate')  # mm/min
                rapid = seg.get('rapid', False)
                # choose feed: if None -> defaults (already set in parser), but keep safe
                if feed is None:
                    feed = 3000.0 if rapid else 1000.0
                speed_mm_s = max(0.001, feed / 60.0)
                for i in range(len(pts)):
                    x, y = pts[i]
                    if i < len(pts) - 1:
                        nx, ny = pts[i+1]
                        dist = math.hypot(nx - x, ny - y)
                        dt = max(MIN_DT, dist / speed_mm_s)
                    else:
                        dt = MIN_DT  # small pause to allow drawing last step
                    timeline.append({'x': x, 'y': y, 'draw': bool(seg.get('laser', False)), 'dt': dt})
            elif seg['type'] == 'pause':
                # append hold: duplicate last point with dt = pause
                if timeline:
                    last = timeline[-1]
                    timeline.append({'x': last['x'], 'y': last['y'], 'draw': last['draw'], 'dt': max(MIN_DT, seg.get('pause', 0.0))})
                else:
                    # no previous point — skip
                    pass
        # ensure final point exists (with dt=0)
        if timeline:
            timeline[-1]['dt'] = 0.0
        return timeline

    def start(self):
        if not self.timeline:
            return
        self.index = 0
        self.running = True
        self.update()
        # schedule first step using dt of index 0
        first_dt = max(0.01, self.timeline[0]['dt'])
        self.timer.start(int(first_dt * 1000))

    def stop(self):
        self.timer.stop()
        self.running = False

    def reset(self):
        # stop and reset index to 0; do not start
        self.stop()
        self.index = 0
        self.update()

    def step(self):
        # advance index and schedule next
        if self.index < len(self.timeline) - 1:
            self.index += 1
            self.update()
            next_dt = max(0.01, self.timeline[self.index]['dt'])
            # schedule next
            if self.index < len(self.timeline) - 1:
                self.timer.start(int(next_dt * 1000))
            else:
                # last element: stop after small pause
                self.timer.start(int(next_dt * 1000))
        else:
            self.stop()

    def set_user_zoom(self, zoom_factor: float):
        self.user_zoom = max(0.01, float(zoom_factor))
        self.update()

    def toggle_invert_x(self):
        self.invert_x = not self.invert_x
        self.update()

    def toggle_invert_y(self):
        self.invert_y = not self.invert_y
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        inner = rect.adjusted(self.margin, self.margin, -self.margin, -self.margin)

        painter.setBrush(QBrush(Qt.white))
        painter.setPen(QPen(QColor("#bdbdbd"), 2))
        painter.drawRect(inner)

        if not self.timeline:
            return

        self.compute_transform(inner)

        pen_on = QPen(QColor("#e33"), 2)
        painter.setPen(pen_on)

        # draw only traversed path up to current index
        for i in range(1, self.index + 1):
            a = self.timeline[i-1]
            b = self.timeline[i]
            if a['draw'] and b['draw']:
                ax, ay = self.map_to_canvas((a['x'], a['y']), inner)
                bx, by = self.map_to_canvas((b['x'], b['y']), inner)
                painter.drawLine(ax, ay, bx, by)

        # draw current point
        cur = self.timeline[self.index]
        sx, sy = self.map_to_canvas((cur['x'], cur['y']), inner)
        if cur['draw']:
            brush = QBrush(QColor(200, 30, 30))
            pen = QPen(QColor(150, 20, 20))
        else:
            brush = QBrush(QColor(100, 100, 100))
            pen = QPen(QColor(70, 70, 70))
        painter.setBrush(brush)
        painter.setPen(pen)
        r = self.dot_px
        painter.drawEllipse(QRectF(sx - r, sy - r, r*2, r*2))

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
# UI: visualization + controls (Restart, Zoom, Invert X/Y)
# ---------------------------
class VisualizationWindow(QWidget):
    def __init__(self, segments, raw_text):
        super().__init__()
        self.segments = segments
        self.raw_text = raw_text
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Laser App - Визуализация")
        self.setGeometry(180, 120, 1200, 720)
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

        # Controls
        controls_layout = QVBoxLayout()
        zoom_label = QLabel("Zoom: 100%")
        zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label = zoom_label

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(10); slider.setMaximum(400); slider.setValue(100)
        slider.setTickInterval(10)
        slider.valueChanged.connect(self.on_zoom_change)
        self.zoom_slider = slider

        invert_x_btn = QPushButton("Invert X")
        invert_y_btn = QPushButton("Invert Y")
        invert_x_btn.setCheckable(True); invert_y_btn.setCheckable(True)
        invert_x_btn.clicked.connect(lambda _: (self.drawing.toggle_invert_x(), self.drawing.update()))
        invert_y_btn.clicked.connect(lambda _: (self.drawing.toggle_invert_y(), self.drawing.update()))

        restart_btn = QPushButton("Restart")
        restart_btn.clicked.connect(self.on_restart)

        controls_layout.addWidget(zoom_label)
        controls_layout.addWidget(slider)
        controls_layout.addWidget(invert_x_btn)
        controls_layout.addWidget(invert_y_btn)
        controls_layout.addWidget(restart_btn)
        controls_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))
        controls_layout.addWidget(QLabel("G-code (raw):"))
        text = QTextEdit()
        text.setReadOnly(True); text.setPlainText(self.raw_text)
        text.setStyleSheet("background-color: white; color: black;"); text.setFixedWidth(360)
        controls_layout.addWidget(text)

        hbox = QHBoxLayout()
        hbox.addWidget(drawing_frame, stretch=1)
        hbox.addLayout(controls_layout)

        main_layout = QVBoxLayout()
        main_layout.addWidget(title)
        main_layout.addLayout(hbox)
        self.setLayout(main_layout)

    def on_zoom_change(self, value):
        self.zoom_label.setText(f"Zoom: {value}%")
        self.drawing.set_user_zoom(value / 100.0)

    def on_restart(self):
        # reset drawing widget and restart
        self.drawing.reset()
        self.drawing.start()

    def showEvent(self, event):
        super().showEvent(event)
        # start animation once shown
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
