# 离线测试 B：无硬件路径 + 全量回归（2026-10-13）

- 执行：测试子智能体 B（ZCode 自动化）
- 环境：Windows 10 x64（build 26300）/ Python 3.13.5 / Tcl-Tk / matplotlib / ttkbootstrap
- 硬件：**全程未连接**。`main.AmpUsb` 一律用 `mock.patch('main.AmpUsb')` 替换且 `connected=False`、`found=False`（与 `tests/test_refactored.py::TestRunButtonWiring.setUpClass` 同款写法），**从未打开任何真实串口**。
- 弹窗：所有 `tkinter.messagebox` 弹窗被录制器替换（防模态阻塞，同时留证弹窗内容）。
- 隔离：测试前备份 `settings.txt`/`settings_asv.txt`（md5 ddf041e0…/58881e54…）；测试允许写设置；结束后已**逐字节恢复**（md5 与备份一致），测试新建的 `settings_amp.txt` 已删除。
- 每个测试脚本独立进程运行，互不干扰；全程捕获 stdout/stderr。

## 一、结果一览

| # | 项目 | 结果 | 断言数 |
|---|------|------|--------|
| 1 | 无设备启动完整 GUI：六页切换 / 状态栏未连接 / 无未捕获异常 | **PASS** | 3/3 |
| 2 | CV/LS/SWV/DPV 运行按钮未连接仍发 'R'，"停止"态后恢复 | **PASS** | 4/4 |
| 3 | 静置 quiet_time_s 3→5 持久化，再改回 3 恢复 | **PASS** | 4/4 |
| 4 | 离线打开数据：verify_multicycle / ASV / AMP / CV CSV 路由+画图+删除当前页 | **PASS** | 6/6 |
| 5 | 方法文件：保存 json → 打乱参数 → 加载恢复 | **PASS** | 2/2 |
| 6 | 平滑 / 基线扣除 / 自动寻峰 / 导出峰 CSV / 还原 | **PASS** | 7/7 |
| 7 | 全量回归 `python tests/run_tests.py` | **PASS**（80/80，"通过：环境诊断与全部测试均正常"，exit 0） | — |
| 8 | stderr 噪音行（bgerror / TclError） | **0 行** | — |

合计功能断言 26/26 通过；**未发现功能性新问题**（3 条非功能性观察见第四节）。

## 二、逐项明细

### 测试 1：无设备启动完整 GUI — PASS
- `ElectroChemGUI()` 在 AmpUsb 被替换为 `connected=False` 下完整构建（CV/LS/SWV/DPV/AMP/ASV 六页 + 状态栏），无异常。
- 依次 `notebook.select()` 六页，`nametowidget(notebook.select())` 均回到对应 frame。
- 状态栏按钮文本 `● 未找到设备`（get_text("status_not_found")，bootstyle=danger）——未连接语义正确。

### 测试 2：CV/LS/SWV/DPV 运行按钮（未连接接线语义）— PASS
对每页点击真实 `run_button.invoke()`，在 `usb_write('R')` 发出瞬间采样按钮状态：

| 页 | 发出的指令序列 | 发 'R' 瞬间 | 结束后 |
|----|----------------|-------------|--------|
| CV | `S|2848|1448|02399|CS` → `R` | 文本="停止"，is_running=True | 恢复"运行 CV 扫描"，is_running=False |
| LS | `S|2848|1448|02399|LS` → `R` | 同上 | 恢复"运行 LS 扫描" |
| SWV | `G|2848|1448|0005|0100|23999|LS` → `R` | 同上 | 恢复"运行 SWV 扫描" |
| DPV | `P|2848|1448|0005|0100|23999|LS` → `R` | 同上 | 恢复"运行 DPV 扫描" |

四页均：启动指令 `'R'` 必发；按钮进入"停止"态后立即走未连接恢复分支（`_exit_scan_state`），界面可再次运行。

### 测试 3：静置设置持久化 — PASS
真实 UI 路径：CV 页右侧"更改设置"按钮 → 弹出 `CVSettingChanges`（标题"更改循环伏安法设置"）→ "扫描前静置 (秒)" Spinbox 由 3 改 5 → 点"保存更改"：
- `settings.txt` 出现 `quiet_time_s = 5.0`，内存 `cv_settings.quiet_time_s == 5.0`，窗口自动关闭。
- 再次打开设置窗口改回 3 并保存：`settings.txt` 回到 `quiet_time_s = 3.0`。
- （结束时另从备份整体恢复，保证逐字节原样。）

