# 魔云腾控制中心开发规则

本文描述当前代码已经落地的技术选型、架构边界，以及为避免占满本机 CPU、内存和界面线程所做的优化。后续改动按这些规则继续，不要把已经收敛掉的压力再加回来。

## 技术栈

| 层次 | 选用 | 用途 |
| --- | --- | --- |
| 语言 | Python 3.10 | 桌面程序与测试 |
| 界面 | PySide6（Qt） | 主窗口、表格、定时器、线程池 |
| 数据模型 | pydantic 2、`dataclass` | 配置校验与主机/云机模型 |
| HTTP | httpx | 魔云腾盒子 API、RPA HTTP、备份流式下载 |
| 本地库 | SQLite（`sqlite3`，WAL） | 主机、云机、任务队列、自动化批次、设置 |
| 打包 | PyInstaller、可选 PyArmor | `build_exe.bat` 产出 Windows EXE |
| 测试 | pytest | `tests/` 覆盖队列、安装、自动化、界面节流 |

运行时数据目录：`%APPDATA%\MoyuntengControl\moyunteng.sqlite3`。崩溃日志放在程序目录的 `debug_log/`，按天滚动，默认保留 7 天。

## 架构

单进程 Windows 桌面程序，界面与后台任务分离。模块职责如下。

```text
main.py
  崩溃钩子（主线程 / 线程 / unraisable）
  └─ app.main_window.MainWindow     界面、事件节流、页面
       ├─ app.database.Database     SQLite，单连接 + RLock
       ├─ app.tasks.TaskService     任务调度、脚本流水线、本地 APK 服务
       │    ├─ app.api_client       盒子 HTTP API
       │    ├─ app.rpa              盒子 /rpa/* HTTP 适配
       │    └─ app.network_discovery  局域网扫描
       ├─ app.models                Host / CloudDevice / AutomationConfig
       ├─ app.statuses              云机与脚本状态
       └─ app.config                路径、崩溃日志保留
```

数据流：

1. 用户操作只进 Qt 主线程，耗时工作通过 `QThreadPool` 的 `Worker` 丢到后台。
2. 后台通过 `TaskEvent` 回报进度。Worker 不走无界 Qt Signal，而是写入线程安全的入口队列。
3. 主线程按 50ms 批量取出事件，合并日志，再局部刷新表格和日志框。
4. 云机级工作不直接 `ThreadPoolExecutor.submit`。先进入该云机的内存队列，由调度器在并发额度内再提交。排队项不占工作线程。
5. 持久状态只进 SQLite。远程列表刷新时保留本机字段（脚本阶段、运行时长、自动化流程），避免被盒子快照覆盖。

业务分层：

- **主机与云机**：扫描、增删改、列表、状态刷新。
- **创建**：主机级创建队列（SQLite `queued_tasks`）。同一时刻全局只允许一台主机创建。同一批创建共用一个轮询器，不按云机各打一遍列表接口。
- **安装**：本机临时 HTTP 提供 APK，盒子走 `/android/batchInstallApk`（V2 镜像走 `/androidV2/batchInstallApk`）。
- **脚本**：固定抖音极速版流程。同一云机内搜索、点击、滑动、等待按顺序执行，不在脚本内部再开并行线程。
- **备份 / 导入**：备份下载按块写盘。备份任务使用独立线程池，且单任务跨主机同时导出不超过 5 台。
- **自动化**：定时扫描已勾选主机，按阶段推进：待安装 → 安装 → 脚本 → 备份 → 关闭。批次记在 `automation_batches`。

并发额度（默认，可在自动化配置里调）：

- 每台主机同时最多 5 个云机任务（上限 10）。
- 全局同时执行的云机任务默认 100。线程池可以扩，但未轮到的任务仍只待在队列里。
- 手动/队列备份：单个备份任务同时最多导出 5 台。
- 备份界面线程池 `maxThreadCount = 1`，避免多路备份同时刷界面。
- 关闭、删除、备份用更高优先级插队，并先给当前脚本发停止信号。
- 「停止脚本」只停脚本，不取消创建、安装、备份。

