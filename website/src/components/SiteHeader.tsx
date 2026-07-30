import { useRef } from 'react'
import { Brand } from './Brand'
import { Icon } from './Icon'
import { site } from '../content/site'

function closeDialog(dialog: HTMLDialogElement | null) {
  if (dialog?.open) dialog.close()
}

export function SiteHeader() {
  const menuRef = useRef<HTMLDialogElement>(null)

  return (
    <header className="site-header">
      <div className="site-header__inner">
        <div className="site-header__identity">
          <Brand />
        </div>

        <nav className="desktop-nav" aria-label="主导航">
          {site.navigation.map((item) => (
            <a key={item.href} href={item.href}>
              {item.label}
            </a>
          ))}
        </nav>

        <div className="site-header__actions">
          <a className="button button--primary button--small desktop-action" href={site.windowsDownloadUrl}>
            <Icon name="download" size={16} />
            下载 Windows 版
          </a>
          <a className="button button--outline button--small desktop-action" href={site.demoUrl}>
            产品界面
          </a>
          <button
            className="icon-button mobile-menu-trigger"
            type="button"
            aria-label="打开导航"
            onClick={() => menuRef.current?.showModal()}
          >
            <Icon name="menu" size={22} />
          </button>
        </div>
      </div>

      <dialog
        ref={menuRef}
        className="mobile-menu"
        aria-label="移动端导航"
        onClick={(event) => {
          if (event.target === menuRef.current) closeDialog(menuRef.current)
        }}
      >
        <div className="mobile-menu__panel">
          <div className="mobile-menu__head">
            <Brand compact />
            <button
              className="icon-button"
              type="button"
              aria-label="关闭导航"
              onClick={() => closeDialog(menuRef.current)}
            >
              <Icon name="x" size={22} />
            </button>
          </div>
          <nav aria-label="移动端主导航">
            {site.navigation.map((item) => (
              <a key={item.href} href={item.href} onClick={() => closeDialog(menuRef.current)}>
                <span>{item.label}</span>
                <Icon name="arrow-right" size={18} />
              </a>
            ))}
          </nav>
          <div className="mobile-menu__actions">
            <a className="button button--primary" href={site.windowsDownloadUrl} onClick={() => closeDialog(menuRef.current)}>
              <Icon name="download" size={17} />
              下载 Windows 版
            </a>
            <a className="button button--outline" href={site.demoUrl} onClick={() => closeDialog(menuRef.current)}>
              查看产品界面
            </a>
          </div>
        </div>
      </dialog>
    </header>
  )
}
