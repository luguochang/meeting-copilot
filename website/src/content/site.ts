export type SiteLink = {
  label: string
  href: string
}

export type Scenario = {
  id: 'architecture' | 'release' | 'incident'
  label: string
  title: string
  description: string
  question: string
  evidence: string
  gaps: string[]
}

const configuredTrialUrl = import.meta.env.VITE_TRIAL_URL?.trim()

export const site = {
  name: 'Meeting Copilot',
  chineseName: '会议助手',
  publicUrl: 'https://meeting.codexai.club/',
  stage: 'Windows 0.1.0 · 公开预览',
  description:
    'Meeting Copilot 是本地优先的中文会议助手，把连续转写整理成可阅读上下文，并在会中维护当前议题、建议和未闭环问题。',
  windowsDownloadUrl:
    'https://github.com/luguochang/meeting-copilot/releases/download/v0.1.0/Meeting-Copilot-0.1.0-windows-x64-base-unsigned.exe',
  windowsChecksumUrl:
    'https://github.com/luguochang/meeting-copilot/releases/download/v0.1.0/Meeting-Copilot-0.1.0-windows-x64-base-unsigned.exe.sha256',
  trialUrl: configuredTrialUrl || '/docs#release',
  trialLabel: configuredTrialUrl ? '提交反馈' : '查看发布说明',
  demoUrl: import.meta.env.VITE_DEMO_URL || '/#product-preview',
  navigation: [
    { label: '产品', href: '/#product' },
    { label: '解决方案', href: '/#workflow' },
    { label: '使用场景', href: '/#scenarios' },
    { label: '文档', href: '/docs' },
    { label: '更新日志', href: '/changelog' },
  ] satisfies SiteLink[],
  externalLinks: {
    repository: 'https://github.com/luguochang/meeting-copilot',
    blog: 'https://blog.csdn.net/luguochang',
    sponsor: 'https://codexai.club/',
  },
}

export const meetingGaps = [
  {
    icon: 'user-round',
    title: '缺少明确 Owner',
    detail: '责任不清，没人跟进',
  },
  {
    icon: 'calendar-clock',
    title: '没有截止日期',
    detail: '无限期搁置，优先级被稀释',
  },
  {
    icon: 'shield-check',
    title: '验证标准模糊',
    detail: '只说“做了”，没有验收条件',
  },
  {
    icon: 'radar',
    title: '缺少监控与信号',
    detail: '上线后风险才暴露',
  },
  {
    icon: 'history',
    title: '缺少回滚条件',
    detail: '异常时没有明确的停止与恢复路径',
  },
]

export const workflowSteps = [
  {
    id: 'capture',
    icon: 'mic',
    shortLabel: '采音',
    title: '从一次明确的开始动作进入会议',
    description: '权限、输入电平和录音状态始终可见，用户可以随时暂停或结束。',
    proof: '本地优先中文识别；远程 ASR 默认不作为依赖。',
  },
  {
    id: 'transcript',
    icon: 'audio-lines',
    shortLabel: '连续文字',
    title: '把片段整理成一条可读的会议正文',
    description: '暂定文字、确认文字和后续校正原位更新，不让内容反复跳动。',
    proof: '只有稳定片段进入正式证据链。',
  },
  {
    id: 'evidence',
    icon: 'scan-search',
    shortLabel: '识别缺口',
    title: '在讨论仍进行时暴露未闭环问题',
    description: '围绕 owner、截止时间、验证、监控和回滚条件维护当前议题。',
    proof: '证据不足时保持待确认，不为了填满界面而生成结论。',
  },
  {
    id: 'question',
    icon: 'message-square-more',
    shortLabel: '证据追问',
    title: '只给出此刻最值得确认的一条建议',
    description: '建议与文字片段绑定，点击即可回到对应时间点和上下文。',
    proof: '用户决定保留、忽略或反馈，产品不替团队做技术裁决。',
  },
  {
    id: 'review',
    icon: 'history',
    shortLabel: '会后复盘',
    title: '让文字、录音和复盘留在同一场会议',
    description: '会议结束后回看议题、未闭环问题、方案风险和对应原话。',
    proof: '本地记录与受控恢复已经进入 Windows 公开预览版。',
  },
]

export const capabilities = [
  {
    icon: 'crosshair',
    number: '01',
    title: '实时缺口雷达',
    description: '在讨论结束前，识别还缺 owner、deadline、验证、监控还是回滚条件。',
    metric: '内容优先，不用空计数填满面板',
  },
  {
    icon: 'badge-check',
    number: '02',
    title: '证据化建议',
    description: '建议不是黑盒结论。查看依据即可定位原话、时间点与附近上下文。',
    metric: '建议可保留、忽略并反馈',
  },
  {
    icon: 'text-select',
    number: '03',
    title: '连续会议文字',
    description: '把实时识别与后续校正整理成连续正文，避免重复行和活动尾部抖动。',
    metric: 'partial / final / revision 状态可见',
  },
  {
    icon: 'list-checks',
    number: '04',
    title: '会后复盘',
    description: '纪要、方案与风险、未闭环问题、文字和录音仍属于同一场会议。',
    metric: '生成中、暂停与失败状态不被隐藏',
  },
]

export const scenarios: Scenario[] = [
  {
    id: 'architecture',
    label: '架构评审',
    title: '让架构讨论留下可验证的边界',
    description: '聚焦兼容性、容量、依赖方、灰度和回滚，减少“方案讲完了，工程条件还没对齐”。',
    question: '如何确保幂等设计在超时重试、网络抖动场景下不会产生重复订单？',
    evidence: '15:03:58 · “如果订单创建接口超时重试，可能会重复下单……”',
    gaps: ['幂等键的生成和存储策略', '依赖服务限流与超时阈值', '迁移脚本的向前兼容验证'],
  },
  {
    id: 'release',
    label: '发布评审',
    title: '把“可以上线”拆成可观测的发布条件',
    description: '在发布窗口前确认灰度比例、暂停阈值、关键指标、负责人和回滚路径。',
    question: '是否已经明确灰度从 10% 扩至 30% 的判断指标与自动暂停条件？',
    evidence: '14:26:18 · “先灰度一部分，观察一段时间再全量。”',
    gaps: ['错误率与 P99 的具体阈值', '扩量与回滚操作 Owner', '回滚后数据一致性检查'],
  },
  {
    id: 'incident',
    label: '事故复盘',
    title: '从现象回到根因、信号与后续动作',
    description: '帮助团队区分临时缓解、已验证根因和仍需跟进的工程改进。',
    question: '本次缓解措施是否覆盖了重复触发路径，告警又如何提前暴露同类问题？',
    evidence: '10:42:31 · “扩容后恢复了，但还不能确认为什么连接池会耗尽。”',
    gaps: ['根因验证实验与截止时间', '告警信号和阈值负责人', '同类服务的横向排查范围'],
  },
]

export const trustItems = [
  {
    icon: 'shield-check',
    title: '本地优先处理',
    description: '中文语音识别优先在本地运行，降低不必要的数据外发。',
  },
  {
    icon: 'hand',
    title: '人工始终可控',
    description: '手动开始、暂停和结束；建议由用户确认，不自动替团队执行。',
  },
  {
    icon: 'activity',
    title: '状态清楚可见',
    description: '录音、识别、AI 分析、保存和恢复状态都有明确反馈。',
  },
  {
    icon: 'database-backup',
    title: '记录可以恢复',
    description: '已确认文字、录音任务和会议事实具备受控恢复路径。',
  },
]
