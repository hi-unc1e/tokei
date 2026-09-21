#!/bin/bash
# Tokei 一键发布（本地零编译版）。
# 用法: ./release.sh [--notes "版本说明"]
#
# 所有构建都在 GitHub Actions 的 macOS runner 上进行（.github/workflows/release.yml）。
# 本脚本只做三件事：校验工作区 → 从 Updater.swift 读版本号 → 打 tag 并推送，
# 推送 v* 标签后 GitHub 会自动编译 DMG 并发布到本仓库的 GitHub Release。
set -euo pipefail
cd "$(dirname "$0")"

NOTES="${2:-}"
[ "${1:-}" = "--notes" ] || NOTES=""

VERSION="$(sed -nE 's/.*releaseTag = "v([^"]+)".*/\1/p' Tokei/Sources/Tokei/Updater.swift | head -n 1)"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "❌ 无法从 Updater.swift 读取版本号"; exit 1; }
TAG="v$VERSION"
echo "==> 目标版本: $TAG"

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "❌ 工作区有未提交改动,先提交再发布"; exit 1
fi
git fetch -q && [ -z "$(git log HEAD..origin/main --oneline)" ] || { echo "❌ 本地落后远端,先 git pull"; exit 1; }
[ -z "$(git log origin/main..HEAD --oneline)" ] || { echo "❌ 有未 push 的提交,先 git push"; exit 1; }

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "❌ 标签 $TAG 已存在。请先提升 Tokei/Sources/Tokei/Updater.swift 中的 releaseTag 再发布。"; exit 1
fi

git tag -a "$TAG" -m "${NOTES:-Release $TAG}"
git push origin "$TAG"
echo "✅ 已推送标签 $TAG,GitHub Actions 正在编译并发布 Release:"
echo "   https://github.com/hi-unc1e/tokei/actions/workflows/release.yml"
