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
    date: '2026-07-31',
    version: 'Windows Installer Update',
    status: '产品',
    title: '安装权限、后台进程与 AI 配置更新',
    summary: 'Windows 客户端改为当前用户安装，隐藏本地后台控制台，并统一 AI 配置与公开项目链接。',
    items: [
      '安装器不再要求管理员权限，也不注册系统服务、开机启动项或防火墙规则',
      '本地后台不再通过命令脚本中转，并随桌面客户端退出',
      '新增 MEETING_COPILOT_STORAGE_DIR，用于指定数据库、录音、日志和能力包目录',
      'AI 设置明确展示赞助商、GitHub 与 CSDN 链接，新配置预填可编辑的赞助商服务地址',
      '公开下载只保留完成安装回归的标准安装程序与校验文件',
    ],
  },
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
    date: '2026-07-30',
    version: 'Meeting Copilot 0.1.0',
    status: '公开预览',
    title: 'Windows 基础安装包进入公开预览',
    summary: '首版采用基础客户端与离线能力包分离交付，官网、下载清单、品牌图标和用户文档同步更新。',
    items: [
      'Windows 10/11 x64 基础安装包与 SHA-256 校验文件通过 GitHub Release 提供',
      '客户端支持校验、导入和激活 .mcpkg 能力包，完整能力包当前尚未公开分发',
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
