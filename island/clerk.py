"""文员工具：金额大写、日期/工作日、批量重命名、文件夹清单、文本整理。

全部本地处理，不改文件内容；批量重命名执行前必须预览并二次确认。
"""
from __future__ import annotations

import csv
import os
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from PySide6.QtCore import QDate, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTabWidget, QToolTip, QVBoxLayout, QWidget,
)

from .panels import ACCENT, SUBTEXT, TEXT, PopupCard

DIGITS = "零壹贰叁肆伍陆柒捌玖"
SMALL_UNITS = ("", "拾", "佰", "仟")
BIG_UNITS = ("", "万", "亿", "万亿")


def _group_cn(n: int) -> str:
    out = ""
    zero = False
    for i in range(3, -1, -1):
        d = (n // (10 ** i)) % 10
        if d:
            if zero and out:
                out += "零"
            out += DIGITS[d] + SMALL_UNITS[i]
            zero = False
        elif out:
            zero = True
    return out


def rmb_upper(value: str | float | Decimal | int) -> str:
    """人民币金额转中文大写；最多到分，超过 9999 万亿直接报错。"""
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("，", "")
        cleaned = cleaned.replace("¥", "").replace("￥", "").replace("元", "")
        if not cleaned:
            raise ValueError("金额为空")
    else:
        cleaned = str(value)
    try:
        amount = Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("金额格式不对")
    negative = amount < 0
    amount = abs(amount)
    if amount >= Decimal("10000000000000000"):
        raise ValueError("金额太大")

    integer = int(amount)
    frac = int((amount - integer) * 100)
    if frac >= 100:
        integer += 1
        frac = 0

    groups = []
    n = integer
    while n:
        groups.append(n % 10000)
        n //= 10000
    if len(groups) > len(BIG_UNITS):
        raise ValueError("金额太大")

    out = ""
    zero_between = False
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        if g:
            if out and (zero_between or g < 1000):
                out += "零"
            out += _group_cn(g) + BIG_UNITS[idx]
            zero_between = False
        elif out:
            zero_between = True

    if not out:
        out = "零"
    out += "元"
    jiao, fen = divmod(frac, 10)
    if fen:
        if jiao:
            out += DIGITS[jiao] + "角"
        else:
            out += "零"
        out += DIGITS[fen] + "分"
    elif jiao:
        out += DIGITS[jiao] + "角整"
    else:
        out += "整"
    return ("负" if negative else "") + out


def workdays_between(start: date, end: date) -> int:
    """不含起始日、包含结束日的周一至周五天数。"""
    if end <= start:
        return 0
    days = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days


def add_workdays(start: date, n: int) -> date:
    cur = start
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        cur += timedelta(days=step)
        if cur.weekday() < 5:
            left -= 1
    return cur


def inventory_csv(folder: str, recursive: bool, out_path: str, limit: int = 5000) -> int:
    """导出文件夹清单；返回写入条数。目录本身也列一行。"""
    rows = []
    base = os.path.abspath(folder)
    for root, dirs, files in os.walk(base):
        subdirs = sorted(dirs)
        if not recursive:
            dirs[:] = []
        for name in subdirs:
            p = os.path.join(root, name)
            rows.append((os.path.relpath(p, base), name, "文件夹", "", "", ""))
            if len(rows) >= limit:
                break
        for name in sorted(files):
            p = os.path.join(root, name)
            try:
                st = os.stat(p)
                size = "%.1f" % (st.st_size / 1024)
                mtime = __import__("time").strftime("%Y-%m-%d %H:%M", __import__("time").localtime(st.st_mtime))
            except OSError:
                size, mtime = "", ""
            rows.append((os.path.relpath(p, base), name,
                         "文件", Path(name).suffix.lower(), size, mtime))
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(("相对路径", "名称", "类型", "扩展名", "大小(KB)", "修改时间"))
        writer.writerows(rows)
    return len(rows)


def rename_plan(folder: str, prefix: str, start: int) -> list[tuple[str, str]]:
    """生成 (旧路径, 新路径) 列表；只处理当前目录下的文件。"""
    prefix = re.sub(r'[\\/:*?"<>|]', "_", (prefix or "文件")).strip() or "文件"
    files = sorted((p for p in Path(folder).iterdir() if p.is_file()),
                   key=lambda p: p.name.lower())
    plan = []
    for i, p in enumerate(files):
        target = p.with_name("%s_%03d%s" % (prefix, start + i, p.suffix))
        plan.append((str(p), str(target)))
    return plan


def clean_text(text: str, drop_blank: bool, trim: bool,
               dedupe: bool, number: bool) -> str:
    lines = text.splitlines()
    out = []
    seen = set()
    for line in lines:
        s = line.strip() if trim else line
        if drop_blank and not s.strip():
            continue
        if dedupe:
            key = s.strip()
            if key in seen:
                continue
            seen.add(key)
        out.append(s.rstrip() if trim else s)
    if number:
        width = max(2, len(str(len(out))))
        out = [("%0*d. %s" % (width, i + 1, s)) for i, s in enumerate(out)]
    return "\n".join(out)


class ClerkPanel(PopupCard):
    WIDTH = 520

    def __init__(self) -> None:
        super().__init__(height=510)
        self.setFocusPolicy(Qt.StrongFocus)
        self._close_rect = QRect(self.width() - 38, 12, 24, 24)
        self._hot_close = False

        self.setStyleSheet(f"""
            QWidget {{ color:{TEXT}; font-size:12px; }}
            QTabWidget::pane {{ border:none; background:transparent; }}
            QTabBar::tab {{ background:#2c2c2e; color:{SUBTEXT}; padding:6px 12px;
                margin-right:4px; border-radius:8px; }}
            QTabBar::tab:selected {{ background:#3a3a3c; color:#ffffff; }}
            QLineEdit, QSpinBox, QDateEdit, QComboBox, QPlainTextEdit {{
                background:#141416; color:{TEXT}; border:1px solid #2c2c2e;
                border-radius:8px; padding:5px; }}
            QLineEdit:focus, QSpinBox:focus, QDateEdit:focus, QComboBox:focus,
            QPlainTextEdit:focus {{ border-color:{ACCENT}; }}
            QPushButton {{ background:#2c2c2e; color:#d1d1d6; border:none;
                border-radius:8px; padding:6px 12px; }}
            QPushButton:hover {{ background:#3a3a3c; color:#ffffff; }}
            QPushButton#primary {{ background:{ACCENT}; color:#ffffff; font-weight:600; }}
            QPushButton#primary:hover {{ background:#3a9bff; }}
            QLabel {{ background:transparent; }}
            QLabel#hint {{ color:{SUBTEXT}; font-size:10px; }}
            QLabel#result {{ color:#ffd08a; font-size:13px; font-weight:600; }}
            QCheckBox {{ color:#d1d1d6; }}
        """)

        self.tabs = QTabWidget(self)
        self.tabs.setGeometry(12, 46, self.width() - 24, self.height() - 58)
        self.tabs.addTab(self._tab_money(), "金额")
        self.tabs.addTab(self._tab_date(), "日期")
        self.tabs.addTab(self._tab_rename(), "重命名")
        self.tabs.addTab(self._tab_inventory(), "清单")
        self.tabs.addTab(self._tab_text(), "文本")

    # ---------- 金额 ----------
    def _tab_money(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 10, 4, 4)
        v.setSpacing(8)
        tip = QLabel("输入 0 ~ 9999 万亿，最多两位小数；支持逗号、￥ 和“元”。")
        tip.setObjectName("hint")
        v.addWidget(tip)

        self.money_in = QLineEdit()
        self.money_in.setPlaceholderText("例如 12800.50")
        self.money_in.returnPressed.connect(self._money_convert)
        v.addWidget(self.money_in)

        row = QHBoxLayout()
        btn = QPushButton("转换")
        btn.setObjectName("primary")
        btn.clicked.connect(self._money_convert)
        row.addWidget(btn)
        copy = QPushButton("复制大写")
        copy.clicked.connect(self._money_copy)
        row.addWidget(copy)
        row.addStretch(1)
        v.addLayout(row)

        self.money_out = QLineEdit()
        self.money_out.setReadOnly(True)
        self.money_out.setPlaceholderText("大写结果")
        v.addWidget(self.money_out)
        self.money_status = QLabel("")
        self.money_status.setObjectName("hint")
        v.addWidget(self.money_status)
        v.addStretch(1)
        return w

    def _money_convert(self) -> None:
        try:
            out = rmb_upper(self.money_in.text())
        except ValueError as e:
            self.money_out.clear()
            self.money_status.setText(str(e))
            self.money_status.setStyleSheet("color:#ff453a;")
            return
        self.money_out.setText(out)
        self.money_status.setStyleSheet("color:#30d158;")
        self.money_status.setText("已转换，可复制")

    def _money_copy(self) -> None:
        text = self.money_out.text()
        if text:
            self._copy(text, "金额大写")

    # ---------- 日期 ----------
    def _tab_date(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 10, 4, 4)
        v.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel("起始日期"), 0, 0)
        self.d_start = QDateEdit(QDate.currentDate())
        self.d_start.setCalendarPopup(True)
        self.d_start.setDisplayFormat("yyyy-MM-dd")
        grid.addWidget(self.d_start, 0, 1)
        grid.addWidget(QLabel("结束日期"), 0, 2)
        self.d_end = QDateEdit(QDate.currentDate())
        self.d_end.setCalendarPopup(True)
        self.d_end.setDisplayFormat("yyyy-MM-dd")
        grid.addWidget(self.d_end, 0, 3)

        grid.addWidget(QLabel("方式"), 1, 0)
        self.d_mode = QComboBox()
        self.d_mode.addItems(["两日期相差（自然日）", "两日期相差（工作日）",
                              "起始日加自然日", "起始日加工作日"])
        grid.addWidget(self.d_mode, 1, 1, 1, 2)
        self.d_days = QSpinBox()
        self.d_days.setRange(-3650, 3650)
        self.d_days.setValue(30)
        grid.addWidget(self.d_days, 1, 3)
        v.addLayout(grid)

        tip = QLabel("工作日只跳过周六、周日，不含法定节假日和调休。")
        tip.setObjectName("hint")
        v.addWidget(tip)

        row = QHBoxLayout()
        calc = QPushButton("计算")
        calc.setObjectName("primary")
        calc.clicked.connect(self._date_calc)
        row.addWidget(calc)
        copy = QPushButton("复制结果")
        copy.clicked.connect(lambda: self._copy(self.d_result.text(), "日期结果"))
        row.addWidget(copy)
        row.addStretch(1)
        v.addLayout(row)

        self.d_result = QLabel("")
        self.d_result.setObjectName("result")
        self.d_result.setWordWrap(True)
        self.d_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.d_result)
        v.addStretch(1)
        self.d_mode.currentIndexChanged.connect(self._date_mode_changed)
        self._date_mode_changed()
        return w

    def _date_mode_changed(self) -> None:
        idx = self.d_mode.currentIndex()
        self.d_end.setEnabled(idx <= 1)
        self.d_days.setEnabled(idx >= 2)

    def _date_calc(self) -> None:
        start = self.d_start.date().toPython()
        end = self.d_end.date().toPython()
        n = self.d_days.value()
        idx = self.d_mode.currentIndex()
        if idx == 0:
            self.d_result.setText("相差 %d 个自然日" % max(0, (end - start).days))
        elif idx == 1:
            self.d_result.setText("相差 %d 个工作日" % workdays_between(start, end))
        elif idx == 2:
            self.d_result.setText(str(start + timedelta(days=n)))
        else:
            self.d_result.setText(str(add_workdays(start, n)))

    # ---------- 重命名 ----------
    def _tab_rename(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 4)
        v.setSpacing(6)

        row = QHBoxLayout()
        self.r_folder = QLineEdit()
        self.r_folder.setPlaceholderText("要重命名的文件夹")
        row.addWidget(self.r_folder, 1)
        browse = QPushButton("选择…")
        browse.clicked.connect(lambda: self._pick_folder(self.r_folder))
        row.addWidget(browse)
        v.addLayout(row)

        row2 = QHBoxLayout()
        self.r_prefix = QLineEdit()
        self.r_prefix.setPlaceholderText("文件名前缀")
        row2.addWidget(self.r_prefix, 1)
        row2.addWidget(QLabel("起始号"))
        self.r_start = QSpinBox()
        self.r_start.setRange(1, 9999)
        self.r_start.setValue(1)
        row2.addWidget(self.r_start)
        v.addLayout(row2)

        self.r_preview = QPlainTextEdit()
        self.r_preview.setReadOnly(True)
        self.r_preview.setPlaceholderText("点“预览”查看改名结果；只改当前文件夹里的文件。")
        v.addWidget(self.r_preview, 1)

        row3 = QHBoxLayout()
        prev = QPushButton("预览")
        prev.clicked.connect(self._rename_preview)
        row3.addWidget(prev)
        run = QPushButton("执行重命名")
        run.setObjectName("primary")
        run.clicked.connect(self._rename_run)
        row3.addWidget(run)
        row3.addStretch(1)
        v.addLayout(row3)
        self.r_status = QLabel("执行前会二次确认；重命名不可撤销，先预览。")
        self.r_status.setObjectName("hint")
        v.addWidget(self.r_status)
        return w

    def _rename_preview(self) -> bool:
        folder = self.r_folder.text().strip()
        if not os.path.isdir(folder):
            self.r_status.setText("文件夹不存在")
            self.r_status.setStyleSheet("color:#ff453a;")
            return False
        plan = rename_plan(folder, self.r_prefix.text().strip(), self.r_start.value())
        self._rename_plan = plan
        lines = ["%s  →  %s" % (Path(a).name, Path(b).name) for a, b in plan]
        self.r_preview.setPlainText("\n".join(lines[:300]))
        self.r_status.setStyleSheet("color:#30d158;")
        self.r_status.setText("共 %d 个文件；确认无误再执行" % len(plan))
        return True

    def _rename_run(self) -> None:
        if not self._rename_preview():
            return
        plan = getattr(self, "_rename_plan", [])
        if not plan:
            return
        targets = [b for _a, b in plan]
        if len(set(targets)) != len(targets) or any(os.path.exists(t) for t in targets):
            self.r_status.setStyleSheet("color:#ff453a;")
            self.r_status.setText("目标文件名有冲突或已存在，已中止")
            return
        self.auto_dismiss = False
        try:
            answer = QMessageBox.question(
                self, "确认重命名",
                "将重命名 %d 个文件，且不可撤销。继续？" % len(plan),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        finally:
            self.auto_dismiss = True
        if answer != QMessageBox.Yes:
            return
        errors = 0
        for old, new in plan:
            try:
                os.rename(old, new)
            except OSError:
                errors += 1
        self.r_status.setStyleSheet("color:#ff9f0a;" if errors else "color:#30d158;")
        self.r_status.setText("完成，失败 %d 个" % errors if errors else "重命名完成")
        self._rename_preview()

    # ---------- 清单 ----------
    def _tab_inventory(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 10, 4, 4)
        v.setSpacing(8)

        row = QHBoxLayout()
        self.i_folder = QLineEdit()
        self.i_folder.setPlaceholderText("要导出清单的文件夹")
        row.addWidget(self.i_folder, 1)
        browse = QPushButton("选择…")
        browse.clicked.connect(lambda: self._pick_folder(self.i_folder))
        row.addWidget(browse)
        v.addLayout(row)

        row2 = QHBoxLayout()
        self.i_recursive = QCheckBox("包含子文件夹")
        self.i_recursive.setChecked(True)
        row2.addWidget(self.i_recursive)
        row2.addStretch(1)
        v.addLayout(row2)

        out_row = QHBoxLayout()
        self.i_out = QLineEdit()
        self.i_out.setText(str(Path.home() / "Desktop" /
                               ("文件清单_%s.csv" % __import__("time").strftime("%Y%m%d_%H%M"))))
        out_row.addWidget(self.i_out, 1)
        out_browse = QPushButton("另存为…")
        out_browse.clicked.connect(self._pick_csv)
        out_row.addWidget(out_browse)
        v.addLayout(out_row)

        run = QPushButton("导出 CSV")
        run.setObjectName("primary")
        run.clicked.connect(self._inventory_run)
        v.addWidget(run)
        self.i_status = QLabel("导出文件清单，Excel 可直接打开；最多 5000 条。")
        self.i_status.setObjectName("hint")
        self.i_status.setWordWrap(True)
        v.addWidget(self.i_status)
        v.addStretch(1)
        return w

    def _inventory_run(self) -> None:
        folder = self.i_folder.text().strip()
        out = self.i_out.text().strip()
        if not os.path.isdir(folder):
            self.i_status.setText("文件夹不存在")
            self.i_status.setStyleSheet("color:#ff453a;")
            return
        if not out:
            self.i_status.setText("先选输出文件")
            return
        try:
            count = inventory_csv(folder, self.i_recursive.isChecked(), out)
        except Exception as e:
            self.i_status.setStyleSheet("color:#ff453a;")
            self.i_status.setText("导出失败：%s" % e)
            return
        self.i_status.setStyleSheet("color:#30d158;")
        self.i_status.setText("已导出 %d 条：%s" % (count, out))

    def _pick_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存清单", self.i_out.text(),
                                              "CSV 文件 (*.csv)")
        if path:
            self.i_out.setText(path)

    # ---------- 文本 ----------
    def _tab_text(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 4)
        v.setSpacing(6)
        self.t_in = QPlainTextEdit()
        self.t_in.setPlaceholderText("粘贴从 PDF、Excel、网页复制来的文本")
        v.addWidget(self.t_in, 1)

        row = QHBoxLayout()
        self.t_blank = QCheckBox("去空行")
        self.t_trim = QCheckBox("去首尾空格")
        self.t_dedupe = QCheckBox("去重复行")
        self.t_number = QCheckBox("加行号")
        for cb in (self.t_blank, self.t_trim, self.t_dedupe, self.t_number):
            cb.setChecked(cb is self.t_trim)
            row.addWidget(cb)
        row.addStretch(1)
        v.addLayout(row)

        row2 = QHBoxLayout()
        run = QPushButton("整理")
        run.setObjectName("primary")
        run.clicked.connect(self._text_run)
        row2.addWidget(run)
        copy = QPushButton("复制结果")
        copy.clicked.connect(lambda: self._copy(self.t_out.toPlainText(), "整理结果"))
        row2.addWidget(copy)
        row2.addStretch(1)
        v.addLayout(row2)

        self.t_out = QPlainTextEdit()
        self.t_out.setReadOnly(True)
        self.t_out.setPlaceholderText("整理后的文本")
        v.addWidget(self.t_out, 1)
        self.t_status = QLabel("")
        self.t_status.setObjectName("hint")
        v.addWidget(self.t_status)
        return w

    def _text_run(self) -> None:
        out = clean_text(self.t_in.toPlainText(), self.t_blank.isChecked(),
                         self.t_trim.isChecked(), self.t_dedupe.isChecked(),
                         self.t_number.isChecked())
        self.t_out.setPlainText(out)
        self.t_status.setStyleSheet("color:#30d158;")
        self.t_status.setText("已整理 %d 行" % len(out.splitlines()))

    # ---------- 共用 ----------
    def _pick_folder(self, target: QLineEdit) -> None:
        self.auto_dismiss = False
        try:
            path = QFileDialog.getExistingDirectory(self, "选择文件夹",
                                                    target.text().strip() or str(Path.home()))
        finally:
            self.auto_dismiss = True
        if path:
            target.setText(path)

    def _copy(self, text: str, what: str) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        QToolTip.showText(QCursor.pos(), "%s已复制" % what, self)

    def mouseMoveEvent(self, event) -> None:
        hot = self._close_rect.contains(event.position().toPoint())
        if hot != self._hot_close:
            self._hot_close = hot
            self.setCursor(Qt.PointingHandCursor if hot else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hot_close = False
        self.setCursor(Qt.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.LeftButton
                and self._close_rect.contains(event.position().toPoint())):
            self.close_panel()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        p.drawText(QRect(16, 8, 240, 28), Qt.AlignLeft | Qt.AlignVCenter, "文员工具")
        p.setFont(self._font(11))
        p.setPen(QColor("#ffffff" if self._hot_close else SUBTEXT))
        p.drawText(self._close_rect, Qt.AlignCenter, "✕")
        p.end()

    def _font(self, px: int) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(px)
        return f
