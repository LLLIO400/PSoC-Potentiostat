# 离线测试 A:脱机模拟模式全功能测试(2026-10-13)

- 执行者:测试子智能体 A(ZCode)
- 结论:**15/15 项全部 PASS,未发现功能性新问题**
- 硬件接触:**零**——全程未打开任何真实 COM 口;构造 GUI 时给 `comm.SerialComm` 挂了"禁止桩"(一旦被调用立即抛 AssertionError),设备一律为 `tests/fake_device.py` 的 `FakeSerialComm`(经 `main._make_device()` 的 simulate_mode 内置模拟器路径注入)。

## 一、结论一览

| # | 测试项 | 结果 | 关键证据 |
|---|--------|------|----------|
| 1 | GUI 构造(模拟模式),6 页全建 | PASS | 页签 6 个齐全;`app.device.device` 类型 = FakeSerialComm;连接按钮显示「● 模拟模式」;构建 2.6 s,禁止桩未触发 |
| 2 | CV / LS / SWV / DPV / ASV / 计时电流 逐页扫描 | PASS(6 小项) | 见下表;每页 x/y 等长、autosave 生成、按钮文字恢复、进度窗关闭 |
| 3 | CV 多圈 2 圈(双路径) | PASS | 固件零间隙与 GUI 逐圈两条路径都跑通,各出 4801 点 ≈ 2×2401 |
| 4 | 扫描中点停止 | PASS | fake 收到 'X';按钮恢复;迟到回调不画图 |
| 5 | 双语 zh↔en / 主题 Cosmo↔Darkly | PASS(2 小项) | 页签/按钮文字随语言变化并还原;darkly 生效且切回 cosmo;全程无异常 |
| 6 | 打开数据自动路由 + 另存临时目录 | PASS(2 小项) | 6 份 autosave 全部路由跳转到对应页并画图;CV/AMP 另存成功(含 AMP 元数据头) |
| 7 | stderr 噪音(bgerror/TclError)统计 | 0 行 | 全程无 bgerror、无 TclError、无意外 messagebox 弹窗 |

### 第 2 项逐页明细(第 3 轮完整运行,19:38–19:39)

| 页面 | 数据点数(x=y) | autosave 产物 | 按钮恢复 | 备注 |
|------|----------------|---------------|----------|------|
| CV(1 圈) | 2401 | `20261013_193854_CV.csv` | 文字+命令均恢复 | −600→600 mV,2.0 V/s,triangle |
| LS | 1201 | `20261013_193855_LS.csv` | 同上 | |
| SWV | 61 | `20261013_193857_SWV.csv` | 同上 | inc=20 mV,period=20 ms |
| DPV | 61 | `20261013_193859_DPV.csv` | 同上 | 'P' 真 DPV 命令路径 |
| ASV(SWV 溶出) | 191 | `20261013_193909_ASV.csv` | 文字恢复;命令改接 asv_run 闭包(功能等价) | 清洗 2 s→富集 2 s→静置 1 s→溶出;搅拌 on/off、's'/'d' 切换、'R'、'E0' 全部按序出现在 fake 命令日志 |
| 计时电流 5 s | 15200(5 秒时 15200,流式实时画图) | `20261013_193914_AMP.csv` | 「开始计时电流」恢复 | time/data 等长;`_AMP.csv` 自动存档正常(此前唯一没有自动存档的页面) |

## 二、测试环境与方法

- Windows 10 x64,Python 3.13.5(Anaconda),项目根 `C:\Users\YZJ\Desktop\PSoC-Potentiostat-YI-1.0_GUI`。
- 方式:自编无人值守测试脚本(临时目录,结束后已删除)构造真实 `main.ElectroChemGUI` 窗口(`withdraw()`),全部通过按钮 `.invoke()` 与 `root.update()` 事件泵驱动,与用户操作等价。
- 数据断言:对每页 `graph.update_data` / `graph.update_amp_data` 包 spy 捕获画图入参,断言 x/y 等长且 >10 点;autosave 以"运行前后目录差分 + 文件名后缀"匹配。
- 弹窗防呆:messagebox 全模块替换为记录桩(无人值守不允许阻塞弹窗)。全程 **0 个弹窗**——扫描完成/停止/超时各路径均未触发任何 messagebox。

