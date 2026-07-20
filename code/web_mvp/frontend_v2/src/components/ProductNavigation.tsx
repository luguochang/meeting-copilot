import { LayoutDashboard, Video, type LucideIcon } from "lucide-react";
import { BrandMark } from "./BrandMark";

type NavigationPage = "meetings" | "live";

interface ProductNavigationProps {
  active: NavigationPage;
  onOpenMeetings?: () => void;
}

interface NavigationItemProps {
  active: boolean;
  label: string;
  icon: LucideIcon;
  onClick?: () => void;
}

function NavigationItem({ active, label, icon: Icon, onClick }: NavigationItemProps) {
  const className = `product-nav-item${active ? " is-active" : ""}`;
  const content = (
    <>
      <Icon size={21} strokeWidth={1.8} aria-hidden="true" />
      <span className="sr-only">{label}{active ? "，当前页面" : ""}</span>
    </>
  );

  if (active && !onClick) {
    return (
      <div className={className} aria-current="page" data-tooltip={label}>
        {content}
      </div>
    );
  }

  return (
    <button
      className={className}
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      aria-label={label}
      data-tooltip={label}
    >
      {content}
    </button>
  );
}

export function ProductNavigation({ active, onOpenMeetings }: ProductNavigationProps) {
  return (
    <aside className="product-navigation" aria-label="产品导航">
      <div className="product-nav-brand">
        <BrandMark tone="white" size="navigation" />
        <span className="sr-only">Meeting Copilot 会议助手</span>
      </div>

      <nav className="product-nav-primary" aria-label="主要模块">
        <NavigationItem
          active={active === "meetings"}
          label="会议记录"
          icon={LayoutDashboard}
          onClick={active === "meetings" ? undefined : onOpenMeetings}
        />
        {active === "live" ? <NavigationItem active label="当前会议" icon={Video} /> : null}
      </nav>
    </aside>
  );
}
