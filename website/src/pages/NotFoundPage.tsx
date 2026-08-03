import { Icon } from '../components/Icon'

export function NotFoundPage() {
  return (
    <main className="not-found" id="main-content">
      <div>
        <span className="eyebrow">404</span>
        <h1>这个页面还没有形成可追溯的结论。</h1>
        <p>地址可能已变更，回到首页继续了解言迹 Talktrace。</p>
        <a className="button button--primary" href="/">
          <Icon name="home" size={18} /> 返回首页
        </a>
      </div>
    </main>
  )
}
