# main.py (updated: fullscreen + wheel zoom + pan clamping + speed control + logo only on main + editable gcode + file check + fix main window size on back + fixed G00/G01 parsing + fixed line drawing + removed gray lines)
import sys
import math
import re
from pathlib import Path
import os  # Добавлен для проверки файлов
from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QTextEdit,
    QSizePolicy, QSpacerItem, QHBoxLayout, QSlider, QFrame, QMessageBox,
    QFileDialog, QStackedWidget
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPoint
from PySide6.QtGui import QColor, QPalette, QPainter, QBrush, QPen, QFont, QImage, QPixmap
# ---------------------------
# OCR (easyocr) — опционально
# ---------------------------
try:
    import easyocr
    import ssl
    import warnings
    ssl._create_default_https_context = ssl._create_unverified_context
    warnings.filterwarnings("ignore", category=UserWarning)
    _OCR_READER = None
    def get_ocr_reader(langs=["en"]):
        global _OCR_READER
        if _OCR_READER is None:
            _OCR_READER = easyocr.Reader(langs, gpu=False)
        return _OCR_READER
    def normalize_gcode_text(raw_text: str) -> str:
        txt = raw_text.replace('\r', '\n')
        parts = re.split(r'[\n;]+', txt)
        cleaned = []
        for p in parts:
            s = p.strip()
            if not s:
                continue
            subparts = re.split(r'(?=[GMgm]\d)', s)
            for sub in subparts:
                s2 = sub.strip()
                if s2:
                    s2 = re.sub(r'\s+', ' ', s2)
                    cleaned.append(s2)
        return "\n".join(cleaned)
    def fix_common_ocr_mistakes(text: str) -> str:
        corrected_lines = []
        for line in text.splitlines():
            # Заменяем '6' на 'G' в начале строки, если за ней цифра
            if line.startswith('6') and len(line) > 1 and line[1].isdigit():
                line = 'G' + line[1:]
            # Заменяем 'O', 'О', '⁰', '₀' на '0'
            o_like_chars = {
                'O': '0',
                'О': '0',  # Кириллическая 'О'
                '⁰': '0',
                '₀': '0',
            }
            for old_char, new_char in o_like_chars.items():
                line = line.replace(old_char, new_char)
            # --- Новая логика для '1' и 'I' ---
            tokens = line.split()
            corrected_tokens = []
            for token in tokens:
                if token.startswith('1'):
                    if len(token) > 1:
                        next_char = token[1]
                        if next_char.isdigit():
                            corrected_token = 'I' + token[1:]
                        elif next_char.isalpha():
                            corrected_token = token
                        else:
                            corrected_token = 'I' + token[1:]
                    else:
                        corrected_token = 'I'
                    corrected_tokens.append(corrected_token)
                else:
                    corrected_tokens.append(token)
            corrected_line = ' '.join(corrected_tokens)
            # --- Добавляем исправление M0S -> M05 ---
            # Исправляем M0S на M05 (частая OCR-ошибка в G-коде)
            # Например: M0S, M0S S1000 → M05, M05 S1000
            corrected_line = corrected_line.replace('M0S', 'M05')
            # Дополнительно: можно исправлять M0s (если 's' строчная)
            corrected_line = corrected_line.replace('M0s', 'M05')
            # --- Добавляем исправление Y5o -> Y50 ---
            corrected_line = corrected_line.replace('Y5o', 'Y50')
            corrected_lines.append(corrected_line)
        return "\n".join(corrected_lines)
    def text_recognition(file_path, text_file_name="gcode.txt") -> str:
        reader = get_ocr_reader(["en"])
        result = reader.readtext(file_path, detail=1, contrast_ths=0.05,
                                 text_threshold=0.3, low_text=0.2, link_threshold=0.2)
        texts = [t[1] for t in result if t and len(t) >= 2]
        raw_joined = "\n".join(texts)
        fixed_joined = fix_common_ocr_mistakes(raw_joined)
        normalized = normalize_gcode_text(fixed_joined)
        with open(text_file_name, "w", encoding="utf-8") as f:
            f.write(normalized)
        return normalized
except Exception:
    easyocr = None
    _OCR_READER = None
    def text_recognition(file_path, text_file_name="gcode.txt"):
        raise RuntimeError("easyocr недоступен")