Python 不能安全杀掉正在执行的线程。停止只是 `Event`。网络请求、等待和重试必须轮询这个信号；单次 HTTP 要等返回或超时后才能退出。

## 本地性能规则

这些规则针对运行本程序的 Windows 电脑，不是魔云腾主机。目标是：界面不「未响应」，日志和表格不把内存和 CPU 吃满，等待中的任务不提前占线程。

### 1. 排队不占线程

- 云机任务先进 `device_task_queues`。调度器 `_pump_device_tasks_locked` 只在「该云机空闲、该主机未满、全局未满」时才 `submit`。
- 同一云机同一时刻只有一个普通任务在执行。脚本步骤不再另开线程。
- 不要为了「等接口」预先创建睡眠线程。等待放在已占用的那个工作线程里，用 `_sleep_with_stop`（最多 0.2 秒一片）以便能停。

### 2. 界面事件必须节流

后台事件路径已经是：入口 deque → 50ms 排空 → 合并 → 待处理队列 → 每拍最多处理 400 条 → 50ms 后再拍。

必须遵守：

- 新的后台回报走 `event_sink`（`_submit_worker_event`），不要每个事件 `Signal.emit`。Qt 队列信号在多主机日志洪峰下会无界增长。
- 日志事件在队列变长时合并（`_coalesce_task_events`），用「合并 N 条」保留内容，不要直接丢弃，也不要逐条 `appendPlainText`。
- 日志框 `setMaximumBlockCount(5000)`。待写日志超过约 2000 行时，普通行让路，错误行仍保留。
- 一次刷日志最多 120 行，然后让出主线程。
- 用户正在回看历史时不要强制滚到底。

### 3. 表格局部更新，禁止无意义整表重建

- 进度事件优先改已有行（状态、脚本、备份列），整表 `reload_devices_table` 只在成员变化或任务结束等必要时机触发。
- 设备表全量刷新最短间隔约 1.2 秒，并用 `_device_table_refresh_pending` 合并连续请求。
- 刷新表格时 `setUpdatesEnabled(False)`，改完再打开。
- 脚本运行时长每秒只改正在跑的那一列，文本没变就不 `setText`。
- 「仅看运行中」按 40 行一块隐藏/显示，块之间 `processEvents`，避免弱机器上勾选框把窗口卡死。
- 行是否已关闭缓存在单元格 `UserRole` 上，过滤时不要每行再查库。
- 自动化扫描结束默认 `_refresh_ui_on_done=False`，扫描很频繁，不能每次都重建主机表和云机表。
- 备份过程日志不进主日志面板，阶段看备份队列。队列徽章、备份队列、创建队列的刷新都要合并，不能每条日志打一次 SQL 再刷整表。

### 4. 传给界面的对象要瘦

- `_emit` 发出去的 `CloudDevice` 必须经 `_slim_device_for_event`，只带本机需要的 `extra` 键。
- 普通日志不附带整份设备对象；安装行补丁才附带瘦身后的设备，并带上 `device_id`。
- 不要把盒子 API 的原始大 JSON 放进 `TaskEvent.data` 再交给 Qt。

### 5. 网络与磁盘不进 UI 线程，也不整包进内存

- 扫描、刷新、创建、安装、脚本、备份、停止脚本（会关 RPA）全部走 `Worker`。
- 局域网扫描只探本机 `/24`，连接超时 0.25 秒，线程数有上限。
- 备份下载用 `httpx.stream` + 1MB `iter_bytes` 写盘，禁止把整个备份读进内存。导出进度用 SSE/分块读取，不把整段流留在内存里。
- 导入上传直接传文件对象，不先 `read()` 全文件。
- APK 由本机一个可复用的 `ThreadingHTTPServer` 提供，按 SHA 注册路径，不把 APK 拷进内存。
- 创建批次用 `_CreationBatchCoordinator`：一批云机只起一个列表轮询（默认 3 秒），其余线程在条件变量上等待。

