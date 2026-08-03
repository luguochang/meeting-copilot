import { useEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { SiteFooter } from './SiteFooter'
import { SiteHeader } from './SiteHeader'
import { site } from '../content/site'

const pageMetadata: Record<string, { title: string; description: string }> = {
  '/': {
    title: '言迹 Talktrace · 本地优先的 AI 会议工作台',
    description: '让每一次技术会议，都更接近一个可执行的决定。',
  },
  '/docs': {
    title: '产品文档 · 言迹 Talktrace',
    description: '了解言迹 Talktrace 当前能力、OpenAI-compatible 分析服务、隐私与发布边界。',
  },
  '/changelog': {
    title: '更新日志 · 言迹 Talktrace',
    description: '查看言迹 Talktrace 的产品、体验与官网更新。',
  },
}

function PageMetadata() {
  const { pathname } = useLocation()

  useEffect(() => {
    const metadata = pageMetadata[pathname] || {
      title: '页面未找到 · 言迹 Talktrace',
      description: '言迹 Talktrace 产品网站。',
    }
    const canonicalPath = pageMetadata[pathname] ? pathname : '/'
    const canonicalUrl = new URL(canonicalPath, site.publicUrl).toString()
    document.title = metadata.title
    document.querySelector('meta[name="description"]')?.setAttribute('content', metadata.description)
    document.querySelector('link[rel="canonical"]')?.setAttribute('href', canonicalUrl)
    document.querySelector('meta[property="og:url"]')?.setAttribute('content', canonicalUrl)
    document.querySelector('meta[property="og:title"]')?.setAttribute('content', metadata.title)
    document.querySelector('meta[property="og:description"]')?.setAttribute('content', metadata.description)
    document.querySelector('meta[name="twitter:title"]')?.setAttribute('content', metadata.title)
    document.querySelector('meta[name="twitter:description"]')?.setAttribute('content', metadata.description)
  }, [pathname])

  return null
}

function ScrollManager() {
  const { pathname, hash } = useLocation()

  useEffect(() => {
    if (hash) {
      window.requestAnimationFrame(() => {
        document.querySelector(hash)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
      return
    }
    window.scrollTo({ top: 0 })
  }, [pathname, hash])

  return null
}

export function SiteLayout() {
  return (
    <div className="site-shell">
      <ScrollManager />
      <PageMetadata />
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <SiteHeader />
      <Outlet />
      <SiteFooter />
    </div>
  )
}
