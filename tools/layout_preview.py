# 临时可视化验证：假设备 + 真实 GUI，PIL 抓屏存 PNG（不碰真机串口）。
import os
import sys
import time
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import comm as comm_module
from fake_device import FakeSerialComm
import main as main_mod

out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "layout_preview")
os.makedirs(out_dir, exist_ok=True)

with mock.patch.object(comm_module, 'SerialComm', return_value=FakeSerialComm()),      mock.patch.object(comm_module.AmpUsb, 'calibrate'):
    app = main_mod.ElectroChemGUI()
app.geometry("1400x900+60+60")
app.attributes("-topmost", True)
app.update_idletasks()
app.update()

from utils import make_cv_x_line
from fake_device import synthesize_cv_voltammogram
x = make_cv_x_line(-400, 700, 5, 'Start', 3)
y = synthesize_cv_voltammogram(x)
app.cv.graph.update_data(x, y, label="CV 3 cycles (mock)")

from PIL import ImageGrab

for name, page in (("cv", app.cv), ("swv", app.swv),
                   ("asv", app.asv), ("amp", app.amp)):
    app.notebook.select(page)
    # 等布局/图尺寸稳定（<Configure> 防抖 100ms）再同步抓屏
    deadline = time.time() + 1.5
    while time.time() < deadline:
        app.update()
        time.sleep(0.05)
    path = os.path.join(out_dir, name + ".png")
    ImageGrab.grab().save(path)
    print("saved", path, flush=True)

app.destroy()
print("DONE", flush=True)
