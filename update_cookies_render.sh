#!/bin/bash
# =====================================================
# 一键从 Chrome 提取最新 Cookie 并推送到 Render
# 用法: ./update_cookies_render.sh
# =====================================================

RENDER_URL="https://douyin-download-iu1s.onrender.com"
COOKIES_FILE="cookies.txt"
UPDATE_TOKEN="${COOKIES_UPDATE_TOKEN:-}"

echo "======================================"
echo "  抖音 Cookie 自动更新工具"
echo "======================================"
echo ""
echo "📦 第一步：从浏览器提取最新 Cookie..."

# 注意: 不使用 set -e，而是手动检查 cookies.txt 文件内容
# yt-dlp 提取 Cookie 成功后即使退出码非零，Cookie 文件也已写入

# 先删除旧文件
rm -f "$COOKIES_FILE"

# 尝试从 Chrome 提取
.venv/bin/yt-dlp \
    --cookies-from-browser chrome \
    --cookies "$COOKIES_FILE" \
    --skip-download \
    --quiet \
    "https://www.douyin.com/" 2>&1 | grep -v "ERROR\|WARNING" || true

# 检查是否写入成功（只要文件存在且有内容就算成功）
if [ -f "$COOKIES_FILE" ] && [ -s "$COOKIES_FILE" ]; then
    LINE_COUNT=$(grep -c "douyin.com" "$COOKIES_FILE" 2>/dev/null || echo 0)
    echo "✅ Chrome Cookie 提取成功！(找到 $LINE_COUNT 条 douyin.com cookie)"
else
    echo "   Chrome 未找到，尝试 Safari..."
    rm -f "$COOKIES_FILE"
    .venv/bin/yt-dlp \
        --cookies-from-browser safari \
        --cookies "$COOKIES_FILE" \
        --skip-download \
        --quiet \
        "https://www.douyin.com/" 2>&1 | grep -v "ERROR\|WARNING" || true

    if [ -f "$COOKIES_FILE" ] && [ -s "$COOKIES_FILE" ]; then
        LINE_COUNT=$(grep -c "douyin.com" "$COOKIES_FILE" 2>/dev/null || echo 0)
        echo "✅ Safari Cookie 提取成功！(找到 $LINE_COUNT 条 douyin.com cookie)"
    else
        echo "❌ 无法从浏览器提取 Cookie"
        echo ""
        echo "   请检查："
        echo "   1. Chrome 或 Safari 是否已打开"
        echo "   2. 浏览器中是否已登录 douyin.com"
        echo "   3. 尝试在浏览器中手动打开 https://www.douyin.com/ 并确认已登录"
        exit 1
    fi
fi

# 验证 sessionid 是否存在（说明是已登录状态）
if ! grep -q "sessionid" "$COOKIES_FILE" 2>/dev/null; then
    echo "⚠️  警告：Cookie 中没有 sessionid，你可能没有登录抖音"
    echo "   请先在浏览器中登录 douyin.com，再运行此脚本"
    exit 1
fi

echo ""
echo "📤 第二步：推送 Cookie 到 Render 服务器..."
echo "   目标: $RENDER_URL"

COOKIES_JSON=$(python3 -c "import json, sys; print(json.dumps(open('$COOKIES_FILE').read()))")

HTTP_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${RENDER_URL}/update-cookies" \
    -H "Content-Type: application/json" \
    -H "X-Update-Token: ${UPDATE_TOKEN}" \
    -d "{\"cookies\": ${COOKIES_JSON}}" \
    --max-time 60 2>/dev/null)

HTTP_BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$HTTP_RESPONSE" | tail -n 1)

if [ "$HTTP_CODE" = "200" ]; then
    COUNT=$(echo "$HTTP_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('cookie_count','?'))" 2>/dev/null || echo "?")
    echo "✅ 推送成功！已上传 $COUNT 条 Cookie"
    echo ""
    echo "📊 第三步：验证 Cookie 状态..."
    STATUS=$(curl -s "${RENDER_URL}/cookie-status" --max-time 30 2>/dev/null)
    echo "$STATUS" | python3 -m json.tool 2>/dev/null || echo "$STATUS"
    echo ""
    echo "🎉 完成！Cookie 已更新，Bot 现在可以正常使用了。"
    echo "   下次需要更新时，重新运行此脚本即可（通常 30 天内不需要）。"
else
    echo "❌ 推送失败 (HTTP ${HTTP_CODE:-超时})"
    echo "   响应: $HTTP_BODY"
    echo ""
    echo "   可能的原因："
    echo "   - Render 服务正在冷启动（等 30 秒后重试）"
    echo "   - 网络连接问题"
fi
