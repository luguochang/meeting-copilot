# Meeting Copilot Website

Meeting Copilot 的独立静态官网，使用 Vite、React 和 TypeScript 构建。官网只展示产品信息、截图、文档和发布清单，不连接本地会议数据库，也不保存 AI 服务密钥。

## 本地开发

```bash
npm ci
npm run dev
```

## 构建与检查

```bash
npm run check
npm run preview
```

生产产物位于 `dist/`。

## 公开配置

```bash
VITE_TRIAL_URL=https://your-form.example
VITE_DEMO_URL=/#product-preview
```

`VITE_*` 会进入浏览器可见的 JavaScript，只能填写公开 URL，不能填写 API Key、数据库口令或其他密钥。

下载按钮由 `public/releases/latest.json` 控制。安装包应托管在独立的 HTTPS 下载目录或对象存储，不应复制到官网源码与 `dist/`。

## 内容位置

- 产品文案：`src/content/site.ts`
- 更新日志：`src/content/changelog.ts`
- 产品截图：`public/product/`
- 发布清单：`public/releases/latest.json`
- 品牌资源：`public/brand/`

部署说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。
