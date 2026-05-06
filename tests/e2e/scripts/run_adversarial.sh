#!/usr/bin/env bash
# ═══ AgentTriad 刁钻 E2E 测试运行器 ═══
# 用法: bash tests/e2e/scripts/run_adversarial.sh [组名]
# 组名: dedup | noise | collision | executor | all (默认)
#
# 每组独立运行，失败不阻断其他组。
# 结果写入 tests/e2e/results/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RESULTS_DIR="$PROJECT_ROOT/tests/e2e/results"
mkdir -p "$RESULTS_DIR"

# ─── 颜色 ───
R="\033[0m"; BOLD="\033[1m"; GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"

run_group() {
    local name="$1"
    local script="$2"
    local report="$3"
    local timeout="$4"
    local extra_flags="$5"

    echo -e "\n${BOLD}${CYAN}═══════════════════════════════════════════════════${R}"
    echo -e "${BOLD}${CYAN}  组: $name  脚本: $script${R}"
    echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════${R}"

    cd "$PROJECT_ROOT"
    local cmd="uv run chat.py --kt --script $script --report $report --turn-timeout $timeout --reset-kt-root $extra_flags"
    echo -e "  ${YELLOW}命令: $cmd${R}\n"

    if eval "$cmd"; then
        echo -e "\n  ${GREEN}✓ $name 通过${R}"
        return 0
    else
        echo -e "\n  ${RED}✗ $name 失败 (exit=$?)${R}"
        return 1
    fi
}

# ─── 测试组定义 ───
declare -A GROUPS
GROUPS=(
    ["dedup"]="tests/e2e/scripts/dedup_stress.txt|$RESULTS_DIR/dedup_stress.json|90|--kt-root workspace/kt_dedup_test"
    ["noise"]="tests/e2e/scripts/noise_antihallucination.txt|$RESULTS_DIR/noise_antihallucination.json|90|--kt-root workspace/kt_noise_test"
    ["collision"]="tests/e2e/scripts/collision_path.txt|$RESULTS_DIR/collision_path.json|90|--kt-root workspace/kt_collision_test"
    ["executor"]="tests/e2e/scripts/adversarial_executor.txt|$RESULTS_DIR/adversarial_executor.json|120|--kt-root workspace/kt_executor_test"
)

TARGET="${1:-all}"
PASSED=0
FAILED=0
FAILED_GROUPS=""

run_target() {
    local key="$1"
    local IFS='|'
    read -r script report timeout extra <<< "${GROUPS[$key]}"
    if run_group "$key" "$script" "$report" "$timeout" "$extra"; then
        ((PASSED++)) || true
    else
        ((FAILED++)) || true
        FAILED_GROUPS="$FAILED_GROUPS $key"
    fi
}

# ─── 执行 ───
echo -e "${BOLD}${CYAN}"
echo "╔════════════════════════════════════════════════════╗"
echo "║   AgentTriad 刁钻 E2E 测试套件                    ║"
echo "║   4 组 × ~10 轮 × 真实 LLM × chat.py --script    ║"
echo "╚════════════════════════════════════════════════════╝"
echo -e "${R}"

START_TIME=$SECONDS

if [ "$TARGET" = "all" ]; then
    for key in dedup noise collision executor; do
        run_target "$key"
    done
elif [ -n "${GROUPS[$TARGET]+x}" ]; then
    run_target "$TARGET"
else
    echo -e "${RED}未知组: $TARGET${R}"
    echo "可用组: dedup noise collision executor all"
    exit 1
fi

ELAPSED=$(( SECONDS - START_TIME ))

# ─── 汇总 ───
echo -e "\n${BOLD}${CYAN}═══════════════════════════════════════════════════${R}"
echo -e "${BOLD}${CYAN}  汇总${R}"
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════${R}"
echo -e "  通过: ${GREEN}$PASSED${R}  失败: ${RED}$FAILED${R}  耗时: ${ELAPSED}s"

if [ -n "$FAILED_GROUPS" ]; then
    echo -e "  ${RED}失败组:$FAILED_GROUPS${R}"
fi

echo -e "\n  报告目录: $RESULTS_DIR/"
ls -la "$RESULTS_DIR/"*.json 2>/dev/null || echo "  (无报告文件)"

if [ "$FAILED" -gt 0 ]; then
    echo -e "\n  ${YELLOW}提示: 查看失败组的 JSON 报告中 tool_calls / tool_outputs 字段${R}"
    echo -e "  ${YELLOW}      确认是工具逻辑失败还是模型超时${R}"
fi

exit $FAILED
