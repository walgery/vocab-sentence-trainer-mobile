# 英语生词造句复习器（移动版）

手机 / 平板优化的 PWA 版本。与桌面版功能一致：导入生词 → AI 分批造句 → 卡片复习。
支持「添加到主屏幕」，图标 + 全屏，接近原生 APP 体验。

## 本地运行

```bash
cd vocab-sentence-trainer-mobile
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HOME="$PWD/.streamlit_home" .venv/bin/streamlit run app.py --server.headless true
```

手机与电脑连同一 WiFi，浏览器打开 `http://<电脑IP>:8501`，
Chrome 菜单 →「添加到主屏幕」即可当 APP 使用。

## 部署到公网（随时随地可用）

推荐 **Streamlit Community Cloud**（免费）：

1. 把本目录推送到 GitHub 仓库
2. 打开 https://share.streamlit.cloud 用 GitHub 登录
3. 「New app」选择仓库、分支、`app.py`，点击 Deploy
4. 部署完成后得到 `https://<你的应用>.streamlit.app`，手机浏览器打开并「添加到主屏幕」

### 云端配置大模型密钥（二选一）

- **推荐**：应用页左下角 Manage app → Settings → Secrets，填入：

  ```toml
  [llm]
  api_key = "你的智谱 API Key"
  base_url = "https://open.bigmodel.cn/api/paas/v4"
  model = "glm-5.3-flash"
  access_code = "自定义访问口令"
  ```

  `access_code` 是可选的**访问口令**：配置后打开应用需先输入口令，
  防止知道链接的人消耗你的 API 额度。不想启用就删掉这一行。

- 或在「设置」侧边栏里填入 API 密钥后点「保存配置」（存于服务端实例，
  网页上不可见；免费版应用休眠重启后需重新输入）。

> ⚠️ 公网部署后任何知道链接的人都能访问。个人使用建议在 Secrets 里配置，
> 并避免把链接公开分享。

## 与桌面版的差异

- 界面按手机竖屏优化：按钮加高、卡片全宽、侧边栏默认收起
- 新增 PWA manifest + 图标（`static/`），可安装到主屏幕
- 逻辑层（`src/`）与桌面版完全一致
- 不含 `samples/` 示例目录（功能已移除，直接导入自己的文件即可）
