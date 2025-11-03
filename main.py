# main.py
import sys
import math
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QTextEdit,
    QSizePolicy, QSpacerItem, QHBoxLayout
)
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPalette, QPainter, QBrush, QPen, QFont

# ---------------------------
# Парсер (упрощённый, для заданного набора команд)
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
    Парсит список строк G-кода и возвращает список сегментов,
    где каждый сегмент — dict {'type':'move','points':[(x,y),...], 'pause': seconds_optional}
    Coordinates are in file units (assumed mm unless G20 seen). This parser applies G90/G91/G92.
    """
    commands = []
    absolute = True  # G90
    units = "mm"     # G21 default
    cur_x, cur_y = 0.0, 0.0
    # G92: offsets for setting current position as given values => we'll set cur_x,cur_y
    for line in lines:
        # simple tokenization: uppercase letters followed by number (no spaces inside)
        toks = []
        i = 0
        s = line.strip()
        # split by spaces first
        for part in s.split():
            if len(part) >= 1:
                toks.append(part.upper())
        if not toks:
            continue
        cmd = toks[0]

        # Helper to get param value
        def get_val(letter):
            for p in toks[1:]:
                if p.startswith(letter):
                    try:
                        return float(p[1:])
                    except:
                        return None
            return None

        if cmd == "G20":
            units = "inches"
            # we don't convert coordinates here
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
            gx = get_val("X")
            gy = get_val("Y")
            if gx is not None:
                cur_x = gx
            if gy is not None:
                cur_y = gy
            continue
        if cmd == "G28":
            # go home (0,0)
            seg = {'type':'move','points':[(cur_x, cur_y), (0.0, 0.0)], 'pause':0.0}
            commands.append(seg)
            cur_x, cur_y = 0.0, 0.0
            continue
        if cmd == "M03":
            # laser on — no path effect, but keep as marker (could be used to draw laser on)
            # we ignore here for path generation
            continue
        if cmd == "M05":
            continue
        if cmd == "G04":
            p = get_val("P") or 0.0
            # treat as pause in milliseconds in file — convert to seconds
            seg = {'type':'pause','points':[], 'pause': float(p)/1000.0}
            commands.append(seg)
            continue

        # Motions
        if cmd in ("G00", "G01"):
            tx = get_val("X")
            ty = get_val("Y")
            if tx is None:
                tx = cur_x
            if ty is None:
                ty = cur_y
            if not absolute:
                tx = cur_x + tx
                ty = cur_y + ty
            # create linear interpolation points
            dist = math.hypot(tx - cur_x, ty - cur_y)
            # choose number of steps proportional to distance (1 mm per step)
            steps = max(1, int(dist / 1.0))
            pts = []
            for i in range(1, steps+1):
                t = i/steps
                x = cur_x + (tx - cur_x)*t
                y = cur_y + (ty - cur_y)*t
                pts.append((x,y))
            seg = {'type':'move','points':[(cur_x,cur_y)] + pts, 'pause':0.0}
            commands.append(seg)
            cur_x, cur_y = tx, ty
            continue

        if cmd in ("G02", "G03"):
            tx = get_val("X")
            ty = get_val("Y")
            ioff = get_val("I") or 0.0
            joff = get_val("J") or 0.0
            if tx is None:
                tx = cur_x
            if ty is None:
                ty = cur_y
            if not absolute:
                tx = cur_x + tx
                ty = cur_y + ty
            # center is offset from start
            cx = cur_x + ioff
            cy = cur_y + joff
            # radius
            r = math.hypot(cur_x - cx, cur_y - cy)
            # angles
            ang1 = math.atan2(cur_y - cy, cur_x - cx)
            ang2 = math.atan2(ty - cy, tx - cx)
            cw = (cmd == "G02")
            # normalize and compute total angle
            if cw:
                if ang2 >= ang1:
                    ang2 -= 2*math.pi
                total_ang = ang1 - ang2
            else:
                if ang2 <= ang1:
                    ang2 += 2*math.pi
                total_ang = ang2 - ang1
            # segments proportional to arc length
            segments = max(8, int(abs(total_ang)/(2*math.pi) * 64))
            pts = []
            for k in range(1, segments+1):
                frac = k/segments
                if cw:
                    ang = ang1 - frac*total_ang
                else:
                    ang = ang1 + frac*total_ang
                x = cx + r*math.cos(ang)
                y = cy + r*math.sin(ang)
                pts.append((x,y))
            seg = {'type':'move','points':[(cur_x,cur_y)] + pts, 'pause':0.0}
            commands.append(seg)
            cur_x, cur_y = tx, ty
            continue

        # unknown commands ignored
    return commands

# ---------------------------
# Виджет рисования и анимация
# ---------------------------
class DrawingWidget(QWidget):
    def __init__(self, segments, parent=None):
        super().__init__(parent)
        self.segments = segments  # list of segments as returned by parse_and_build_path
        self.margin = 20
        self.dot_px = 10  # radius in pixels
        self.path_points = self.flatten_points(segments)  # complete ordered list of points
        self.index = 0
        self.timer = QTimer(self)
        self.timer.setInterval(15)  # ms per step
        self.timer.timeout.connect(self.step)
        self.running = False

        # Compute bounding box for scaling
        self.min_x = 0.0; self.max_x = 1.0; self.min_y = 0.0; self.max_y = 1.0
        if self.path_points:
            xs = [p[0] for p in self.path_points]
            ys = [p[1] for p in self.path_points]
            self.min_x, self.max_x = min(xs), max(xs)
            self.min_y, self.max_y = min(ys), max(ys)
            # if flat area, add tiny padding
            if abs(self.max_x - self.min_x) < 1e-6:
                self.max_x += 1.0
            if abs(self.max_y - self.min_y) < 1e-6:
                self.max_y += 1.0

    def flatten_points(self, segments):
        pts = []
        for seg in segments:
            if seg['type'] == 'move':
                # append points in order
                for p in seg['points']:
                    pts.append(p)
            elif seg['type'] == 'pause':
                # pause doesn't add points, but we can duplicate last point to indicate hold
                if pts:
                    pts.append(pts[-1])
        # remove consecutive duplicates
        if not pts:
            return []
        out = [pts[0]]
        for p in pts[1:]:
            if abs(p[0]-out[-1][0])>1e-9 or abs(p[1]-out[-1][1])>1e-9:
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
        # Advance index; handle end
        if self.index < len(self.path_points)-1:
            self.index += 1
            # repaint
            self.update()
        else:
            self.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        # white working area with border
        inner = rect.adjusted(self.margin, self.margin, -self.margin, -self.margin)
        painter.setBrush(QBrush(Qt.white))
        painter.setPen(QPen(QColor("#bdbdbd"), 2))
        painter.drawRect(inner)

        # draw trajectory (scaled)
        if self.path_points:
            pen = QPen(QColor("#888"), 1)
            painter.setPen(pen)
            prev = None
            for p in self.path_points:
                sx, sy = self.map_to_canvas(p, inner)
                if prev is not None:
                    painter.drawLine(prev[0], prev[1], sx, sy)
                prev = (sx, sy)

            # draw red dot at current index
            cx, cy = self.map_to_canvas(self.path_points[self.index], inner)
            painter.setBrush(QBrush(QColor(200, 30, 30)))
            painter.setPen(QPen(QColor(150,20,20)))
            r = self.dot_px
            painter.drawEllipse(QRectF(cx - r, cy - r, r*2, r*2))

    def map_to_canvas(self, point, inner_rect):
        px, py = point
        inner_w = inner_rect.width()
        inner_h = inner_rect.height()
        # map x from [min_x, max_x] to inner_rect.x..x+width
        sx = inner_rect.x() + (px - self.min_x) / (self.max_x - self.min_x) * inner_w
        # map y, invert so that larger y in machine -> down in screen (keep natural)
        sy = inner_rect.y() + (py - self.min_y) / (self.max_y - self.min_y) * inner_h
        return sx, sy

# ---------------------------
# UI: main menu and visualization window
# ---------------------------
class VisualizationWindow(QWidget):
    def __init__(self, segments, raw_text):
        super().__init__()
        self.segments = segments
        self.raw_text = raw_text
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Laser App - Визуализация")
        self.setGeometry(180, 120, 1000, 700)

        # background
        p = QPalette()
        p.setColor(QPalette.Window, QColor(200,200,200))
        self.setAutoFillBackground(True)
        self.setPalette(p)

        title = QLabel("Процесс визуализации", self)
        title.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        title.setStyleSheet("font-size:28px; font-weight:bold; color:#222;")
        title.setFixedHeight(60)

        # left: drawing area; right: raw text box
        self.drawing = DrawingWidget(self.segments)
        self.draw_frame_layout = QVBoxLayout()
        self.draw_frame_layout.addWidget(self.drawing)

        # raw text
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.raw_text)
        text.setStyleSheet("background-color: white; color: black;")
        text.setFixedWidth(320)

        hbox = QHBoxLayout()
        hbox.addLayout(self.draw_frame_layout)
        hbox.addWidget(text)

        main_layout = QVBoxLayout()
        main_layout.addWidget(title)
        main_layout.addLayout(hbox)
        self.setLayout(main_layout)

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
        layout.addSpacerItem(QSpacerItem(20,40, QSizePolicy.Minimum, QSizePolicy.Expanding))
        layout.addWidget(self.play_button, alignment=Qt.AlignHCenter)
        layout.addWidget(self.exit_button, alignment=Qt.AlignHCenter)
        layout.addSpacerItem(QSpacerItem(20,40, QSizePolicy.Minimum, QSizePolicy.Expanding))
        self.setLayout(layout)

    def on_play(self):
        # load and parse file, then open visualization window
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
