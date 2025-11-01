import json
import re

def parse_gcode_line(line):
    """Парсит одну строку G-кода и возвращает структуру данных (словарь)."""
    line = line.strip()
    if not line or line.startswith(';'):
        return None  # Пропускаем пустые строки и комментарии

    # Разделяем строку на части
    parts = re.findall(r'([A-Z][0-9\.\-]+)', line)
    if not parts:
        return None

    command = parts[0]
    data = {"command": command}

    # -------------------------
    # Команды перемещений
    # -------------------------
    if command in ["G00", "G01"]:
        move_type = "rapid_move" if command == "G00" else "linear_move"
        coords = {"X": None, "Y": None}
        feedrate = None

        for part in parts[1:]:
            if part.startswith("X"):
                coords["X"] = float(part[1:])
            elif part.startswith("Y"):
                coords["Y"] = float(part[1:])
            elif part.startswith("F"):
                feedrate = float(part[1:])

        data["type"] = move_type
        data["to"] = coords
        if feedrate:
            data["feedrate"] = feedrate

    # -------------------------
    # Круговые движения
    # -------------------------
    elif command in ["G02", "G03"]:
        data["type"] = "arc_move"
        data["direction"] = "CW" if command == "G02" else "CCW"
        coords = {"X": None, "Y": None}
        center = {"I": None, "J": None}

        for part in parts[1:]:
            if part.startswith("X"):
                coords["X"] = float(part[1:])
            elif part.startswith("Y"):
                coords["Y"] = float(part[1:])
            elif part.startswith("I"):
                center["I"] = float(part[1:])
            elif part.startswith("J"):
                center["J"] = float(part[1:])

        data["to"] = coords
        data["center_offset"] = center

    # -------------------------
    # Пауза
    # -------------------------
    elif command == "G04":
        data["type"] = "pause"
        duration = next((float(part[1:]) for part in parts if part.startswith("P")), 0)
        data["duration_ms"] = duration

    # -------------------------
    # Единицы измерения
    # -------------------------
    elif command == "G20":
        data["type"] = "units"
        data["units"] = "inches"
    elif command == "G21":
        data["type"] = "units"
        data["units"] = "mm"

    # -------------------------
    # Режим координат
    # -------------------------
    elif command == "G90":
        data["type"] = "position_mode"
        data["mode"] = "absolute"
    elif command == "G91":
        data["type"] = "position_mode"
        data["mode"] = "relative"

    # -------------------------
    # Установка нуля
    # -------------------------
    elif command == "G92":
        data["type"] = "set_position"
        origin = {"X": None, "Y": None}
        for part in parts[1:]:
            if part.startswith("X"):
                origin["X"] = float(part[1:])
            elif part.startswith("Y"):
                origin["Y"] = float(part[1:])
        data["new_origin"] = origin

    # -------------------------
    # Возврат домой
    # -------------------------
    elif command == "G28":
        data["type"] = "go_home"

    # -------------------------
    # Лазер ВКЛ / ВЫКЛ
    # -------------------------
    elif command == "M03":
        data["type"] = "laser"
        data["state"] = "on"
        data["power"] = next((float(part[1:]) for part in parts if part.startswith("S")), 1000.0)
    elif command == "M05":
        data["type"] = "laser"
        data["state"] = "off"
        data["power"] = 0

    else:
        data["type"] = "unknown"

    return data


def parse_gcode_file(filename):
    """Считывает файл и возвращает список команд в виде JSON-объектов."""
    commands = []
    with open(filename, "r", encoding="utf-8") as file:
        for line in file:
            cmd = parse_gcode_line(line)
            if cmd:
                commands.append(cmd)
    return commands


def save_to_json(data, output_file="laser_commands.json"):
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


if __name__ == "__main__":
    input_file = "gcode.txt"  # ← сюда помещаешь свой .txt с G-кодом
    parsed_data = parse_gcode_file(input_file)
    save_to_json(parsed_data)
    print("✅ Файл успешно обработан! Результат сохранён в laser_commands.json")