### 隔离与安全措施

1. **禁止真实串口**:`mock.patch comm.SerialComm` 及 `auto_find_com_port` 为抛错桩;启动前预检 `simulate_mode is True`,否则拒绝启动。
2. **settings 隔离**:开工前备份 `settings.txt`/`settings_asv.txt`(逐字节);测试期间只改这两个文件,结束时 `cmp` 逐字节还原(md5 回到原始 `ddf041e0…`/`58881e54…`,与另一备份源交叉验证一致)。
   - 测试用临时值:`settings.txt` → `simulate_mode=True`、`start_voltage=-600`、`sweep_rate=2.0`、`swv_inc=20`、`swv_period=20`、`use_swv=False`、`quiet_time_s=0.0`;`settings_asv.txt` → `plate_time=2.0`、`rest_time=1.0`、`pulse_inc=10`、`pulse_width=20`(清洗 2 s 保持)。
3. **并行智能体协调**:测试准备期间检测到另一测试智能体(B)正在操作同一对 settings 文件(19:11–19:31)。本测试等待其 python 进程全部退出、并确认 settings 已被其恢复为原始值之后才开工,避免互相覆盖。
4. **新增文件清理**:语言切换测试会生成 `settings_app.txt`(原本不存在)——已删除;临时目录(脚本/备份/另存 csv)已删除;autosave 产物与报告按要求保留;无残留 python/tk 进程。

## 三、各项详情与复现步骤

### 1. GUI 构造(模拟模式)— PASS
复现:`settings.txt` 写 `simulate_mode = True` → `ElectroChemGUI()`(SerialComm 已挂禁止桩)→ `withdraw()`。
结果:CV/LS/SWV/DPV/Amp/ASV 六页全部建出,notebook 6 页签,设备为内置模拟器,状态栏「● 模拟模式」。日志出现 `simulate mode: using built-in simulator`。

### 2. 逐页扫描 — PASS(明细见上表)
复现:每页点「运行」按钮(`.invoke()`),`root.update()` 泵事件直到完成;断言曲线入参、autosave、按钮状态。
- CV 页 1 圈数据 2401 点 = 理论值 2×1200+1 ✓
- LS 1201、SWV/DPV 61(1200/20+1)、ASV 191(1900/10+1)全部与参数吻合 ✓
- 每页扫描结束后 `timer_window=None`(合一进度窗正常关闭)、`is_running=False` ✓

### 3. CV 多圈 2 圈(记录所走路径)— PASS
将圈数设 2(内存)后跑两轮,分别记录路径(日志为证):
- **(a) 软件逐圈路径**(把 FakeSerialComm 的 `support_multi_cycle` 关掉模拟旧固件):日志 `lut length probe: got 2402, expect N-cycle 4802 / 1-cycle 2402` + `fw multi-cycle unsupported, using gui loop: 2 cycles`;出图 **4801 点**,等长 ✓
- **(b) 固件零间隙路径**(主记录,模拟当前已刷的多圈固件):日志 `lut length probe: got 4802, expect N-cycle 4802 / 1-cycle 2402`,无 gui loop 标记;出图 **4801 点** ✓ ≈ 2×2401−1(段间端点去重,理论值)
- 两条路径均符合 `max_cv_cycles(-600,600)=2` 的容量预期(容量内走固件 LUT)。

