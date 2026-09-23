# TaskDeck

后台长任务监控面板（macOS / Linux / Windows，Python 3 **标准库零依赖**）。

给 AI agent（或你自己）一个「派发后台任务 → 在网页看进度 → 自动判卡死/空转/超期」的闭环。44K，4 个文件，不装任何第三方包。

## 安装

```bash
git clone https://github.com/tiancai4652/taskdeck.git
bash taskdeck/install.sh
```

`install.sh` 会：
1. 把工具装到 `~/tools/taskdeck/`（可用 `--dir` 改）
2. 把 agent 用法说明装到已有的 skill 目录（`~/.config/opencode/skills/`、`~/.opencode/skills/`、`~/.claude/skills/` 里存在的）
3. **注册开机自启**（macOS launchd / Linux systemd --user / Windows 给出 schtasks 命令）
4. 起服务并验证 `http://127.0.0.1:8747/`

```bash
bash install.sh --no-service      # 只要文件，不注册自启
bash install.sh --dir /opt/taskdeck
```

> 服务本身无需守护进程也能用：`task-run.py` 带 `ensure_server()`，检测到服务没起会自动拉起。自启只是让面板常驻、重启后立即可用。

## 用法

```bash
# 服务（install.sh 已自动起；手动起也行）
python3 ~/tools/taskdeck/server.py        # → http://127.0.0.1:8747

# 派发后台任务：一步完成「脱离终端运行 + 日志重定向 + 堵死 stdin + 注册」
python3 ~/tools/taskdeck/task-run.py \
  --name "抓取数据" \
  --goal "拉取近 30 天行情，预计 10 分钟，产出 data/*.csv" \
  --max-minutes 30 \
  -- python3 fetch.py
```

- 数据与日志在 `~/.taskdeck/`；服务只绑 `127.0.0.1`
- agent 使用说明见 `SKILL.md`（安装器已装到 skill 目录，opencode/claude 可识别）

## 健康判定

| 状态 | 条件 |
|---|---|
| 运行中 | 日志在推进（心跳正常） |
| 无输出·疑似卡死 | 日志停滞超 `beat_seconds` 且 CPU 空闲 |
| 无输出·疑似空转 | 日志停滞但 CPU 高（≥25%） |
| 超期 | 超过 `--max-minutes` |
| 已结束 / 疑似失败 | 进程退出（尾部有 Traceback/panic → 疑似失败） |

> Windows 下 CPU 判定自动降级（`ps` 不可用），其余判定照常。

## API

```
GET  /                       总览大盘
GET  /task/<id>              详情页（日志跟随 + 判定 + kill）
GET  /api/health             探活
GET  /api/tasks              任务列表 JSON
POST /api/tasks              注册 {name, goal, command?, log_path, pid?, cwd?, max_minutes?, beat_seconds?}
GET  /api/tasks/<id>/log?offset=N   增量日志
POST /api/tasks/<id>/kill    终止进程
```

## 卸载

```bash
# macOS
launchctl unload ~/Library/LaunchAgents/com.taskdeck.server.plist
rm ~/Library/LaunchAgents/com.taskdeck.server.plist
# Linux
systemctl --user disable --now taskdeck.service && rm ~/.config/systemd/user/taskdeck.service
# 通用
rm -rf ~/tools/taskdeck ~/.taskdeck
```

## 设计要点（为什么这样做）

- **监控带外**：观察日志 mtime/size、pid 存活、CPU——不依赖任务自己汇报，阻塞/挂起的任务也能被判定
- **`task-run` 一步完成**：detached 后台运行 + 日志重定向 + stdin 堵死（防"卡在等输入"）+ 注册；协议越省事越会被 agent 遵守
- **分组键用工作目录（项目）**，会话 ID 易碎不采用；归属由注册时显式声明
- **判定展示证据**（最后输出时间 / 日志增速 / CPU），不只给结论

## License

MIT
