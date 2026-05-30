#!/bin/bash
# =====================================================
# 一键从 Chrome 提取最新 Cookie 并推送到 Render
# 用法: ./update_cookies_render.sh
# =====================================================

set -e

RENDER_URL="https://douyin-download-iu1s.onrender.com"
COOKIES_FILE="cookies.txt"
UPDATE_TOKEN="${COOKIES_UPDATE_TOKEN:-}"   # 从环境变量读取，可选

echo "======================================"
echo "  抖音 Cookie 自动更新工具"
echo "======================================"
echo ""
echo "📦 第一步：从 Chrome 浏览器提取最新 Cookie..."
echo "    ⚠️  请确保 Chrome 已打开且已登录 douyin.com"
echo ""

# Extract cookies from Chrome
if .venv/bin/yt-dlp \
    --cookies-from-browser chrome \
    --cookies "$COOKIES_FILE" \
    --skip-download \
    "https://www.douyin.com/" 2>/dev/null; then
    echo "✅ Chrome Cookie 提取成功"
elif .venv/bin/yt-dlp \
    --cookies-from-browser safari \
    --cookies "$COOKIES_FILE" \
    --skip-download \
    "https://www.douyin.com/" 2>/dev/null; then
    echo "✅ Safari Cookie 提取成功"
else
    echo "❌ 无法从浏览器提取 Cookie"
    echo "   请确保 Chrome 或 Safari 已打开且已登录 douyin.com"
    exit 1
fi

if [ ! -f "$COOKIES_FILE" ] || [ ! -s "$COOKIES_FILE" ]; then
    echo "❌ Cookie 文件为空，提取失败"
    exit 1
fi

LINE_COUNT=$(wc -l < "$COOKIES_FILE")
echo "   已提取 $LINE_COUNT 行 Cookie 数据"

echo ""
echo "📤 第二步：推送 Cookie 到 Render 服务器..."

COOKIES_JSON=$(python3 -c "import json, sys; print(json.dumps(open('$COOKIES_FILE').read()))")

HTTP_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${RENDER_URL}/update-cookies" \
    -H "Content-Type: application/json" \
    -H "X-Update-Token: ${UPDATE_TOKEN}" \
    -d "{\"cookies\": ${COOKIES_JSON}}")

HTTP_BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$HTTP_RESPONSE" | tail -n 1)

echo "   服务器响应 (HTTP $HTTP_CODE):"
echo "   $HTTP_BODY" | python3 -m json.tool 2>/dev/null || echo "   $HTTP_BODY"

if [ "$HTTP_CODE" = "200" ]; then
    echo ""
    echo "✅ 推送成功！验证 Cookie 状态..."
    curl -s "${RENDER_URL}/cookie-status" | python3 -m json.tool
    echo ""
    echo "🎉 完成！Cookie 已更新，Bot 应该可以正常使用了。"
    echo "   下次 Cookie 需要更新时，重新运行此脚本即可。"
else
    echo ""
    echo "❌ 推送失败 (HTTP $HTTP_CODE)"
    echo "   请检查 Render 服务是否正在运行"
fi