### 4. 扫描中点停止 — PASS
复现:LS 页点运行 → 运行中再点同一按钮(此刻文字已是「停止」)。
结果:fake 收到 `'X'`(复位)✓;`is_running=False`;按钮文字恢复「运行 LS 扫描」✓;随后手动注入迟到回调 `run_scan_continue()` 与 `_start_lut_scan()` 均直接返回,曲线数量不变(不画图)✓。

### 5. 双语与主题 — PASS
- `set_language_and_refresh(app,'en')`:页签 `循环伏安法 (CV)`→`Cyclic Voltammetry`,运行按钮 `运行 CV 扫描`→`Run CV Scan`;切回 zh 完整还原,无异常。
- `set_theme('darkly')`:`style.theme_use()='darkly'`、图区 `_dark=True`;切回 `cosmo` 还原。全程无异常。

### 6. 打开数据路由 + 另存 — PASS
- 用第 2 步生成的 6 份 autosave CSV 依次走 文件→打开数据(桩掉对话框返回路径):CV/LS/SWV/DPV/ASV/AMP **全部自动跳转到对应页并画出曲线**(按 `# experiment:` 元数据路由)。
- 另存:当前页保存 CV 数据(113.6 KB,`voltage` 表头 ✓)与 AMP 数据(`# experiment: AMP` 元数据 ✓)到临时目录成功。

### 7. stderr 噪音 — 0 行
全程捕获 stderr,`bgerror` / `TclError` / `invalid command name` 命中 **0 行**;无"application has been destroyed"类销毁期噪音。

## 四、发现的问题与观察项

**未发现功能性新问题。** 以下为非阻塞观察项(仅记录,不建议当缺陷处理):

1. **`_start_lut_scan` 重复定义**(`experiments.py` 约 772–786 与 788–799 行):同一方法连续定义两次,后者静默覆盖前者;两版逻辑一致,属死代码,建议择期清理以免未来只改到其中一份。
2. **ASV 页运行后按钮命令改接闭包**:扫描结束后运行按钮的 command 从原 `_run_scan` 变为 `asv_run` 的 lambda。功能等价(点击仍会完整重跑 ASV,文字恢复「运行 ASV」),但与 CV 族"恢复原命令对象"的行为不一致;若未来 `_run_scan` 上挂了前置逻辑(如页面态刷新),这条路径会绕过。
3. **Python 3.13 `_tkinter` 行为变化(测试基建注意)**:把 `cget('command')` 得到的命令名以字符串回填后,再 `cget` 会得到 `<bytecode object: '…'>` 包装对象而非纯字符串(功能等价,repr 内含原注册名)。对"以字符串比较恢复前后命令"的新测试会造成误报;本次已用"注册名等价"判据。既有套件实测不受影响(`TestUnifiedScanWindow` 4 项 OK,因其按钮初始无命令)。
4. **设计内的数据对齐告警**(非问题,记录现象):每轮 SWV/DPV/ASV 扫描日志出现 1 条 `swv data has odd length 123, dropping last point`(奇数保护按设计丢弃 1 个尾采样,差分后点数与阶梯 x 轴精确对齐);另有多条 `x/data length mismatch … truncating`(1~2 点端点规则差异按较短者对齐)。画图/存档均正常,点数与参数理论值吻合。
5. **settings_app.txt 副作用**:语言切换会立即持久化生成 `settings_app.txt`(原本不存在)。属于既有设计(语言选择持久化),测试后已删除还原;如需"零残留"测试可提前备份该文件。

## 五、产物与清理记录

- 保留:`autosave_backup/` 中本次生成的 `_CV/_LS/_SWV/_DPV/_ASV/_AMP.csv`(19:32/19:38/19:39 三轮);本报告。
- 还原:`settings.txt` / `settings_asv.txt` 逐字节恢复为原始值(md5 `ddf041e0…` / `58881e54…`,`cmp` 通过);删除测试生成的 `settings_app.txt`;临时目录与测试脚本已删除;无残留 python/tk 进程(进程数 0)。
- 项目源码:**零改动**。
