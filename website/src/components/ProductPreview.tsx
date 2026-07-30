import { useState } from 'react'
import { Icon } from './Icon'

export type PreviewMode = 'live' | 'review'

const previewContent: Record<
  PreviewMode,
  { label: string; eyebrow: string; image: string; alt: string; caption: string }
> = {
  live: {
    label: '会中工作台',
    eyebrow: '连续文字、上下文选择与 Ask AI',
    image: '/product/meeting-copilot-live-0.1.0-20260730.webp',
    alt: 'Meeting Copilot 0.1.0 会中工作台，左侧显示连续会议文字，右侧提供上下文范围和 Ask AI 提问区',
    caption: '连续发言按时间整理成可回顾的上下文，右侧可按最近内容、当前章节或整场会议继续追问。',
  },
  review: {
    label: '会后复盘',
    eyebrow: '纪要、风险、问题与行动项',
    image: '/product/meeting-copilot-review-0.1.0-20260730.webp',
    alt: 'Meeting Copilot 0.1.0 会后复盘界面，展示会议摘要、关键结论、风险、未解问题、AI 建议与证据片段',
    caption: '会后在同一页查看摘要、结论、风险、未解问题、下一步与对应证据片段。',
  },
}

type ProductArtworkProps = {
  mode?: PreviewMode
  compact?: boolean
}

export function ProductArtwork({ mode = 'live', compact = false }: ProductArtworkProps) {
  const content = previewContent[mode]

  return (
    <div className={`product-artwork product-artwork--${mode}${compact ? ' product-artwork--compact' : ''}`}>
      <img
        src={content.image}
        alt={content.alt}
        width="1440"
        height="900"
        loading={compact ? 'eager' : 'lazy'}
        fetchPriority={compact ? 'high' : 'auto'}
        decoding="async"
      />
    </div>
  )
}

export function ProductPreview() {
  const [mode, setMode] = useState<PreviewMode>('review')
  const content = previewContent[mode]

  return (
    <section className="product-preview section" id="product-preview" aria-labelledby="preview-title">
      <div className="container">
        <div className="section-heading section-heading--split" data-reveal>
          <div>
            <span className="eyebrow">0.1.0 产品界面</span>
            <h2 id="preview-title">不是录完再整理，而是在会议还进行时工作。</h2>
          </div>
          <p>截图使用脱敏演示数据。切换视图查看会中和会后的信息层级；官网本身不连接语音或模型服务。</p>
        </div>

        <div className="preview-switcher" role="tablist" aria-label="产品视图" data-reveal>
          {(Object.keys(previewContent) as PreviewMode[]).map((previewMode) => (
            <button
              key={previewMode}
              id={`preview-tab-${previewMode}`}
              role="tab"
              type="button"
              aria-selected={mode === previewMode}
              aria-controls="preview-panel"
              className={mode === previewMode ? 'is-active' : ''}
              onClick={() => setMode(previewMode)}
            >
              <Icon name={previewMode === 'live' ? 'audio-lines' : 'history'} size={18} />
              <span>{previewContent[previewMode].label}</span>
            </button>
          ))}
        </div>

        <div
          className="preview-frame"
          id="preview-panel"
          role="tabpanel"
          aria-labelledby={`preview-tab-${mode}`}
          data-reveal
        >
          <div className="preview-frame__bar" aria-hidden="true">
            <span />
            <span />
            <span />
            <p>{content.eyebrow}</p>
            <em>CURRENT BUILD</em>
          </div>
          <ProductArtwork key={mode} mode={mode} />
        </div>
        <div className="preview-caption" data-reveal>
          <p>{content.caption}</p>
          <span>演示内容不包含真实用户数据 · 实际界面会随公开预览版持续调整</span>
        </div>
      </div>
    </section>
  )
}
