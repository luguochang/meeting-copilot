import { Icon } from '../components/Icon'
import { ProductArtwork, ProductPreview } from '../components/ProductPreview'
import { ReleasePanel } from '../components/ReleasePanel'
import { ScenarioExplorer } from '../components/ScenarioExplorer'
import { WorkflowOrbit } from '../components/WorkflowOrbit'
import { capabilities, meetingGaps, site, trustItems } from '../content/site'
import { useReveal } from '../hooks/useReveal'

export function HomePage() {
  useReveal()

  return (
    <main id="main-content">
      <section className="hero-section" aria-labelledby="hero-title">
        <div className="container hero-section__inner">
          <div className="hero-copy" data-reveal>
            <div className="hero-copy__stage">
              <span className="status-dot" />
              {site.stage}
              <a href="/changelog">查看更新</a>
            </div>
            <h1 id="hero-title">Meeting Copilot</h1>
            <p className="hero-copy__promise">让每一次技术会议，都更接近一个可执行的决定。</p>
            <p className="hero-copy__description">
              Meeting Copilot 把连续发言整理成可阅读的会议上下文，维护当前议题与未闭环问题，并在还来得及追问时给出可以回到原话的建议。
            </p>
            <div className="hero-copy__actions">
              <a className="button button--primary button--large" href={site.windowsDownloadUrl}>
                <Icon name="download" size={18} />
                下载 Windows 版
              </a>
              <a className="button button--outline button--large" href={site.demoUrl}>
                查看产品界面
                <Icon name="arrow-right" size={18} />
              </a>
            </div>
            <div className="hero-trust" aria-label="产品信任点">
              <span>
                <Icon name="shield-check" size={16} /> 本地优先中文识别
              </span>
              <span>
                <Icon name="hand" size={16} /> 建议由人确认
              </span>
              <span>
                <Icon name="database-backup" size={16} /> 会议记录可恢复
              </span>
            </div>
          </div>

          <div className="hero-visual" data-reveal>
            <div className="hero-visual__chrome" aria-hidden="true">
              <span />
              <span />
              <span />
              <p>当前版本 · 会后复盘</p>
            </div>
            <ProductArtwork mode="review" compact />
          </div>
        </div>
        <div className="hero-section__foot container" data-reveal>
          <span>为技术负责人、会议主持人和工程团队设计</span>
          <a href="#problem">
            看看会议里经常漏掉什么 <Icon name="arrow-right" size={16} />
          </a>
        </div>
      </section>

      <section className="gap-section" id="problem" aria-labelledby="problem-title">
        <div className="container">
          <div className="section-heading section-heading--center" data-reveal>
            <span className="eyebrow">为什么需要会议副驾驶</span>
            <h2 id="problem-title">真正让技术会议失效的，往往不是没记录。</h2>
            <p>而是讨论结束后，关键工程条件仍然没有闭环。</p>
          </div>
          <div className="gap-grid" data-reveal>
            {meetingGaps.map((gap) => (
              <div className="gap-item" key={gap.title}>
                <span className="gap-item__icon">
                  <Icon name={gap.icon} size={22} />
                </span>
                <strong>{gap.title}</strong>
                <p>{gap.detail}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="workflow-section section" id="workflow" aria-labelledby="workflow-title">
        <div className="container">
          <div className="section-heading section-heading--split" data-reveal>
            <div>
              <span className="eyebrow">一个可追溯的会议闭环</span>
              <h2 id="workflow-title">从一句话，到一个值得确认的工程问题。</h2>
            </div>
            <p>从开始会议到会后复盘，转写、证据与处理状态始终属于同一场会议。</p>
          </div>
          <WorkflowOrbit />
        </div>
      </section>

      <section className="capability-section section" id="product" aria-labelledby="capability-title">
        <div className="container">
          <div className="section-heading" data-reveal>
            <span className="eyebrow">核心能力</span>
            <h2 id="capability-title">每一块界面，都回答会议中的一个具体问题。</h2>
            <p>不堆叠泛化的 AI 总结，而是围绕“现在讨论什么、还缺什么、证据在哪里”组织信息。</p>
          </div>
          <div className="capability-grid" data-reveal>
            {capabilities.map((capability) => (
              <article className="capability-card" key={capability.number}>
                <div className="capability-card__top">
                  <span className="capability-card__icon">
                    <Icon name={capability.icon} size={24} />
                  </span>
                  <em>{capability.number}</em>
                </div>
                <h3>{capability.title}</h3>
                <p>{capability.description}</p>
                <span className="capability-card__proof">
                  <Icon name="circle-check" size={16} /> {capability.metric}
                </span>
              </article>
            ))}
          </div>
        </div>
      </section>

      <ProductPreview />

      <section className="scenario-section section" id="scenarios" aria-labelledby="scenario-title">
        <div className="container">
          <div className="section-heading section-heading--center" data-reveal>
            <span className="eyebrow">优先使用场景</span>
            <h2 id="scenario-title">为真实的中文技术会议而设计。</h2>
            <p>产品首先聚焦工程语境清晰、能在现场形成行动价值的会议。</p>
          </div>
          <ScenarioExplorer />
        </div>
      </section>

      <section className="trust-section section" aria-labelledby="trust-title">
        <div className="container">
          <div className="trust-section__heading" data-reveal>
            <span className="eyebrow eyebrow--light">数据与控制</span>
            <h2 id="trust-title">你的会议，你掌控。</h2>
            <p>本地优先不等于夸大“完全离线”。只有在运行环境显式配置并启用后，产品才会调用兼容 OpenAI 协议的远程分析服务。</p>
            <a href="/docs#privacy">
              了解数据边界 <Icon name="arrow-right" size={16} />
            </a>
          </div>
          <div className="trust-grid" data-reveal>
            {trustItems.map((item) => (
              <div className="trust-item" key={item.title}>
                <Icon name={item.icon} size={23} />
                <strong>{item.title}</strong>
                <p>{item.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="release-section section" aria-label="下载与发布状态">
        <div className="container">
          <ReleasePanel />
        </div>
      </section>
    </main>
  )
}
