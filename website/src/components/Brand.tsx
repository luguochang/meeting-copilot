import { Link } from 'react-router-dom'

type BrandProps = {
  inverse?: boolean
  compact?: boolean
}

export function Brand({ inverse = false, compact = false }: BrandProps) {
  return (
    <Link className={`brand${inverse ? ' brand--inverse' : ''}`} to="/" aria-label="Meeting Copilot 首页">
      <img
        src={inverse ? '/brand/meeting-copilot-mark-white.png' : '/brand/meeting-copilot-mark.png'}
        width="40"
        height="40"
        alt=""
      />
      <span className="brand__text">
        <strong>Meeting Copilot</strong>
        {!compact && <small>会议助手</small>}
      </span>
    </Link>
  )
}
