#!/usr/bin/env bash
# TaskDeck 一键安装：工具文件就位 + agent skill 就位 + 开机自启（默认）
# 用法: bash install.sh [--no-service] [--dir <安装目录>]
#   --no-service   不注册开机自启（仅装文件 + 起服务）
#   --dir PATH     自定义安装目录（默认 ~/tools/taskdeck）
set -e

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${TASKDECK_DIR:-$HOME/tools/taskdeck}"
PORT=8747
SERVICE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-service) SERVICE=0; shift;;
    --dir) DEST="$2"; shift 2;;
    *) echo "未知参数: $1"; exit 1;;
  esac
done

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "找不到 python3，请先安装 Python 3"; exit 1; }

# 1) 工具文件就位
mkdir -p "$DEST"
cp "$SRC/server.py" "$SRC/task-run.py" "$SRC/frontend.html" "$DEST/"
echo "✓ 工具已装到 $DEST"

# 2) agent skill 就位（opencode / claude 常见 skill 根，存在才装）
for sk in "$HOME/.config/opencode/skills" "$HOME/.opencode/skills" "$HOME/.claude/skills"; do
  if [ -d "$sk" ]; then
    mkdir -p "$sk/taskdeck"; cp "$SRC/SKILL.md" "$sk/taskdeck/SKILL.md"
    echo "✓ skill 已装到 $sk/taskdeck"
  fi
done

mkdir -p "$HOME/.taskdeck"

# 3) 停掉已有实例（避免端口冲突），再由服务统一接管
pkill -f "taskdeck/server.py" 2>/dev/null || true
sleep 0.5

register_service() {
  case "$(uname -s)" in
    Darwin)
      PLIST="$HOME/Library/LaunchAgents/com.taskdeck.server.plist"
      mkdir -p "$HOME/Library/LaunchAgents"
      cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.taskdeck.server</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DEST/server.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.taskdeck/server.log</string>
  <key>StandardErrorPath</key><string>$HOME/.taskdeck/server.log</string>
</dict></plist>
EOF
      launchctl unload "$PLIST" 2>/dev/null || true
      launchctl load -w "$PLIST"
      echo "✓ 已注册 macOS 开机自启 (launchd): com.taskdeck.server"
      ;;
    Linux)
      UNIT="$HOME/.config/systemd/user/taskdeck.service"
      mkdir -p "$HOME/.config/systemd/user"
      cat > "$UNIT" <<EOF
[Unit]
Description=TaskDeck background task monitor
[Service]
ExecStart=$PY $DEST/server.py
Restart=always
[Install]
WantedBy=default.target
EOF
      systemctl --user daemon-reload 2>/dev/null || true
      if systemctl --user enable --now taskdeck.service 2>/dev/null; then
        echo "✓ 已注册 Linux 开机自启 (systemd --user): taskdeck"
      else
        echo "⚠ systemd --user 不可用，跳过自启（可手动 nohup 启动）"
      fi
      ;;
    *)
      echo "⚠ 未识别的平台（如 Windows）：请手动注册自启，例如："
      echo "  schtasks /Create /SC ONLOGON /TN TaskDeck /TR \"\\\"$PY\\\" \\\"$DEST\\server.py\\\"\" /F"
      echo "  或把 server.py 的快捷方式放进「启动」文件夹。"
      ;;
  esac
}

start_now() {
  if ! curl -s -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    nohup "$PY" "$DEST/server.py" > "$HOME/.taskdeck/server.log" 2>&1 &
    sleep 1
  fi
}

if [ "$SERVICE" = "1" ]; then register_service; sleep 1; fi
start_now

# 4) 验证
if curl -s -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "✓ TaskDeck 运行中: http://127.0.0.1:$PORT/"
else
  echo "⚠ 服务未就绪，请看日志: $HOME/.taskdeck/server.log"
fi
echo "安装完成。派发示例: python3 $DEST/task-run.py --name demo --goal \"演示任务\" -- sleep 5"
