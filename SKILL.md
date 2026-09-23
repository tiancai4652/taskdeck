---
name: taskdeck
description: 后台长任务监控。派发后台脚本/长命令/批处理任务时,用 task-run 一步完成"后台运行 + 日志 + 注册",让用户在 TaskDeck 面板(总览+详情)实时看到脚本目的、日志滚动和健康判定(卡死/空转/超期)。USE WHEN 后台任务, 长任务, 后台脚本, 跑个脚本, nohup, run in background, 后台执行, batch job, 挂机跑, 后台爬虫, 长时间运行, 监控脚本状态, 脚本卡死. NOT FOR 前台交互命令, subagent 内的普通工具调用, 或已由 CI/工作流系统管理的任务.
---

# TaskDeck — 后台长任务监控

服务:`python3 ~/tools/taskdeck/server.py`(127.0.0.1:8747,自动常驻,数据在 `~/.taskdeck/`)。
大盘:`http://127.0.0.1:8747/` · 每个任务有独立详情页(日志实时滚动 + 健康判定)。

## 铁律

1. **凡是预计运行超过 1 分钟、且要在后台跑的命令,必须用 `task-run` 派发**,不要裸 `nohup`/`&`。
2. `--name` 和 `--goal` 必填且认真写:用户在面板上主要靠 `--goal` 理解这个脚本在干什么。
3. 派发后把返回的详情页 URL 告诉用户。

## 用法

```bash
python3 ~/tools/taskdeck/task-run.py \
  --name "爬取中报PDF" \
  --goal "从巨潮资讯抓取 2026 年中报 PDF 到 ./reports/,预计 200 个文件" \
  --max-minutes 120 \
  -- python3 crawl.py --year 2026
```

- `--name` 短标题(必填)
- `--goal` 这个脚本做什么、预期产出(必填,写给用户看)
- `--max-minutes` 预期最长时长,超过面板标"超期"(可选,长任务建议给)
- `--beat-seconds` 心跳阈值:超过该秒数无日志输出即判定疑似卡死(默认 300;爬虫/编译类可给 600,快速脚本给 30)
- `--group` 分组键,默认取当前目录(面板按此分组)

task-run 会自动:脱离终端后台运行(detached)、日志重定向到 `~/.taskdeck/logs/<id>.log`、堵死 stdin(防止"卡在等输入")、确保服务已启动并注册。

## 健康判定(面板自动展示)

| 判定 | 含义 |
|---|---|
| 运行中 | 日志在推进,健康 |
| 无输出·疑似卡死 | 日志停滞超阈值且 CPU 空闲 → 等网络/锁/输入 |
| 无输出·疑似空转 | 日志停滞但 CPU 高 → 死循环嫌疑 |
| 超期 | 超过 `--max-minutes` |
| 已结束/疑似失败 | 进程退出(尾部有 Traceback/panic 则标疑似失败) |

## 其他 API

```bash
# 服务探活
curl -s http://127.0.0.1:8747/api/health
# 总览数据
curl -s http://127.0.0.1:8747/api/tasks
# 登记一个已在跑的外部进程(只盯日志 + 可选 pid)
curl -s -X POST http://127.0.0.1:8747/api/tasks -H 'Content-Type: application/json' \
  -d '{"name":"xx","goal":"xx","log_path":"/abs/path.log","pid":1234}'
# 终止
curl -s -X POST http://127.0.0.1:8747/api/tasks/<id>/kill
```

## 平台

macOS / Linux / Windows 通用(Python 3 stdlib,零依赖)。Windows 上 CPU 采样不可用(判定退化为仅日志心跳),其余功能一致。
