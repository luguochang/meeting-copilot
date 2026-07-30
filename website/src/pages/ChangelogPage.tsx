import { Icon } from '../components/Icon'
import { changelog } from '../content/changelog'
import { site } from '../content/site'
import { useReveal } from '../hooks/useReveal'

export function ChangelogPage() {
  useReveal()

  return (
    <main className="subpage changelog-page" id="main-content">
      <header className="subpage-hero">
        <div className="container" data-reveal>
          <span className="eyebrow">更新日志</span>
          <h1>
            把已验证、进行中<br />
            和未完成说清楚。
          </h1>
          <p>这里记录公开预览版、官网和产品功能的主要变化；稳定版承诺仍以正式发布说明为准。</p>
        </div>
      </header>
      <div className="container changelog-list">
        {changelog.map((entry) => (
          <article key={`${entry.date}-${entry.version}`} data-reveal>
            <div className="changelog-list__date">
              <time dateTime={entry.date}>{entry.date}</time>
              <span>{entry.status}</span>
            </div>
            <div className="changelog-list__content">
              <span className="eyebrow">{entry.version}</span>
              <h2>{entry.title}</h2>
              <p>{entry.summary}</p>
              <ul>
                {entry.items.map((item) => (
                  <li key={item}>
                    <Icon name="circle-check" size={17} />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </article>
        ))}
      </div>
      <section className="changelog-cta">
        <div className="container" data-reveal>
          <div>
            <span className="eyebrow">Windows 公开预览</span>
            <h2>下载 0.1.0，开始一次本地优先的会议记录。</h2>
          </div>
          <a className="button button--primary" href={site.windowsDownloadUrl}>
            下载 Windows 版 <Icon name="download" size={17} />
          </a>
        </div>
      </section>
    </main>
  )
}
