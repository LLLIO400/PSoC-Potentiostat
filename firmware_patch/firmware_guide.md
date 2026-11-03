# 固件说明 —— 本分支固件改了什么、怎么刷

## 〇、先读这段

本文件夹 6 个源文件是**合并版固件，同时包含 'O' 搅拌、'P' 真 DPV、CV 多圈、'X' 复位不休眠四个功能增强**。刷机只刷一次：用这 6 个文件覆盖工程同名文件、编译、烧录，全部功能同时生效，不存在"分几次刷"。

**`Pin_Stir` 引脚必须添加**：`main.c` 的 'O' 分支引用了 `Pin_Stir`，即使不用搅拌，不加这个原理图引脚元件固件就编译不过。添加方法见第二节第 5 步。

## 一、补丁改动明细

### 补丁 1：'O' 搅拌控制命令

**功能**：收到 `O|1` → P2.0（J1_01）输出高电平，触发 ZK-TD2 继电器吸合（马达搅拌）；`O|0` → 低电平断开；上电默认低电平。GUI 的 ASV 流程自动调用（富集时开、静置前关）。

| 位置 | 改动 |
|---|---|
| `globals.h` | 新增 `#define STIRRING_CONTROL 'O'` |
| `main.c` | 命令分发新增分支：`case STIRRING_CONTROL: Pin_Stir_Write(...); break;` |
| 原理图 `TopDesign.cysch` | 添加 Digital Output Pin 元件 `Pin_Stir`（配置见刷机步骤第 5 步，**编译必需**） |

### 补丁 2：'P' 真 DPV 命令

**原因**：上游固件无真 DPV——`'G'` 走 SWV 方波 LUT，DPV 函数在源码中被注释（NOT FINISHED）。本补丁向后兼容，不动 'G'/SWV。

**协议与波形**：命令格式 `P|start|end|步进4位|脉冲高度4位|周期5位|类型`，字段布局与 'G' 相同；每个阶梯电平输出两个 LUT 项 **[脉冲电平, 基线电平]**，各占 1 个 PWM 周期（50% 占空比，为"每 tick 一个 DAC 值"架构的固有限制）；采样与 SWV 一致（每步 2 点成对差分），GUI 复用 SWV 数据处理。

| 文件 | 改动 |
|---|---|
| `lut_protocols.c/.h` | 新增 `LUT_make_dpv_line()` |
| `user_selections.c/.h` | 新增 `user_lookup_table_maker_dpv()` |
| `globals.h` | 新增 `#define DPV_PULSE_LUT 'P'` |
| `main.c` | 新增 `case DPV_PULSE_LUT('P')` 分支 |

波形逻辑与数据协议已通过 PC 模拟测试；**2026-10-13 真机验证通过**（-400~600 mV 设 2 圈：'g' 回读 LUT 4002 与理论一致，单次 'R' 20.1 s 连续跑完、圈间零间隙，数据 4001 点完整）。修订：lut_protocols.c 五处 printf 字符串断行导致的编译错误已修复（此前未真机编译过）。峰形/峰位的标液对照验证待做。

### 补丁 3：CV 循环圈数（'S' 命令尾缀 `|N`）

**原因**：上游 CV 的 LUT 只生成一圈三角波，GUI 无法设置循环圈数。

**协议**：`S|start|end|周期5位|CS|N`——在原 20 字符命令后追加 `|N`（N = 圈数，1–9，位于 `data_buffer[21]`）。**旧上位机不带该字段时按 1 圈处理，完全向后兼容**；旧固件收到带圈数的新命令也会忽略 N，只做 1 圈。

**波形**：
- 'S'（Start 起始）：N 个 `start→end→start` 往返连续循环（段间去重端点，**零间隙**，与 CHI/Autolab 等商用仪器的连续循环一致），收尾保持 start 1 拍；
- 'Z'（零点起始）：0→start 一次，N−1 个往返，末圈 start→end 后从 end 回 0 收尾；
- LUT 满 5000 点时提前收尾（GUI 侧会按容量限制圈数并提示）。

**GUI 配合**：GUI 发参数后用 `'g'` 命令回读 LUT 长度判断固件能力——长度 = N 圈期望值 → 固件连续循环；长度 = 1 圈 → **旧固件自动改用软件逐圈调度**（每圈重发 'R'，圈间电极停在起始电位约 0.2–1 s，对 CV 无碍）。即：**不刷此补丁也能做多圈 CV，只是圈间有短暂停顿**。

