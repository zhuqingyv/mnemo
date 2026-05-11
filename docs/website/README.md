# Mnemo 官网

这是 Mnemo 项目的官方网站，部署在 GitHub Pages 上。

## 网站特性

- **智能平台检测**：自动检测用户操作系统和架构
- **一键安装**：根据检测结果提供对应的安装命令
- **实时版本信息**：通过 GitHub API 获取最新版本信息
- **响应式设计**：支持桌面端和移动端
- **完整文档**：包含安装指南和 API 文档

## 技术架构

- **静态网站**：纯 HTML/CSS/JavaScript，无构建依赖
- **GitHub Pages**：免费托管，自动部署
- **GitHub API**：实时获取版本信息
- **自动更新**：发版时自动更新网站内容

## 文件结构

```
docs/website/
├── index.html              # 主页
├── docs/
│   ├── installation.html   # 安装指南
│   └── api.html           # API 文档
├── assets/
│   ├── css/
│   │   ├── style.css      # 主样式
│   │   └── docs.css       # 文档样式
│   ├── js/
│   │   ├── main.js        # 主要逻辑
│   │   └── config.js      # 配置文件（自动生成）
│   └── images/            # 图片资源
└── README.md              # 说明文件
```

## 部署流程

1. **代码推送**：推送到 main 分支时自动部署
2. **版本发布**：发布新版本时自动更新版本信息
3. **手动触发**：可通过 GitHub Actions 手动部署

## 开发指南

### 本地开发

直接在浏览器中打开 `index.html` 文件即可预览网站。

### 修改内容

1. **样式修改**：编辑 `assets/css/style.css`
2. **功能修改**：编辑 `assets/js/main.js`
3. **内容修改**：编辑对应的 HTML 文件

### 添加新页面

1. 创建新的 HTML 文件
2. 添加导航链接
3. 更新 sitemap（如果需要）

## 自动化

网站通过 GitHub Actions 自动部署：

- `.github/workflows/website.yml` - 部署配置
- 自动获取最新版本信息
- 自动生成配置文件
- 自动部署到 GitHub Pages

## 自定义配置

### 主题颜色

在 `assets/css/style.css` 中修改 CSS 变量：

```css
:root {
  --primary-color: #2563eb;
  --secondary-color: #64748b;
  --background-color: #ffffff;
  --text-color: #333333;
}
```

### 平台映射

在 `assets/js/main.js` 中的 `PlatformDetector` 类中修改平台检测逻辑。

## 性能优化

- 使用 CDN 加载字体和图标
- 图片懒加载
- CSS 和 JavaScript 压缩
- 缓存策略

## 浏览器支持

- Chrome 60+
- Firefox 60+
- Safari 12+
- Edge 79+

## 问题反馈

如果网站有问题，请在 [GitHub Issues](https://github.com/zhuqingyv/mnemo/issues) 中反馈。

## 许可证

MIT License - 与主项目保持一致。
