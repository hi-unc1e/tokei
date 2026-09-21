#!/bin/bash
# 遥测/后门黑名单检查 —— 本地 pre-commit 钩子与 GitHub Actions（CI、上游同步）共用的兜底闸门。
#
# 用法:
#   scripts/check_blacklist.sh            扫描当前 git 追踪的全部文件（CI / 同步工作流用）
#   scripts/check_blacklist.sh --staged   只扫描本次暂存的改动（pre-commit 钩子用，快）
#
# 命中任何模式即退出码 1，并打印命中的文件与加入黑名单的原因。
# 新增模式时请务必写明原因，保持一行一个 "PATTERN<TAB>原因"。
set -uo pipefail

cd "$(dirname "$0")/.."

# 黑名单：模式 <TAB> 原因
# shellcheck disable=SC2016
BLACKLIST=(
    'tokei-ops\.lanshuagent\.com	上游作者的运营/遥测端点：应用曾每日向它上报随机安装 ID、版本与系统信息，已彻底移除，禁止回归'
    'ActivityReporter	已删除的遥测模块类名（每日心跳上报安装 ID/版本/OS），禁止重新引入'
    'shareBasicActivity	已删除的遥测开关 UserDefaults key（ActivityReporter.enabledKey），禁止重新引入'
    'activityStatisticsEnabled	已删除的遥测设置开关变量，禁止重新引入'
    'dl\.lanshuagent\.com	上游作者的私有下载/更新 CDN：本 fork 的更新与分发一律走本仓库（hi-unc1e/tokei）的 GitHub Releases，禁止回退'
)

STAGED=0
[ "${1:-}" = "--staged" ] && STAGED=1

# 只扫描源代码与配置，跳过文档中的历史性提及（README 里的官网/致谢链接属信息引用，不构成代码回归）
SCAN_PATHS='Tokei/Sources tests usage.30s.py scripts .github install.sh release.sh'

fail=0
for entry in "${BLACKLIST[@]}"; do
    pattern="${entry%%	*}"
    reason="${entry#*	}"

    if [ "$STAGED" -eq 1 ]; then
        # pre-commit：只看本次暂存、且属于扫描范围的文件
        files="$(git diff --cached --name-only --diff-filter=ACMR -- $SCAN_PATHS 2>/dev/null || true)"
        [ -z "$files" ] && continue
        matches="$(grep -lE "$pattern" $files 2>/dev/null || true)"
    else
        matches="$(git ls-files -- $SCAN_PATHS 2>/dev/null | xargs grep -lE "$pattern" 2>/dev/null || true)"
    fi

    # 排除本脚本自身（它必然包含这些模式定义）
    matches="$(echo "$matches" | grep -v '^scripts/check_blacklist.sh$' || true)"

    if [ -n "$matches" ]; then
        fail=1
        echo "❌ 黑名单命中: /$pattern/" >&2
        echo "   原因: $reason" >&2
        echo "$matches" | sed 's/^/   - /' >&2
    fi
done

if [ "$fail" -ne 0 ]; then
    echo >&2
    echo "提交被拒绝。如确属误报，请在 scripts/check_blacklist.sh 中调整模式并说明理由。" >&2
    exit 1
fi

exit 0