| 文件 | 改动 |
|---|---|
| `lut_protocols.c/.h` | `LUT_MakeTriangle_Wave()` / `LUT_MakeCVStartZero()` 增加 `cycles` 参数与循环 |
| `user_selections.c` | `user_lookup_table_maker()` 解析 `data_buffer[21]` 圈数 |

### 功能 4：'X' 复位不再休眠硬件（Sleep/Wakeup 根治）

**原因**：上游 `'X'`（停止/复位）会让全部硬件休眠。PSoC 外设的休眠/唤醒须严格配对，`'X'` 的休眠没有对应的唤醒（GUI 停止后下次连接才发 `'H'`），ΔΣ ADC 唤醒后不恢复转换——实际症状：**以"停止/X 复位"结束会话后，下次连接标定读到垃圾值、扫描无响应**。

**改动**：`user_reset_device()` 只关中断，不再调用 `helper_HardwareSleep()`。复位后硬件保持上电正常态（DAC 维持当前电位，ADC 空闲），下次连接标定/扫描即正常。扫描自然结束时的休眠（`dacInterrupt`）与 `'R'` 的唤醒严格配对，保持不变。

**状态**：已修改并已刷入（2026-10-13 合并固件）。当日实测：多次 'X'/扫描收尾后重连，标定正常、扫描正常，验证清单实质闭环：
1. 连接 → 标定正常（无垃圾值）；
2. 跑一次扫描 → 正常完成；
3. 中途点"停止"（发 `'X'`）→ 重新连接 → 标定仍正常；
4. 再跑一次扫描 → 成功。
四步全过即闭环。旧固件不受影响——GUI 的 `'H'` 唤醒规避对老固件依然有效。

| 文件 | 改动 |
|---|---|
| `user_selections.c` | `user_reset_device()` 移除 `helper_HardwareSleep()` 调用 |

## 二、刷机步骤

前提：Windows 安装 PSoC Creator 4.4；下载上游固件仓库 https://github.com/KyleLopin/PSoC-Potentiostat

1. 双击 `Amperometry_v059_2.cydsn\Amperometry_v059_2.cyprj` 打开工程（不要开 `.cywrk`）。
2. 组件版本报错：右键工程 → Update Components... → 全选 → Finish。
3. 缺 DVDAC 依赖时：右键工程 → Dependencies → Add Entry → 选仓库里的 `HighResDacs.cylib`。
4. 先原样编译一次，0 Errors 再继续。
5. **添加 `Pin_Stir` 引脚**：双击 `TopDesign.cysch` → 拖入 Digital Output Pin → 双击配置：General 页 Drive Mode 选 Strong Drive；Reset 页初始电平 Low；Output 页**取消勾选 "HW Connection"**。`.cydwr` → Pins 页分配到 **P2[0]**，勾 Lock。
6. 用本目录 6 个 `.c/.h` 覆盖工程同名文件。
7. Build → Clean and Build。预期 **0 Errors**；Warnings 为上游代码遗留（数量随编译器版本略有出入，均为无害告警，不影响功能）。
8. 接 KitProg（J10），Debug → Program 烧录，完成后拔回 J1。

**注意事项**：

- **不要给工程/文件夹改名**：PSoC Creator 要求 `.cyprj` 与所在 `.cydsn` 文件夹同名，改名会导致工程无法打开或时钟配置丢失。保持 `Amperometry_v059_2` 原名直接覆盖文件。
- 若遇时钟报错（ILO must be set to 100 kHz / USB clock is disabled）：.cydwr → Clocks → Edit Clock：IMO 选 24 MHz；ILO 勾 100 kHz 且 Route 选 100 kHz；启用 USB 并选 IMOx2 (48 MHz)；Save All 后重新编译。若无法保存，是文件夹只读或桌面写入被拦截，把工程挪出桌面（如 C:\PSoC\）并取消只读属性。

## 三、与 GUI 的配合

- GUI 的 DPV 页和 ASV-DPV 溶出均发送 'P' 命令；**旧固件（无 'P'）下 DPV 扫描无响应**，固件与 GUI 必须同为新版。
- CV 多圈：新固件零间隙连续循环；旧固件 GUI 自动逐圈调度兜底（见补丁 3）。
- 搅拌接线见升级说明文档第五节（ZK-TD2 设 P13 模式、OP=0.1 s）。
