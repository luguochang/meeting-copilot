import { Brand } from './Brand'
import { site } from '../content/site'

const footerGroups = [
  {
    title: '产品',
    links: [
      { label: '核心能力', href: '/#product' },
      { label: '工作方式', href: '/#workflow' },
      { label: '使用场景', href: '/#scenarios' },
    ],
  },
  {
    title: '资源',
    links: [
      { label: '产品文档', href: '/docs' },
      { label: '更新日志', href: '/changelog' },
      { label: site.trialLabel, href: site.trialUrl },
    ],
  },
  {
    title: '项目与支持',
    links: [
      { label: 'GitHub 仓库', href: site.externalLinks.repository },
      { label: 'CSDN 博客', href: site.externalLinks.blog },
      { label: 'AI 赞助商', href: site.externalLinks.sponsor },
    ],
  },
]

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer__inner">
        <div className="site-footer__brand">
          <Brand inverse />
          <p>技术会议的实时副驾驶。让重要问题在散会前被看见。</p>
          <span>{site.stage}</span>
        </div>
        <div className="site-footer__links">
          {footerGroups.map((group) => (
            <div key={group.title}>
              <strong>{group.title}</strong>
              {group.links.map((link) => (
                <a
                  key={link.href}
                  href={link.href}
                  {...(link.href.startsWith('http')
                    ? { target: '_blank', rel: 'noreferrer' }
                    : {})}
                >
                  {link.label}
                </a>
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="site-footer__legal">
        <p>© 2026 luguochang. Meeting Copilot Windows 0.1.0 公开预览版。</p>
        <p>产品截图使用脱敏演示数据，实际功能以安装包为准。</p>
      </div>
    </footer>
  )
}
