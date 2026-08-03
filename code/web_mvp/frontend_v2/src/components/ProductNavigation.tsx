import {
  LayoutDashboard,
  Moon,
  NotebookPen,
  PackageCheck,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  Sun,
  Video,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { BrandMark } from "./BrandMark";

type NavigationPage = "meetings" | "live" | "notes" | "capabilities";

interface ProductNavigationProps {
  active: NavigationPage;
  onOpenMeetings?: () => void;
  onOpenNotes?: () => void;
  onOpenCapabilities?: () => void;
}

interface NavigationItemProps {
  active: boolean;
  label: string;
  icon: LucideIcon;
  onClick?: () => void;
}

type NavigationTheme = "light" | "dark";

function readNavigationTheme(): NavigationTheme {
  try {
    return window.localStorage.getItem("meeting-copilot-navigation-theme") === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function readNavigationCollapsed(): boolean {
  try {
    return window.localStorage.getItem("meeting-copilot-navigation-collapsed") === "true";
  } catch {
    return false;
  }
}

function NavigationItem({ active, label, icon: Icon, onClick }: NavigationItemProps) {
  const className = `product-nav-item${active ? " is-active" : ""}`;
  const content = (
    <>
      <Icon size={21} strokeWidth={1.8} aria-hidden="true" />
      <span className="sr-only">{label}{active ? "，当前页面" : ""}</span>
      <span className="product-nav-label" aria-hidden="true">{label}</span>
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

export function ProductNavigation({
  active,
  onOpenMeetings,
  onOpenNotes,
  onOpenCapabilities,
}: ProductNavigationProps) {
  const [theme, setTheme] = useState<NavigationTheme>(readNavigationTheme);
  const [collapsed, setCollapsed] = useState(readNavigationCollapsed);

  useEffect(() => {
    try {
      window.localStorage.setItem("meeting-copilot-navigation-theme", theme);
    } catch {
      // The navigation still works when browser storage is unavailable.
    }
  }, [theme]);

  useEffect(() => {
    try {
      window.localStorage.setItem("meeting-copilot-navigation-collapsed", String(collapsed));
    } catch {
      // The navigation still works when browser storage is unavailable.
    }
  }, [collapsed]);

  return (
    <aside
      className={`product-navigation product-navigation--${active} product-navigation--${theme}${collapsed ? " is-collapsed" : ""}`}
      aria-label="产品导航"
      data-theme={theme}
      data-collapsed={collapsed ? "true" : "false"}
    >
      <div className="product-nav-brand">
        <BrandMark size="navigation" tone={theme === "dark" ? "white" : "color"} />
        <div className="product-nav-brand-copy" aria-hidden="true">
          <strong>言迹 <small>Talktrace</small></strong>
          <span>本地会议工作台</span>
        </div>
        <span className="sr-only">言迹 Talktrace 本地会议工作台</span>
      </div>

      <nav className="product-nav-primary" aria-label="主要模块">
        <NavigationItem
          active={active === "meetings"}
          label="会议记录"
          icon={LayoutDashboard}
          onClick={active === "meetings" ? undefined : onOpenMeetings}
        />
        <NavigationItem
          active={active === "notes"}
          label="笔记"
          icon={NotebookPen}
          onClick={active === "notes" ? undefined : onOpenNotes}
        />
        <NavigationItem
          active={active === "capabilities"}
          label="离线能力"
          icon={PackageCheck}
          onClick={active === "capabilities" ? undefined : onOpenCapabilities}
        />
        {active === "live" ? <NavigationItem active label="当前会议" icon={Video} /> : null}
      </nav>

      <div className="product-nav-controls" aria-label="侧栏显示设置">
        <button
          className="product-nav-control"
          type="button"
          onClick={() => setTheme((current) => current === "light" ? "dark" : "light")}
          aria-label={theme === "light" ? "切换为深色侧栏" : "切换为浅色侧栏"}
          title={theme === "light" ? "深色侧栏" : "浅色侧栏"}
        >
          {theme === "light" ? <Moon size={17} aria-hidden="true" /> : <Sun size={17} aria-hidden="true" />}
          <span className="product-nav-control-label">{theme === "light" ? "深色侧栏" : "浅色侧栏"}</span>
        </button>
        <button
          className="product-nav-control"
          type="button"
          onClick={() => setCollapsed((current) => !current)}
          aria-label={collapsed ? "展开侧栏" : "收起侧栏为图标"}
          title={collapsed ? "展开侧栏" : "收起侧栏"}
          aria-expanded={!collapsed}
        >
          {collapsed ? <PanelLeftOpen size={17} aria-hidden="true" /> : <PanelLeftClose size={17} aria-hidden="true" />}
          <span className="product-nav-control-label">{collapsed ? "展开侧栏" : "收起侧栏"}</span>
        </button>
      </div>

      <section className="product-nav-security" aria-label="本地数据说明">
        <ShieldCheck size={18} aria-hidden="true" />
        <div>
          <strong>本地数据 · 由你控制</strong>
          <p>录音默认留在本机；启用远程 AI 后会发送分析所需文字。</p>
        </div>
      </section>

      <div className="product-nav-user" aria-label="本地工作区，单用户模式">
        <span className="product-nav-avatar" aria-hidden="true">本</span>
        <div>
          <strong>本地工作区</strong>
          <span><i aria-hidden="true" />单用户模式</span>
        </div>
      </div>
    </aside>
  );
}
