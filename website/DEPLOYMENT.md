# 官网部署

官网是纯静态 Vite 应用，不需要常驻 Node.js 服务或数据库。

## 构建

```bash
cd website
npm ci
npm run check
```

将 `dist/` 部署到支持 SPA 回退的静态托管平台。推荐 Node.js 22 构建环境，输出目录为 `website/dist`。

## Nginx 示例

```nginx
server {
    listen 80;
    server_name your-domain.example;
    root /var/www/meeting-copilot;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~* \.(?:css|js|woff2|webp|png|ico)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location = /releases/latest.json {
        expires -1;
        add_header Cache-Control "no-cache";
    }
}
```

正式环境应启用 HTTPS。

## 下载清单

`public/releases/latest.json` 是官网展示下载按钮的唯一来源。只有 `status` 和平台 `availability` 均为 `public` 且 URL 非空时才应提供下载。

基础安装资产托管在项目 GitHub Release。大型离线能力包必须先确认全部组件的公开再分发许可，并使用适合大文件的独立托管；不得直接提交到 Git，超过 GitHub Release 单文件限制的资产也不得写入清单。更新清单前应确认：

- 文件名、版本和平台正确
- HTTPS 地址可访问
- SHA-256 文件与安装包一致
- 安装包已经过预期平台的安装验证
- GitHub Release 已创建且公开 URL 可实际下载
- 未发布平台保持不可用状态

## 上线检查

- 运行 `npm run check`。
- 检查桌面与手机布局、站内路由和外部链接。
- 确认 GitHub、CSDN 和 AI 赞助商链接正确。
- 确认页面与构建产物中没有密钥或本地路径。
- 检查 `robots.txt`、站点图标、Open Graph 图片和发布清单。
- 从正式域名实际下载一次公开文件并校验 SHA-256。
