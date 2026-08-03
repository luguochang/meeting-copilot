import { Link } from 'react-router-dom'

type BrandProps = {
  inverse?: boolean
  compact?: boolean
}

export function Brand({ inverse = false, compact = false }: BrandProps) {
  return (
    <Link className={`brand${inverse ? ' brand--inverse' : ''}`} to="/" aria-label="言迹 Talktrace 首页">
      <img
        src={inverse ? '/brand/talktrace-mark-white.svg' : '/brand/talktrace-mark.svg'}
        width="40"
        height="40"
        alt=""
      />
      <span className="brand__text">
        <strong>言迹</strong>
        {!compact && <small>TALKTRACE</small>}
      </span>
    </Link>
  )
}
