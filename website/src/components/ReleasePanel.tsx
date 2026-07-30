import { useEffect, useState } from 'react'
import { site } from '../content/site'
import { Icon } from './Icon'

type PlatformRelease = {
  id: string
  label: string
  availability: 'planned' | 'private' | 'public'
  url: string | null
  sha256Url?: string | null
  minimumOs: string
  architecture: string
  signatureStatus?: 'signed' | 'unsigned'
}

type ReleaseManifest = {
  version: string
  status: 'private-preview' | 'public'
  updatedAt: string
  note: string
  platforms: PlatformRelease[]
}

export function ReleasePanel() {
  const [manifest, setManifest] = useState<ReleaseManifest | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${import.meta.env.BASE_URL}releases/latest.json`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('release manifest unavailable')
        return response.json() as Promise<ReleaseManifest>
      })
      .then(setManifest)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setManifest(null)
      })
    return () => controller.abort()
  }, [])

  const publicRelease = manifest?.status === 'public'
  const primaryPlatform = manifest?.platforms.find((platform) => platform.availability === 'public')

  return (
    <div className="release-panel" data-reveal>
      <div className="release-panel__icon">
        <img
          src="/brand/app-icon.png"
          width="112"
          height="112"
          loading="lazy"
          decoding="async"
          alt="Meeting Copilot 应用图标"
        />
      </div>
      <div className="release-panel__copy">
        <span className="eyebrow">{publicRelease ? 'Windows 公开预览' : 'Controlled Preview'}</span>
        <h2>{publicRelease ? '下载 Meeting Copilot' : '先从一场真实技术会议开始内测。'}</h2>
        <p>
          {manifest?.note ||
            '当前正在补齐桌面原生采集、签名、公证和干净机器安装验证。官网先开放产品演示与内测申请。'}
        </p>
        <div className="release-panel__meta">
          <span>版本 {manifest?.version || 'Preview'}</span>
          <span>{manifest?.updatedAt || '持续更新'}</span>
          <span>{primaryPlatform?.signatureStatus === 'unsigned' ? '未签名' : '本地优先'}</span>
        </div>
      </div>
      <div className="release-panel__action">
        {publicRelease && primaryPlatform?.url ? (
          <>
            <a className="button button--primary" href={primaryPlatform.url}>
              <Icon name="download" size={18} />
              下载 {primaryPlatform.label}
            </a>
            {primaryPlatform.sha256Url ? (
              <a className="text-link" href={primaryPlatform.sha256Url}>
                SHA-256 校验文件 <Icon name="arrow-right" size={16} />
              </a>
            ) : null}
          </>
        ) : (
          <a className="button button--primary" href={site.trialUrl}>
            <Icon name="mail" size={18} />
            {site.trialLabel}
          </a>
        )}
        {publicRelease && primaryPlatform?.sha256Url ? null : (
          <a className="text-link" href="/docs#release">
            查看发布边界 <Icon name="arrow-right" size={16} />
          </a>
        )}
      </div>
    </div>
  )
}
