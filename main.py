import sys
from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QSpacerItem, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


class VisualizationWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Laser App - Визуализация")
        self.setGeometry(200, 200, 800, 600)

        # Однотонный серый фон
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(200, 200, 200))  # светло-серый
        self.setAutoFillBackground(True)
        self.setPalette(palette)

        # Надпись сверху по центру
        label = QLabel("Процесс визуализации", self)
        label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        label.setStyleSheet("""
            font-size: 28px;
            font-weight: bold;
            color: #333;
            margin-top: 20px;
        """)

        # Расположение элементов
        layout = QVBoxLayout()
        layout.addWidget(label)
        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))
        self.setLayout(layout)


class MainMenu(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Laser App - Главное меню")
        self.setGeometry(200, 200, 500, 300)

        # Кнопки
        self.play_button = QPushButton("Играть", self)
        self.exit_button = QPushButton("Выход", self)

        # Стиль кнопок
        button_style = """
            QPushButton {
                background-color: #444;
                color: white;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #666;
            }
        """
        self.play_button.setStyleSheet(button_style)
        self.exit_button.setStyleSheet(button_style)

        # События
        self.play_button.clicked.connect(self.play_action)
        self.exit_button.clicked.connect(self.exit_action)

        # Расположение кнопок
        layout = QVBoxLayout()
        layout.addWidget(self.play_button, alignment=Qt.AlignCenter)
        layout.addWidget(self.exit_button, alignment=Qt.AlignCenter)
        self.setLayout(layout)

    def play_action(self):
        print("Открываю окно визуализации...")
        self.close()
        self.visual_window = VisualizationWindow()
        self.visual_window.show()

    def exit_action(self):
        print("Выход из программы.")
        self.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainMenu()
    window.show()
    sys.exit(app.exec())
