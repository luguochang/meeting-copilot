export type ChangeLogEntry = {
  date: string
  version: string
  status: '公开预览' | '网站' | '产品'
  title: string
  summary: string
  items: string[]
}

export const changelog: ChangeLogEntry[] = [
  {
    date: '2026-07-30',
    version: 'Workbench UI Update',
    status: '产品',
    title: '工作台视觉与状态反馈统一',
    summary: '会议历史、会中工作台、会后复盘、笔记和设置使用统一的内容层级、状态颜色与交互窗口。',
    items: [
      '强化进行中、已完成、需处理和失败状态的颜色区分',
      '统一会议预检、录音导入和服务配置窗口',
      '补齐桌面、平板和窄屏布局检查',
    ],
  },
  {
    date: '2026-07-27',
    version: 'Meeting Copilot 0.1.0',
    status: '公开预览',
    title: 'Windows 基础安装包进入公开预览',
    summary: '首版采用轻量客户端与完整离线能力包分离交付，官网、下载清单、品牌图标和用户文档同步更新。',
    items: [
      'Windows 10/11 x64 基础安装包与 SHA-256 校验文件由官网提供',
      '完整离线 ASR 能力包通过 .mcpkg 文件在客户端内校验、导入和激活',
      'macOS 标记为准备中，公网 Web 应用暂不开放',
      '当前安装包未签名，仍属于公开预览版而非生产稳定版',
    ],
  },
  {
    date: '2026-07-18',
    version: 'Website Preview 0.1',
    status: '网站',
    title: '产品官网首版',
    summary: '建立独立于产品运行代码的官网工程，集中呈现产品定位、交互预览、文档和内测入口。',
    items: [
      '完成中文技术会议核心叙事与能力边界整理',
      '加入会中工作台、会后复盘和证据链交互预览',
      '下载入口改为发布清单驱动，未发布时自动回退到申请内测',
    ],
  },
]