# ---------------------------
# G-code parser
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
    segments = []
    absolute = True
    units = "mm"
    cur_x, cur_y = 0.0, 0.0
    laser_on = False
    current_feed = None
    default_cut_feed = 1000.0
    default_rapid_feed = 3000.0
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
            gx = get("X");
            gy = get("Y")
            if gx is not None:
                cur_x = gx
            if gy is not None:
                cur_y = gy
            continue
        if cmd == "G28":
            seg = {'type': 'move', 'points': [(cur_x, cur_y), (0.0, 0.0)], 'pause': 0.0, 'laser': laser_on,
                   'feedrate': current_feed, 'rapid': True}
            segments.append(seg)
            cur_x, cur_y = 0.0, 0.0
            continue
        if cmd == "M03":
            laser_on = True
            continue
        if cmd == "M05":
            laser_on = False
            continue
        if cmd == "G04":
            p = get("P") or 0.0
            seg = {'type': 'pause', 'points': [], 'pause': float(p) / 1000.0, 'laser': laser_on}
            segments.append(seg)
            continue
        fval = get("F")
        if fval is not None:
            current_feed = fval
        if cmd in ("G00", "G01"):
            tx = get("X");
            ty = get("Y")
            if tx is None: tx = cur_x
            if ty is None: ty = cur_y
            if not absolute:
                tx = cur_x + tx
                ty = cur_y + ty
            if cmd == "G00":
                chosen_feed = current_feed if current_feed is not None else default_rapid_feed
                rapid_flag = True
            else:
                chosen_feed = current_feed if current_feed is not None else default_cut_feed
                rapid_flag = False
            dist = math.hypot(tx - cur_x, ty - cur_y)
            steps = max(1, int(dist / 1.0))
            pts = []
            for i in range(1, steps + 1):
                t = i / steps
                x = cur_x + (tx - cur_x) * t
                y = cur_y + (ty - cur_y) * t
                pts.append((x, y))
            # ИСПРАВЛЕНИЕ: БЫЛО (cur_x, cur_x), ДОЛЖНО БЫТЬ (cur_x, cur_y)
            seg = {'type': 'move', 'points': [(cur_x, cur_y)] + pts, 'pause': 0.0, 'laser': laser_on,
                   'feedrate': chosen_feed, 'rapid': rapid_flag}
            segments.append(seg)
            cur_x, cur_y = tx, ty
            continue
        if cmd in ("G02", "G03"):
            tx = get("X");
            ty = get("Y")
            ioff = get("I") or 0.0
            joff = get("J") or 0.0
            if tx is None: tx = cur_x
            if ty is None: ty = cur_y
            if not absolute:
                tx = cur_x + tx
                ty = cur_y + ty
            chosen_feed = current_feed if current_feed is not None else default_cut_feed
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
            seg = {'type': 'move', 'points': [(cur_x, cur_y)] + pts, 'pause': 0.0, 'laser': laser_on,
                   'feedrate': chosen_feed, 'rapid': False}
            segments.append(seg)
            cur_x, cur_y = tx, ty
            continue
    return segments
