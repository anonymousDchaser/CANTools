# widgets/theme.py
"""信号分组 / 信号检索两视图共用的深色主题样式表。

两视图独立为停靠窗后，各自需要自带深色 QSS（不再互相继承），
故集中在此处定义，保证主题完全一致。
"""
DARK_PANEL_QSS = """
    QWidget {
        background-color: #1e1e2e;
        color: #e0e0e0;
        font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
        font-size: 13px;
    }
    QLabel {
        color: #9090a0;
        font-weight: 500;
        padding: 0 4px;
    }
    QComboBox {
        background-color: #2a2a3e;
        color: #e0e0e0;
        border: 1px solid #3a3a4e;
        border-radius: 4px;
        padding: 6px 12px;
        min-height: 28px;
    }
    QComboBox:hover {
        border-color: #4fc3f7;
    }
    QComboBox::drop-down {
        border: none;
        width: 24px;
    }
    QComboBox QAbstractItemView {
        background-color: #252535;
        color: #e0e0e0;
        selection-background-color: #1e3a5a;
        selection-color: #4fc3f7;
        border: 1px solid #3a3a4e;
        outline: none;
    }
    QPushButton {
        background-color: #3a3a4e;
        color: #e0e0e0;
        border: 1px solid #4a4a5e;
        border-radius: 4px;
        padding: 6px 14px;
        min-height: 28px;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: #4a4a5e;
        border-color: #4fc3f7;
    }
    QPushButton:pressed {
        background-color: #2a2a3e;
    }
    QListWidget {
        background-color: #1e1e2e;
        alternate-background-color: #252535;
        color: #e0e0e0;
        border: 1px solid #3a3a4e;
        border-radius: 4px;
        padding: 4px;
        outline: none;
    }
    QListWidget::item {
        padding: 5px 8px;
    }
    QTreeWidget::item {
        min-height: 28px;
        padding: 7px 8px;
    }
    QListWidget::item:selected {
        background-color: #1e3a5a;
        color: #4fc3f7;
    }
    QListWidget::item:hover {
        background-color: #2a2a4e;
    }
    QCheckBox {
        background-color: transparent;
        color: #e0e0e0;
        spacing: 6px;
        font-size: 13px;
    }
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border: 1px solid #4a4a5e;
        border-radius: 3px;
        background-color: #2a2a3e;
    }
    QCheckBox::indicator:unchecked:hover {
        border-color: #4fc3f7;
    }
    QCheckBox::indicator:checked {
        background-color: #4fc3f7;
        border-color: #4fc3f7;
    }
"""
