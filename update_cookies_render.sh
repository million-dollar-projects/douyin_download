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
echo "📦 第一步：从 Chrome 浏览器提取最新 Cookie..."

rm -f "$COOKIES_FILE"

# macOS 上 Chrome 的 Default profile 路径
CHROME_DEFAULT="$HOME/Library/Application Support/Google/Chrome/Default"
CHROME_PROFILES=(
    "$CHROME_DEFAULT"
    "$HOME/Library/Application Support/Google/Chrome/Profile 3"
    "$HOME/Library/Application Support/Google/Chrome/Default"
    "$HOME/Library/Application Support/Google/Chrome/Profile 1"
    "$HOME/Library/Application Support/Google/Chrome/Profile 4"
)

EXTRACTED=false
for PROFILE_PATH in "${CHROME_PROFILES[@]}"; do
    if [ ! -d "$PROFILE_PATH" ]; then
        continue
    fi
    
    PROFILE_NAME=$(basename "$PROFILE_PATH")
    echo "   尝试 Chrome profile: $PROFILE_NAME ..."
    
    .venv/bin/yt-dlp \
        --cookies-from-browser "chrome:$PROFILE_PATH" \
        --cookies "$COOKIES_FILE" \
        --skip-download \
        --quiet \
        "https://www.douyin.com/" 2>&1 | grep -v "ERROR\|WARNING" || true
    
    # 检查是否有 douyin sessionid
    if [ -f "$COOKIES_FILE" ] && grep -q "sessionid" "$COOKIES_FILE" && grep -q "douyin.com" "$COOKIES_FILE"; then
        DOUYIN_COUNT=$(grep -c "douyin.com" "$COOKIES_FILE" 2>/dev/null || echo 0)
        echo "✅ 成功！从 $PROFILE_NAME 提取到 $DOUYIN_COUNT 条 douyin.com Cookie (含 sessionid)"
        EXTRACTED=true
        break
    else
        echo "   $PROFILE_NAME 中未找到 douyin sessionid，继续尝试..."
        rm -f "$COOKIES_FILE"
    fi
done

if [ "$EXTRACTED" = false ]; then
    echo "❌ 所有 Chrome profile 均未找到 douyin.com 的登录 Cookie"
    echo ""
    echo "   请确认："
    echo "   1. 已在 Chrome 中打开 https://www.douyin.com/"
    echo "   2. 已完成登录（页面显示你的账号头像）"
    echo "   3. 关闭 Chrome 后重新打开再试（有时需要）"
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