# ---------------------------
# DrawingWidget: timeline + wheel zoom + pan clamping + speed control
# ---------------------------
class DrawingWidget(QWidget):
    def __init__(self, segments, parent=None):
        super().__init__(parent)
        self.segments = segments
        self.margin = 20
        self.dot_px = 8
        # --- НОВОЕ: Добавляем список сегментов для отрисовки ---
        self.draw_segments = self.build_draw_segments(segments)
        # --- КОНЕЦ НОВОГО ---
        self.timeline = self.build_timeline(segments)
        self.index = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)
        self.running = False
        self.user_zoom = 1.0
        self.invert_x = False
        self.invert_y = False
        self.speed_factor = 1.0  # Default speed factor (1.0x)
        # Панорамирование
        self.pan_offset_x = 0.0
        self.pan_offset_y = 0.0
        self.last_mouse_pos = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.min_x = 0.0;
        self.max_x = 1.0;
        self.min_y = 0.0;
        self.max_y = 1.0
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

    # --- НОВЫЙ МЕТОД: Строит список сегментов для отрисовки ---
    def build_draw_segments(self, segments):
        draw_segs = []
        for seg in segments:
            if seg['type'] == 'move' and len(seg['points']) >= 2:
                start_point = seg['points'][0]
                end_point = seg['points'][-1] # Берем последнюю точку сегмента
                draw_segs.append({
                    'start': start_point,
                    'end': end_point,
                    'draw': bool(seg.get('laser', False)),
                    'rapid': bool(seg.get('rapid', False)), # Добавляем флаг для стиля
                    'type': 'line' # Для линейных сегментов
                })
            elif seg['type'] == 'move' and len(seg['points']) > 2: # Дуга
                # Для дуги, если нужно отрисовывать как дугу, можно сохранить центр и радиус
                # или список точек. Здесь сохраним точки для отрисовки как полилинию.
                # Однако, для прямых линий под углом, мы хотим использовать только start и end.
                # Оставим дуги как есть, но они будут отрисовываться как полилиния точек из timeline.
                # Для прямых линий, мы уже сохранили start и end.
                # Если сегмент - дуга, он не попадет в 'line' тип выше.
                # Оставим его как есть для таймлайна, но для отрисовки как дуги нужно будет другое.
                # Пока оставим только линии.
                # Правильнее будет оставить дугу как 'arc' тип сеегмента.
                # Нужно пересмотреть логику в parse_and_build_path для дуг.
                # Или использовать точки таймлайна для отрисовки дуг.
                # Пока оставим как есть, но учтем, что дуги будут отрисовываться как полилиния.
                # Для прямых линий - основная задача выполнена.
                # Если нужно отрисовывать дуги как гладкие дуги, нужно будет хранить центр/радиус.
                # Пока оставим как есть.
                # Нет, для дуг тоже можно сохранить start и end, и отрисовывать как дугу.
                # Нужно хранить центр, радиус, начальный и конечный углы.
                # Переделаем логику в parse_and_build_path и build_draw_segments.
                # Но для упрощения, если дуга представлена списком точек, можно отрисовать её как линию между start и end.
                # Но это не будет дугой.
                # Оставим дуги как полилинию точек в таймлайне, а отрисовка их как дуг требует отдельной логики.
                # Для задачи "линии под углом" - основная цель - линии (G00, G01).
                # Дуги (G02, G03) отрисовываются как набор точек, что уже является "гладким" приближением дуги.
                # Поэтому, для дуг оставим как есть, а для линий сделаем прямую.
                # Таким образом, draw_segments будет содержать start и end только для сегментов, которые мы хотим отрисовать как прямые линии.
                # parse_and_build_path уже делает это для G00 и G01.
                # Поэтому текущая логика build_draw_segments подходит для прямых линий.
                # Для дуг - отрисовка будет происходить через таймлайн как полилиния.
                # Оставим так.
                pass # Логика для дуг будет в paintEvent через timeline
        return draw_segs
    # --- КОНЕЦ НОВОГО МЕТОДА ---

    def build_timeline(self, segments):
        timeline = []
        MIN_DT = 0.01
        for i, seg in enumerate(segments): # Добавляем индекс сегмента
            if seg['type'] == 'move':
                pts = seg['points']
                feed = seg.get('feedrate')
                rapid = seg.get('rapid', False)
                if feed is None:
                    feed = 3000.0 if rapid else 1000.0
                speed_mm_s = max(0.001, feed / 60.0)
                for j in range(len(pts)):
                    x, y = pts[j]
                    if j < len(pts) - 1:
                        nx, ny = pts[j + 1]
                        dist = math.hypot(nx - x, ny - y)
                        dt = max(MIN_DT, dist / speed_mm_s)
                    else:
                        dt = MIN_DT
                    timeline.append({
                        'x': x, 'y': y, 'draw': bool(seg.get('laser', False)),
                        'dt': dt, 'seg_index': i # Сохраняем индекс сегмента
                    })
            elif seg['type'] == 'pause':
                if timeline:
                    last = timeline[-1]
                    timeline.append({
                        'x': last['x'], 'y': last['y'], 'draw': last['draw'],
                        'dt': max(MIN_DT, seg.get('pause', 0.0)), 'seg_index': last.get('seg_index', -1)
                    })
        if timeline:
            timeline[-1]['dt'] = 0.0
        return timeline

    def start(self):
        if not self.timeline:
            return
        if self.index >= len(self.timeline):
            self.index = 0
        self.running = True
        self.update()
        next_dt = max(0.01, self.timeline[self.index]['dt']) / self.speed_factor
        self.timer.start(int(next_dt * 1000))

    def stop(self):
        self.timer.stop()
        self.running = False

    def reset(self):
        self.stop()
        self.index = 0
        self.update()

    def step(self):
        if self.index < len(self.timeline) - 1:
            self.index += 1
            self.update()
            next_dt = max(0.01, self.timeline[self.index]['dt']) / self.speed_factor
            if self.index < len(self.timeline) - 1:
                self.timer.start(int(next_dt * 1000))
            else:
                self.timer.start(int(next_dt * 1000))
        else:
            self.stop()

    def set_user_zoom(self, zoom_factor: float):
        self.user_zoom = max(0.01, float(zoom_factor))
        self.update()

    def set_speed_factor(self, speed_factor: float):
        """Set the speed multiplier for animation (0.5x to 2.0x)"""
        self.speed_factor = max(0.5, min(2.0, float(speed_factor)))
        if self.running:
            # Restart the timer with new speed
            self.timer.stop()
            next_dt = max(0.01, self.timeline[self.index]['dt']) / self.speed_factor
            self.timer.start(int(next_dt * 1000))

    def toggle_invert_x(self):
        self.invert_x = not self.invert_x
        self.update()

    def toggle_invert_y(self):
        self.invert_y = not self.invert_y
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.last_mouse_pos = event.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.last_mouse_pos is not None and event.buttons() == Qt.LeftButton:
            dx = event.pos().x() - self.last_mouse_pos.x()
            dy = event.pos().y() - self.last_mouse_pos.y()
            dx_mm = dx / self.scale
            dy_mm = -dy / self.scale
            self.pan_offset_x += dx_mm
            self.pan_offset_y += dy_mm
            self.last_mouse_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.last_mouse_pos = None

    def wheelEvent(self, event):
        # Zoom centered on mouse cursor
        delta = event.angleDelta().y()
        if delta == 0:
            return
        # compute inner rect
        rect = self.rect()
        inner = rect.adjusted(self.margin, self.margin, -self.margin, -self.margin)
        # Data point under cursor (before zoom)
        cursor_pos = event.position() if hasattr(event, 'position') else event.pos()
        cx = cursor_pos.x()
        cy = cursor_pos.y()
        data_x, data_y = self.map_canvas_to_data((cx, cy), inner)
        # compute zoom factor
        factor = 1.0 + (delta / 1200.0)
        if factor <= 0:
            factor = 0.9
        self.user_zoom = max(0.01, self.user_zoom * factor)
        # After zoom, adjust pan so that data_x,data_y stays under cursor
        # Recompute transform (will be done in paint), but we need intermediate values
        self.compute_transform(inner)
        # Compute new pan offsets so that data point maps to same canvas coords
        new_pan_x = self.compute_pan_to_keep_canvas_point((cx, cy), (data_x, data_y), inner)
        new_pan_y = self.compute_pan_to_keep_canvas_to_data_point((cx, cy), (data_x, data_y), inner, vertical=True)
        if new_pan_x is not None:
            self.pan_offset_x = new_pan_x
        if new_pan_y is not None:
            self.pan_offset_y = new_pan_y
        # Clamp pan to keep drawing visible
        self.clamp_pan(inner)
        self.update()

    def compute_pan_to_keep_canvas_to_data_point(self, canvas_point, data_point, inner_rect, vertical=False):
        # Solve for pan_offset so that data_point maps to canvas_point
        cx, cy = canvas_point
        dx, dy = data_point
        data_w = (self.max_x - self.min_x)
        data_h = (self.max_y - self.min_y)
        drawing_w = data_w * self.scale
        drawing_h = data_h * self.scale
        base_x = inner_rect.x() + self.left_pad
        base_y = inner_rect.y() + self.top_pad
        if not vertical:
            if not self.invert_x:
                # sx = base_x + ((dx + pan - min_x) / data_w) * drawing_w
                pan = ((cx - base_x) / drawing_w) * data_w - (dx - self.min_x)
            else:
                # sx = base_x + (1 - ((dx + pan - min_x) / data_w)) * drawing_w
                pan = ((base_x + drawing_w - cx) / drawing_w) * data_w - (dx - self.min_x)
            return pan
        else:
            if not self.invert_y:
                pan = ((cy - base_y) / drawing_h) * data_h - (dy - self.min_y)
            else:
                pan = ((base_y + drawing_h - cy) / drawing_h) * data_h - (dy - self.min_y)
            return pan

    # --- ОБНОВЛЁННЫЙ paintEvent ---
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
        # After computing transform, clamp pan to ensure drawing visible
        self.clamp_pan(inner)

        # --- Рисуем завершенные сегменты как прямые линии ---
        for seg_info in self.draw_segments:
            # Рисуем только если лазер был включен
            if not seg_info['draw']:
                continue # Пропускаем сегменты без лазера
            # Находим индекс последней точки этого сегмента в таймлайне
            timeline_idx_end_of_seg = -1
            target_end = seg_info['end']
            for j in range(len(self.timeline) - 1, -1, -1): # Ищем с конца
                if (abs(self.timeline[j]['x'] - target_end[0]) < 1e-6 and
                    abs(self.timeline[j]['y'] - target_end[1]) < 1e-6 and
                    self.timeline[j]['seg_index'] == self.timeline[j-1]['seg_index'] if j > 0 else True): # Проверяем, что это конец сегмента
                    if j == len(self.timeline) - 1 or self.timeline[j+1]['seg_index'] != self.timeline[j]['seg_index']:
                         timeline_idx_end_of_seg = j
                         break

            # Рисуем, если текущий индекс в таймлайне >= индекса конца сегмента
            if self.index >= timeline_idx_end_of_seg and timeline_idx_end_of_seg != -1:
                 start_canvas = self.map_to_canvas(seg_info['start'], inner)
                 end_canvas = self.map_to_canvas(seg_info['end'], inner)

                 # Устанавливаем перо в зависимости от типа сегмента (лазер/холостой)
                 # Теперь рисуем только лазерные линии
                 painter.setPen(QPen(QColor("#e33"), 2)) # Лазер - всегда красный

                 painter.drawLine(start_canvas[0], start_canvas[1], end_canvas[0], end_canvas[1])

        # --- Рисуем текущую позицию ---
        if self.index < len(self.timeline):
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
            painter.drawEllipse(QRectF(sx - r, sy - r, r * 2, r * 2))

        # --- Рисуем дуги и активный сегмент ---
        # Отрисовка дуг как полилинии и текущего активного сегмента
        pen_on = QPen(QColor("#e33"), 2)
        painter.setPen(pen_on)

        for i in range(1, self.index + 1):
            a = self.timeline[i - 1]
            b = self.timeline[i]
            # Проверяем, находится ли пара точек в сегменте, который уже отрисован как прямая линия
            seg_idx_a = a.get('seg_index', -1)
            seg_idx_b = b.get('seg_index', -1)
            if seg_idx_a == seg_idx_b and seg_idx_a < len(self.segments):
                seg = self.segments[seg_idx_a]
                # Если сегмент уже отрисован как прямая (т.е. его индекс есть в draw_segments и он завершен), пропускаем
                is_drawn_line = False
                for ds in self.draw_segments:
                     # Нужно сопоставить индекс сегмента с его start/end
                     # Это сложно сделать напрямую. Лучше хранить индекс в draw_segments.
                     # Переделаем build_draw_segments и paintEvent.
                     # Добавим индекс сегмента в draw_segments
                     pass
                # Более простой способ: отрисовать дуги отдельно.
                # Найдем сегменты, которые являются дугами.
                # Переделаем всю логику.

        # Переделаем paintEvent полностью
        # 1. Найдем завершенные линейные сегменты и нарисуем их как прямые линии
        # 2. Найдем завершенные дуговые сегменты и нарисуем их как полилинии (или дуги, если возможно)
        # 3. Найдем текущий активный сегмент и нарисуем его от начала до текущей позиции
        # 4. Нарисуем текущую позицию

        # Сбросим painter и начнем заново
        painter.setBrush(QBrush(Qt.white))
        painter.setPen(QPen(QColor("#bdbdbd"), 2))
        painter.drawRect(inner)

        if not self.timeline:
            return

        self.compute_transform(inner)
        self.clamp_pan(inner)

        # --- Нарисуем все завершенные сегменты ---
        drawn_seg_indices = set()
        for i in range(1, min(self.index + 1, len(self.timeline))):
            a = self.timeline[i - 1]
            b = self.timeline[i]
            seg_idx_a = a.get('seg_index', -1)
            seg_idx_b = b.get('seg_index', -1)

            if seg_idx_a != seg_idx_b:
                # Это переход между сегментами. Значит, сегмент seg_idx_a завершен.
                drawn_seg_indices.add(seg_idx_a)

        # Отрисовка завершенных сегментов как прямых линий (для G00, G01) или полилиний (для G02, G03)
        for seg_idx, seg in enumerate(self.segments):
             if seg_idx in drawn_seg_indices:
                 if seg['type'] == 'move':
                     pts = seg['points']
                     if len(pts) < 2: continue
                     if seg_idx < len(self.draw_segments): # Это линейный сегмент (G00/G01)
                          ds = self.draw_segments[seg_idx]
                          # Рисуем только если лазер включен
                          if not ds['draw']:
                              continue # Пропускаем холостой ход
                          start_canvas = self.map_to_canvas(ds['start'], inner)
                          end_canvas = self.map_to_canvas(ds['end'], inner)
                          painter.setPen(QPen(QColor("#e33"), 2)) # Лазер
                          painter.drawLine(start_canvas[0], start_canvas[1], end_canvas[0], end_canvas[1])
                     else: # Это дуговой сегмент (G02/G03) или другой - отрисовываем как полилинию
                          # Найдем все точки этого сегмента в таймлайне
                          seg_points = []
                          for pt in self.timeline:
                              if pt.get('seg_index') == seg_idx:
                                  seg_points.append((pt['x'], pt['y']))
                          if len(seg_points) > 1:
                              if seg.get('laser', False):
                                  painter.setPen(QPen(QColor("#e33"), 2))
                              else:
                                  continue # Пропускаем холостой ход для дуг
                              for j in range(len(seg_points) - 1):
                                  p1 = self.map_to_canvas(seg_points[j], inner)
                                  p2 = self.map_to_canvas(seg_points[j+1], inner)
                                  painter.drawLine(p1[0], p1[1], p2[0], p2[1])

        # --- Нарисуем текущий активный сегмент ---
        if self.index > 0 and self.index < len(self.timeline):
            current_seg_idx = self.timeline[self.index].get('seg_index', -1)
            if current_seg_idx != -1 and current_seg_idx not in drawn_seg_indices:
                # Найдем начало текущего сегмента
                start_of_current_seg = 0
                for j in range(self.index, -1, -1):
                    if self.timeline[j].get('seg_index') != current_seg_idx:
                        start_of_current_seg = j + 1
                        break
                # Рисуем от начала сегмента до текущей позиции
                start_point = (self.timeline[start_of_current_seg]['x'], self.timeline[start_of_current_seg]['y'])
                current_point = (self.timeline[self.index]['x'], self.timeline[self.index]['y'])
                start_canvas = self.map_to_canvas(start_point, inner)
                current_canvas = self.map_to_canvas(current_point, inner)

                seg_laser = self.timeline[self.index].get('draw', False)
                if seg_laser:
                    painter.setPen(QPen(QColor("#e33"), 2))
                    painter.drawLine(start_canvas[0], start_canvas[1], current_canvas[0], current_canvas[1])
                # else: # Не рисуем линию для холостого хода

        # --- Нарисуем текущую позицию ---
        if self.index < len(self.timeline):
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
            painter.drawEllipse(QRectF(sx - r, sy - r, r * 2, r * 2))


    # --- КОНЕЦ ОБНОВЛЁННОГО paintEvent ---

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

    def clamp_pan(self, inner_rect):
        # Ensure that the bounding box of the data (after pan & scale) stays within inner_rect
        # We'll compute allowed pan interval from all four corners and intersect.
        data_w = (self.max_x - self.min_x)
        data_h = (self.max_y - self.min_y)
        drawing_w = data_w * self.scale
        drawing_h = data_h * self.scale
        base_x = inner_rect.x() + self.left_pad
        base_y = inner_rect.y() + self.top_pad
        # compute canvas coords for corners with pan=0
        def canvas_no_pan(px, py):
            # map with pan=0
            x_rel = (px - self.min_x) / data_w if data_w != 0 else 0.0
            y_rel = (py - self.min_y) / data_h if data_h != 0 else 0.0
            if not self.invert_x:
                sx = base_x + x_rel * drawing_w
            else:
                sx = base_x + (1.0 - x_rel) * drawing_w
            if not self.invert_y:
                sy = base_y + y_rel * drawing_h
            else:
                sy = base_y + (1.0 - y_rel) * drawing_h
            return sx, sy
        corners = [
            (self.min_x, self.min_y),
            (self.min_x, self.max_y),
            (self.max_x, self.min_y),
            (self.max_x, self.max_y),
        ]
        inner_x0 = inner_rect.x()
        inner_x1 = inner_rect.x() + inner_rect.width()
        inner_y0 = inner_rect.y()
        inner_y1 = inner_rect.y() + inner_rect.height()
        # for each corner derive pan constraints
        pan_x_min = -1e12
        pan_x_max = 1e12
        pan_y_min = -1e12
        pan_y_max = 1e12
        factor_x = self.scale * (1.0 if not self.invert_x else -1.0)
        factor_y = self.scale * (1.0 if not self.invert_y else -1.0)
        for (px, py) in corners:
            sx0, sy0 = canvas_no_pan(px, py)
            # requiring inner_x0 <= sx0 + pan*factor_x <= inner_x1
            if factor_x != 0:
                low = (inner_x0 - sx0) / factor_x
                high = (inner_x1 - sx0) / factor_x
                if low > high:
                    low, high = high, low
                pan_x_min = max(pan_x_min, low)
                pan_x_max = min(pan_x_max, high)
            if factor_y != 0:
                low = (inner_y0 - sy0) / factor_y
                high = (inner_y1 - sy0) / factor_y
                if low > high:
                    low, high = high, low
                pan_y_min = max(pan_y_min, low)
                pan_y_max = min(pan_y_max, high)
        # If ranges are invalid (e.g., drawing larger than inner), allow centering by clamping to mid
        if pan_x_min > pan_x_max:
            # center horizontally
            pan_x = (pan_x_min + pan_x_max) / 2.0
            self.pan_offset_x = pan_x
        else:
            self.pan_offset_x = max(pan_x_min, min(self.pan_offset_x, pan_x_max))
        if pan_y_min > pan_y_max:
            pan_y = (pan_y_min + pan_y_max) / 2.0
            self.pan_offset_y = pan_y
        else:
            self.pan_offset_y = max(pan_y_min, min(self.pan_offset_y, pan_y_max))

    def map_to_canvas(self, point, inner_rect):
        px, py = point
        inner_x = inner_rect.x()
        inner_y = inner_rect.y()
        inner_w = inner_rect.width()
        inner_h = inner_rect.height()
        data_w = (self.max_x - self.min_x)
        data_h = (self.max_y - self.min_y)
        px += self.pan_offset_x
        py += self.pan_offset_y
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

    def map_canvas_to_data(self, canvas_point, inner_rect):
        cx, cy = canvas_point
        data_w = (self.max_x - self.min_x)
        data_h = (self.max_y - self.min_y)
        drawing_w = data_w * self.scale
        drawing_h = data_h * self.scale
        base_x = inner_rect.x() + self.left_pad
        base_y = inner_rect.y() + self.top_pad
        if drawing_w == 0 or drawing_h == 0:
            return self.min_x, self.min_y
        if not self.invert_x:
            x_rel = (cx - base_x) / drawing_w
        else:
            x_rel = 1.0 - ((cx - base_x) / drawing_w)
        if not self.invert_y:
            y_rel = (cy - base_y) / drawing_h
        else:
            y_rel = 1.0 - ((cy - base_y) / drawing_h)
        px = self.min_x + x_rel * data_w - self.pan_offset_x
        py = self.min_y + y_rel * data_h - self.pan_offset_y
        return px, py