### 6. SQLite 要轻

- 单连接，`check_same_thread=False`，所有访问拿 `RLock`。`journal_mode=WAL`，`synchronous=NORMAL`。
- 启动时清理：上次错误、中断的安装/脚本、未完成队列标为已中断（不自动续跑）、任务历史保留 7 天且完成记录最多约 1000 条。
- 主机列表用 `_hosts_cache`。勾选主机的写库用单次定时器合并，不要每次点击都 `commit`。
- 新增查询要走现有索引（主机+状态+排序、批次+主机）。不要在 UI 定时器里对全表做无过滤扫描。

### 7. 退出不能在 UI 线程上等待

`closeEvent` 只做这些事：停定时器、`shutdown(fast=True)` 打停止标记、`os._exit(0)`。

禁止在关闭路径里等待线程池、`join` APK 服务、关 RPA、或再跑一遍数据库清理。那些等待会显示成「未响应」。

### 8. 定时器节奏

| 定时器 | 间隔 | 做什么 |
| --- | --- | --- |
| 工作事件入口 | 50ms | 排空后台事件 |
| 任务事件 / 日志刷出 | 50ms | 分批上屏 |
| 设备表合并刷新 | ≥120ms，全量至少隔 1.2s | 重建云机表 |
| 队列徽章 | 80ms 合并 | 导航红点 |
| 线程数、脚本时长 | 1s | 只改变化的单元格 |
| 自动化扫描 | 默认 30 秒（5–3600） | 扫已勾选主机 |

新增轮询先复用这些定时器或合并标志（`*_pending`）。不要再挂一个更密的 `QTimer` 去刷同一块 UI。

## 开发时必须遵守

1. **UI 线程零阻塞。** 不在槽函数里发 HTTP、扫网、等文件、等线程。需要结果就用 `Worker` + `TaskEvent`。
2. **先队列，后线程。** 新的云机级工作走 `_submit_device_task`。不要在批量循环里无上限 `submit`。
3. **同一云机串行，多云机并行。** 脚本内部保持顺序。主机内并发不超过配置值。
4. **可停止。** 长时间 `sleep`、重试、下载循环都要看 `stop_event`，并使用已有的 `TaskCancelled`。
5. **事件可合并。** 高频进度用 `kind` 区分。日志用 `log`；需要改表格用 `device` / `progress`。备份流水不要打进主日志。
6. **瘦载荷。** 事件里的设备、JSON 摘要走现有瘦身函数（`_slim_device_for_event`、`_short_json`）。
7. **本地字段不被远程快照清掉。** 写 `device.extra` 时走 `merge_local_device_extra` / `preserve_local_device_extra`。
8. **状态用 `app.statuses` 里的常量**，不要在界面和任务里各写一套中文状态。
9. **配置用 `AutomationConfig.from_dict`。** 数值有上下限（主机并发 1–10，扫描间隔 5–3600，下载端口 1–65535）。
10. **异常进 `debug_log`。** UI 批处理失败走 `_record_ui_exception`。不要空 `except` 把故障吃掉还不留日志。
11. **测试随行为走。** 队列、停止、安装结果、创建轮询、界面节流已有对应用例。改这些路径要补或改 `tests/`，并用 `pytest` 跑相关文件。
12. **打包不改运行时路径假设。** 冻结后 `app_dir()` 是 EXE 所在目录，数据库仍在 `%APPDATA%`。新增资源文件要能在 PyInstaller 产物里找到。

## 不该做的事

- 为每台云机或每条日志新建线程、QTimer 或 Qt Signal。
- 在主线程 `time.sleep`、同步 httpx、或 `executor.shutdown(wait=True)`。
- 每次状态变化清空并重建整个 `QTableWidget`。
- 把 UI XML、截图、备份包、APK 放进日志或 SQLite。
- 用递归恢复绕过「当前必须在目标 APP 包名内」的校验。
- 在自动化扫描的每一拍上做全量主机/云机刷新。
- 关闭窗口时等待后台任务自然结束。