### 测试 4：数据离线链路 — PASS
经"文件→打开数据"同款代码路径（`app.open_data()`，文件对话框替换为固定路径）：
- `autosave_backup/20261013_185728_AMP.csv` → 自动跳到**计时电流页**，200 个采样点画出图线；"删除当前页数据"后图线移除、`time/data` 清空。
- `autosave_backup/20261013_185727_ASV.csv` → 自动跳到 **ASV 页**并画 1 条曲线；经真实"勾选删除"弹窗（勾选曲线→"是"按钮）删除后曲线数=0、`data.index=0`。
- `autosave_backup/20261013_185706_CV.csv` → 自动跳到 **CV 页**并画图。
- `实验记录/verify_multicycle_20261013_111652.csv` → 弹"打开失败"警告、页面不跳转（见观察 1）。

### 测试 5：方法文件 — PASS
- `save_method_file()` 存出临时目录 json，含 method 标识与 cv/asv/amp 全套键。
- 打乱：start/end 电压、扫速、圈数、swv_height、ASV plate_time/sweep_type/rest_time、AMP voltage。
- `load_method_file()` 后 9 项参数全部恢复原值（不一致项=无），各页参数显示/横轴刷新无异常。

### 测试 6：数据分析工具（离线）— PASS
以 `autosave_backup/20261013_185727_ASV.csv`（1901 点）为对象：
- **平滑**：Savitzky-Golay 后曲线变化（max|Δy|=0.267 µA），自动备份原始数据。
- **基线扣除**：max|y| 12.80 → 9.82 µA。
- **自动寻峰**：弹窗报告"峰1: 峰位 −254.0 mV，峰高 9.80 µA，半峰宽 178.7 mV，电量 9.01 µC；峰2: −269.0 mV …"，图上完成标注。
- **导出峰结果 CSV**：临时目录 `peaks_export.csv` 生成，表头 + 2 条峰行（含峰位/峰高/半峰宽/面积/电量）。
- **还原**：曲线逐点回到载入时原始值。
- CV 页（401 点多圈 CV）平滑+寻峰冒烟无异常（该数据报"未检测到峰"，属数据形态正常结论）。

### 测试 7：全量回归 — PASS
`python tests/run_tests.py`（2026-10-13 19:24–19:29，每个测试类独立子进程）：
- 环境诊断通过；**80/80 类全部 OK**，输出"通过：环境诊断与全部测试均正常"，返回码 0。
- `test_report.txt` 已由本次运行刷新（含本次 80/80 结果）。无需单独重跑任何类。

### 测试 8：stderr 噪音统计 — 0 行
6 个 GUI 测试脚本 + 全量回归的 stderr 全部捕获：**合计 0 字节，bgerror/TclError/Traceback 均为 0 行**。

## 三、清理证明
- `settings.txt`、`settings_asv.txt` 已从备份恢复，md5 与测试前一致（ddf041e0… / 58881e54…）。
- 测试产生的 `settings_amp.txt` 已删除；无 `_gui_ready.flag`、`startup_error.txt` 等遗留。
- 所有 tk 窗口按 `_safe_destroy` 流程销毁；临时目录（测试脚本、method_test.json、peaks_export.csv、捕获日志）已删除，仅保留本报告。

## 四、观察项（非功能性，不修码，仅记录）
1. **`实验记录/verify_multicycle_*.csv` 不能用"文件→打开数据"载入**：该文件是 `tests/verify_multicycle_cv.py` 的输出，表头首列为 `point`（本程序保存格式为 `voltage`/`time`），`load_csv_file_full` 按设计拒绝并弹"打开失败"警告。复看多圈结果请直接看同目录 `verify_multicycle_20261013_111652.png`，或用 `tests/verify_patched_multicycle.py` 复检。属预期行为，但若希望验证产物也能回灌 GUI，可让验证脚本按 GUI 保存格式写表头。
2. **方法文件"保存→加载"会把 settings.txt 的 `sweep_type/use_swv/delay_time` 重写为 CV 页值**（实测 LS→CV、True→False、56000→14000），ASV 数值行 ".0" 小数格式变整数。根因：`ASVFrame` 经 `CVFrame.__init__`（mode 缺省 'CV'）构造，`_apply_mode()` 在**应用启动末尾**把共享 `cv_settings.sweep_type/use_swv` 置为 `('CV', False)`，`save_method_file` 保存的正是这份共享缓存。功能无影响——每个方法页运行前都强制自己的技术（`_apply_mode`/`run_scan` 的 `MODE_SETTINGS`），SWV/DPV 的发参、估时也按页面模式自算；仅持久化文件里的"上次技术"缓存值会变。
3. **settings.txt 的 `start_dac_value` 缓存行在测试前就是陈旧值**：原文件 1648 对应 start=−400 mV 时代的计算（现值 −800 应为 1248），`end_dac_value` 一致。任何一次 GUI 保存设置都会把它刷新为当前计算值——本次测试触发了该刷新。属历史遗留的缓存陈旧，非本次引入；已按原样恢复。