# ---------------------------
# НОВЫЙ КЛАСС: Управление всеми страницами в одном окне
# ---------------------------
class AppWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("StanVision")
        self.setGeometry(100, 100, 1024, 768)
        # Основной контейнер для всех страниц
        self.stacked_widget = QStackedWidget()
        # Создаем все страницы
        self.main_menu = MainMenu(self)
        self.machine_selection = MachineSelectionWindow(self)
        self.visualization = None  # Будет создан при необходимости
        # Добавляем страницы в стек
        self.stacked_widget.addWidget(self.main_menu)
        self.stacked_widget.addWidget(self.machine_selection)
        # Основной layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.stacked_widget)
        self.setLayout(main_layout)
        # Максимизируем окно
        self.showMaximized()

    def show_main_menu(self):
        self.stacked_widget.setCurrentWidget(self.main_menu)

    def show_machine_selection(self):
        self.stacked_widget.setCurrentWidget(self.machine_selection)

    def show_visualization(self, segments, raw_text):
        # Создаем VisualizationWindow при первом вызове
        if self.visualization is None:
            self.visualization = VisualizationWindow(segments, raw_text, self)
            self.stacked_widget.addWidget(self.visualization)
        else:
            # Обновляем данные, если окно уже существует
            self.visualization.update_data(segments, raw_text)
        self.stacked_widget.setCurrentWidget(self.visualization)

    def go_back(self):
        current = self.stacked_widget.currentWidget()
        if current == self.visualization:
            self.show_machine_selection()
        elif current == self.machine_selection:
            self.show_main_menu()

    def close_application(self):
        """Закрываем всё приложение"""
        QApplication.instance().quit()

