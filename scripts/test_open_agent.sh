#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# test_open_agent.sh
# 测试通过命令行打开 Cursor 和 Windsurf 的能力
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

pass()  { echo -e "${GREEN}[PASS]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
info()  { echo -e "       $1"; }

# -----------------------------------------------------------
# Agent 配置表：agent_id -> { name, command, check_cmd, open_cmd }
# check_cmd: 检测命令是否可用，输出版本信息
# open_cmd:  打开命令模板，{dir} 会被替换为目录路径
# -----------------------------------------------------------
declare -A AGENTS

AGENTS[cursor_name]="Cursor"
AGENTS[cursor_command]="cursor"
AGENTS[cursor_check]="cursor --version"

AGENTS[windsurf_name]="Windsurf"
AGENTS[windsurf_command]="windsurf"
AGENTS[windsurf_check]="windsurf --version"

# 可扩展更多 agent，按同样格式添加

AGENT_IDS=("cursor" "windsurf")

echo "============================================"
echo " Agent 命令行打开能力检测"
echo "============================================"
echo ""

found_any=false

for id in "${AGENT_IDS[@]}"; do
  name="${AGENTS[${id}_name]}"
  cmd="${AGENTS[${id}_command]}"
  check="${AGENTS[${id}_check]}"

  echo "--- $name ($cmd) ---"

  cmd_path=$(which "$cmd" 2>/dev/null) || true

  if [ -z "$cmd_path" ]; then
    warn "$cmd 命令未安装，跳过"
    echo ""
    continue
  fi

  info "路径: $cmd_path"

  # 执行 check 命令获取版本
  version_output=$($check 2>&1) || true
  info "版本: $version_output"

  # 检测是否支持目录参数（cursor/windsurf 都来源于 VS Code，支持 [paths...]）
  help_output=$($cmd --help 2>&1) || true
  if echo "$help_output" | grep -q "paths"; then
    pass "支持 [paths...] 参数，可直接传入工作目录"
  else
    fail "不支持路径参数"
    echo ""
    continue
  fi

  # 验证命令类型（应是指向 .app 内脚本的 symlink）
  if [ -L "$cmd_path" ]; then
    target=$(readlink "$cmd_path")
    info "Symlink 目标: $target"
    if echo "$target" | grep -qE '\.app/'; then
      pass "命令指向 .app bundle 内脚本，来源可靠"
    else
      warn "命令未指向 .app bundle，可能是自定义安装"
    fi
  elif [ -f "$cmd_path" ] && [ -x "$cmd_path" ]; then
    info "直接可执行文件（非 symlink）"
  fi

  found_any=true
  echo ""
done

echo "============================================"
if $found_any; then
  pass "至少检测到一个可用的 agent 命令"
else
  fail "没有检测到任何可用的 agent 命令"
fi

# -----------------------------------------------------------
# 模拟打开测试（不实际打开，仅验证命令语法）
# -----------------------------------------------------------
echo ""
echo "============================================"
echo " 模拟打开测试（使用临时目录）"
echo "============================================"

TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

for id in "${AGENT_IDS[@]}"; do
  cmd="${AGENTS[${id}_command]}"
  name="${AGENTS[${id}_name]}"

  if ! which "$cmd" &>/dev/null; then
    info "$name: 命令不可用，跳过"
    continue
  fi

  # 模拟打开（--help 不会实际执行，但能验证命令可运行）
  if $cmd --help &>/dev/null; then
    pass "$name: 命令可用，打开语法: $cmd $TEMP_DIR"
  else
    fail "$name: 命令无法正常执行"
  fi
done

echo ""
echo "测试完成。如需实际打开，执行: cursor /path/to/project 或 windsurf /path/to/project"