# ---------------------------
# UI Windows — переделаны для работы в одном окне
# ---------------------------
class MainMenu(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.init_ui()

    def init_ui(self):
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(240, 240, 240))
        self.setAutoFillBackground(True)
        self.setPalette(palette)

        top_bar = QHBoxLayout()
        # Убираем кнопку "назад" из главного меню, так как это стартовая страница
        title = QLabel("StanVision")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:28px; font-weight:bold; color:#222;")
        top_bar.addStretch(1)
        top_bar.addWidget(title, stretch=2)
        top_bar.addStretch(1)

        logo_label = QLabel()
        try:
            logo_path = "stankin-logo-main-color-en-rgb!-01.png"
            if os.path.exists(logo_path):
                logo_pixmap = QPixmap(logo_path)
                if logo_pixmap.isNull():
                    raise FileNotFoundError("Pixmap is null, file might be corrupted or unsupported format.")
            else:
                raise FileNotFoundError("Logo file does not exist.")
        except Exception as e:
            print(f"Warning: Could not load logo image - {e}")
            logo_pixmap = QPixmap(400, 150)
            logo_pixmap.fill(QColor(240, 240, 240))
            painter = QPainter(logo_pixmap)
            painter.setPen(QColor(0, 0, 0))
            painter.setFont(QFont("Arial", 16, QFont.Bold))
            painter.drawText(logo_pixmap.rect(), Qt.AlignCenter, "STANKIN LOGO")
            painter.end()
        scaled_logo = logo_pixmap.scaled(400, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        logo_label.setPixmap(scaled_logo)
        logo_label.setAlignment(Qt.AlignCenter)

        app_name = QLabel("StanVision")
        app_name.setStyleSheet("font-size: 24px; font-weight: bold; color: black;")
        app_name.setAlignment(Qt.AlignCenter)

        self.start_button = QPushButton("Начать визуализацию")
        self.exit_button = QPushButton("Выход")

        self.start_button.clicked.connect(self.on_start)
        self.exit_button.clicked.connect(self.on_exit)

        style = """
            QPushButton { 
                background-color: #444; 
                color: white; 
                font-size: 18px; 
                padding: 12px; 
                border-radius: 8px; 
                min-width: 200px;
            }
            QPushButton:hover { 
                background-color: #666; 
            }
        """
        self.start_button.setStyleSheet(style)
        self.exit_button.setStyleSheet(style)

        # Логотип в самом верху
        layout = QVBoxLayout()
        layout.addWidget(logo_label, alignment=Qt.AlignTop | Qt.AlignHCenter)
        # Заголовок приложения под логотипом
        layout.addWidget(app_name)
        # Пространство между заголовком и кнопками
        layout.addSpacerItem(QSpacerItem(20, 30, QSizePolicy.Minimum, QSizePolicy.Expanding))
        # Кнопки в центре
        layout.addWidget(self.start_button, alignment=Qt.AlignHCenter)
        layout.addWidget(self.exit_button, alignment=Qt.AlignHCenter)
        # Пространство под кнопками
        layout.addSpacerItem(QSpacerItem(20, 30, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.setLayout(layout)

    def on_start(self):
        self.controller.show_machine_selection()

    def on_exit(self):
        """Обработчик для кнопки выхода"""
        self.controller.close_application()

class MachineSelectionWindow(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.init_ui()

    def init_ui(self):
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(240, 240, 240))
        self.setAutoFillBackground(True)
        self.setPalette(palette)

        top_bar = QHBoxLayout()
        back_btn = QPushButton("←")
        back_btn.setFixedSize(45, 30)
        back_btn.clicked.connect(self.on_back)

        title = QLabel("Выбери тип станка")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:20px; font-weight:bold;")

        top_bar.addWidget(back_btn, alignment=Qt.AlignLeft)
        top_bar.addStretch(1)
        top_bar.addWidget(title, stretch=2)
        top_bar.addStretch(1)

        laser_btn = QPushButton("Лазерный")
        drill_btn = QPushButton("Сверлильный")
        mill_btn = QPushButton("Токарный")

        btn_style = """
            QPushButton {
                background-color:#444;
                color:white;
                font-size:16px;
                border-radius:6px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color:#555;
            }
        """
        for btn in (laser_btn, drill_btn, mill_btn):
            btn.setStyleSheet(btn_style)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(48)

        laser_btn.clicked.connect(self.on_laser)
        drill_btn.clicked.connect(lambda: self._not_ready("Сверлильный"))
        mill_btn.clicked.connect(lambda: self._not_ready("Токарный"))

        button_widget = QWidget()
        button_layout = QVBoxLayout(button_widget)
        button_layout.setContentsMargins(80, 0, 80, 0)
        button_layout.setSpacing(15)
        button_layout.addWidget(laser_btn)
        button_layout.addWidget(drill_btn)
        button_layout.addWidget(mill_btn)

        layout = QVBoxLayout()
        layout.addLayout(top_bar)
        layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))
        layout.addWidget(button_widget)
        layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.setLayout(layout)

    def on_back(self):
        self.controller.go_back()

    def on_laser(self):
        try:
            lines = load_gcode_lines("gcode.txt")
            segments = parse_and_build_path(lines)
            raw = "\n".join(lines)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать gcode.txt:\n{e}")
            return
        self.controller.show_visualization(segments, raw)

    def _not_ready(self, name):
        QMessageBox.information(self, "В процессе", f"{name} — в процессе разработки.")

class VisualizationWindow(QWidget):
    def __init__(self, segments, raw_text, controller):
        super().__init__()
        self.controller = controller
        self.segments = segments
        self.raw_text = raw_text
        self.init_ui()

    def init_ui(self):
        p = QPalette()
        p.setColor(QPalette.Window, QColor(200, 200, 200))
        self.setAutoFillBackground(True)
        self.setPalette(p)

        top_bar = QHBoxLayout()
        back_btn = QPushButton("←")
        back_btn.setFixedSize(45, 30)
        back_btn.clicked.connect(self.on_back)

        title = QLabel("Процесс визуализации")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:28px; font-weight:bold; color:#222;")

        top_bar.addWidget(back_btn, alignment=Qt.AlignLeft)
        top_bar.addStretch(1)
        top_bar.addWidget(title, stretch=2)
        top_bar.addStretch(1)

        self.drawing = DrawingWidget(self.segments)
        self.drawing.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        drawing_frame = QFrame()
        drawing_frame.setLayout(QVBoxLayout())
        drawing_frame.layout().setContentsMargins(0, 0, 0, 0)
        drawing_frame.layout().addWidget(self.drawing)

        controls_layout = QVBoxLayout()

        invert_x_btn = QPushButton("Invert X")
        invert_y_btn = QPushButton("Invert Y")
        invert_x_btn.setCheckable(True);
        invert_y_btn.setCheckable(True)
        invert_x_btn.clicked.connect(lambda _: (self.drawing.toggle_invert_x(), self.drawing.update()))
        invert_y_btn.clicked.connect(lambda _: (self.drawing.toggle_invert_y(), self.drawing.update()))

        restart_btn = QPushButton("Restart")
        restart_btn.clicked.connect(self.on_restart)

        upload_btn = QPushButton("Загрузить фотографию")
        upload_btn.clicked.connect(self.on_upload_image)

        start_btn = QPushButton("Start")
        start_btn.clicked.connect(lambda: self.drawing.start())
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(lambda: self.drawing.stop())

        # Добавляем ползунок скорости
        speed_layout = QVBoxLayout()
        speed_label = QLabel("Скорость: 1.0x")
        self.speed_label = speed_label
        speed_slider = QSlider(Qt.Horizontal)
        speed_slider.setMinimum(5)  # 0.5x
        speed_slider.setMaximum(20)  # 2.0x
        speed_slider.setValue(10)  # 1.0x по умолчанию
        speed_slider.setTickPosition(QSlider.TicksBelow)
        speed_slider.setTickInterval(5)
        speed_slider.valueChanged.connect(self.on_speed_change)
        self.speed_slider = speed_slider
        speed_layout.addWidget(speed_label)
        speed_layout.addWidget(speed_slider)

        controls_layout.addWidget(invert_x_btn)
        controls_layout.addWidget(invert_y_btn)
        controls_layout.addLayout(speed_layout)  # Добавляем ползунок скорости

        h_ctrl = QHBoxLayout()
        h_ctrl.addWidget(upload_btn)
        h_ctrl.addWidget(start_btn)
        h_ctrl.addWidget(stop_btn)
        controls_layout.addLayout(h_ctrl)
        controls_layout.addWidget(restart_btn)

        controls_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        controls_layout.addWidget(QLabel("G-code (raw):"))
        self.text = QTextEdit()  # Сохраняем как атрибут класса
        self.text.setPlainText(self.raw_text)
        self.text.setStyleSheet("background-color: white; color: black;")
        self.text.setMinimumWidth(300)
        self.text.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        controls_layout.addWidget(self.text)

        hbox = QHBoxLayout()
        hbox.addWidget(drawing_frame, stretch=1)
        hbox.addLayout(controls_layout)

        main_layout = QVBoxLayout()
        main_layout.addLayout(top_bar)
        main_layout.addLayout(hbox)
        self.setLayout(main_layout)

    def update_data(self, segments, raw_text):
        """Обновляем данные при повторном входе в окно"""
        self.segments = segments
        self.raw_text = raw_text
        self.text.setPlainText(self.raw_text)
        # Сохраняем текущие настройки
        old_zoom = self.drawing.user_zoom
        old_pan_x = self.drawing.pan_offset_x
        old_pan_y = self.drawing.pan_offset_y
        old_invert_x = self.drawing.invert_x
        old_invert_y = self.drawing.invert_y
        old_speed = self.drawing.speed_factor
        try:
            self.drawing.stop()
        except:
            pass
        # Создаем новый виджет с новыми данными
        self.drawing = DrawingWidget(segments)
        self.drawing.user_zoom = old_zoom
        self.drawing.pan_offset_x = old_pan_x
        self.drawing.pan_offset_y = old_pan_y
        self.drawing.invert_x = old_invert_x
        self.drawing.invert_y = old_invert_y
        self.drawing.speed_factor = old_speed
        # Заменяем виджет в layout
        for fr in self.findChildren(QFrame):
            layout = fr.layout()
            if layout and layout.count() > 0:
                while layout.count():
                    it = layout.takeAt(0)
                    w = it.widget()
                    if w:
                        w.setParent(None)
                layout.addWidget(self.drawing)
                break

    def on_speed_change(self, value):
        factor = value / 10.0  # преобразуем в диапазон 0.5-2.0
        self.speed_label.setText(f"Скорость: {factor:.1f}x")
        self.drawing.set_speed_factor(factor)

    def on_restart(self):
        self.drawing.reset()

    def on_back(self):
        try:
            self.drawing.stop()
        except:
            pass
        self.controller.go_back()

    def on_upload_image(self):
        if easyocr is None:
            QMessageBox.critical(self, "Ошибка",
                                 "Библиотека easyocr не установлена. Установите через pip: pip install easyocr")
            return
        fname, _ = QFileDialog.getOpenFileName(self, "Выберите изображение с G-code", "",
                                               "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All files (*)")
        if not fname:
            return
        try:
            img = QImage(fname)
            if img.isNull():
                QMessageBox.critical(self, "Ошибка", "Не удалось открыть изображение.")
                return
            w = img.width();
            h = img.height()
            min_side = min(w, h)
            MIN_SIDE_PX = 300
            if min_side < MIN_SIDE_PX:
                QMessageBox.warning(self, "Плохое качество",
                                    f"Изображение слишком маленькое ({w}x{h}). Порог: {MIN_SIDE_PX}px. Пожалуйста, используйте более качественную фотографию.")
                return
        except Exception as e:
            QMessageBox.warning(self, "Предупреждение",
                                f"Не удалось проверить размер изображения: {e}\nПопробуйте другое изображение.")
            return

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            recognized = text_recognition(fname, "gcode.txt")
            QApplication.restoreOverrideCursor()
            if not recognized or not recognized.strip():
                QMessageBox.warning(self, "Распознавание",
                                    "Распознанный текст пуст. Попробуйте другое изображение или улучшите качество.")
                return
            self.raw_text = recognized
            self.text.setPlainText(self.raw_text)  # Обновляем текст в поле
            lines = [ln.strip() for ln in recognized.splitlines() if ln.strip()]
            segments = parse_and_build_path(lines)
            if not segments:
                QMessageBox.warning(self, "Парсер", "После распознавания не найдено сегментов G-code.")
                return
            # Обновляем данные в текущем окне
            self.update_data(segments, recognized)
            QMessageBox.information(self, "Готово",
                                    "Изображение распознано, G-code записан в gcode.txt и визуализация обновлена. Нажмите 'Start' для запуска.")
        except RuntimeError as rexc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка OCR", str(rexc))
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка OCR", f"Ошибка при распознавании изображения:\n{e}")

# ---------------------------
# Запуск
# ---------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app_window = AppWindow()
    sys.exit(app.exec())