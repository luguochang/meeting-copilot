import {
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  FileArchive,
  FolderOpen,
  HardDrive,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { LocalCapabilityStatus, LocalRuntimeCapability, MeetingApi } from "../../api/client";
import { ProductNavigation, type ActiveMeetingNavigation } from "../../components/ProductNavigation";

interface LocalCapabilitiesProps {
  api: MeetingApi;
  onOpenMeetings: () => void;
  onOpenNotes: () => void;
  onOpenKnowledge?: () => void;
  activeMeeting?: ActiveMeetingNavigation | null;
  onOpenActiveMeeting?: () => void;
}

function safeDownloadUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

const runtimeLabels: Record<LocalRuntimeCapability["state"], string> = {
  ready: "已就绪", starting: "正在加载", idle: "按需启动", available: "可用",
  disabled: "已关闭", unavailable: "未就绪", failed: "运行失败", unknown: "状态未知",
};

const runtimeReasonMessages = new Map<string, string>([
  ["asr_capacity_unavailable", "当前可用资源不足，本次未启动；释放内存后请手动重试。"],
  ["asr_resource_probe_failed", "无法读取本机可用资源，本次离线处理无法完成；请检查运行环境后手动重试。"],
  ["asr_resource_guard_triggered", "本机资源保护触发，本次任务未完成；请稍后手动重试。"],
  ["asr_cold_start_busy", "本机仍有本地处理任务未结束，本次未启动；请稍后手动重试。"],
  ["worker_out_of_memory", "本地模型因内存不足未能运行；释放内存后请手动重试。"],
  ["worker_dependency_load_failed", "本地运行组件加载失败；修复组件后请手动重试。"],
  ["worker_dependency_unavailable", "缺少本地运行组件；补齐组件后请手动重试。"],
  ["worker_start_timeout", "本地模型加载超时，本次加载已停止；请手动重试。"],
  ["worker_spawn_failed", "本地进程未能创建；请检查运行环境后手动重试。"],
  ["worker_exited_before_ready", "本地进程在就绪前退出；请检查运行环境后手动重试。"],
  ["worker_exited", "本地进程已退出；请检查运行环境后手动重试。"],
  ["worker_write_failed", "本地进程连接已中断；请手动重试。"],
  ["worker_timeout", "本地处理超时；请手动重试。"],
  ["worker_reported_failure", "上次本地处理失败；请检查运行环境后手动重试。"],
  ["worker_loading", "本地模型正在加载，完成前尚未就绪。"],
  ["worker_starts_on_demand", "本地组件已配置，模型尚未加载，使用时按需启动。"],
  ["runtime_components_unavailable", "所需本地组件缺失或不可用。"],
  ["runtime_status_check_failed", "暂时无法读取运行状态，请稍后重新检查。"],
  ["disabled_by_resource_policy", "当前策略未启用实时离线精修。"],
]);

function runtimeReasonMessage(capability: LocalRuntimeCapability | undefined): string | null {
  if (!capability) return null;
  if (capability.reason) return runtimeReasonMessages.get(capability.reason) ?? "运行原因暂未识别，请检查本地运行状态。";
  if (capability.state === "failed") return runtimeReasonMessages.get("worker_reported_failure")!;
  if (capability.state === "unknown") return runtimeReasonMessages.get("runtime_status_check_failed")!;
  return null;
}

export function LocalCapabilities({
  api,
  onOpenMeetings,
  onOpenNotes,
  onOpenKnowledge,
  activeMeeting,
  onOpenActiveMeeting,
}: LocalCapabilitiesProps) {
  const [status, setStatus] = useState<LocalCapabilityStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    if (!api.getLocalCapabilities) {
      setError("当前版本不支持离线能力包管理");
      setLoading(false);
      return () => controller.abort();
    }
    void api.getLocalCapabilities(controller.signal)
      .then(setStatus)
      .catch(() => {
        if (!controller.signal.aborted) {
          setError("离线能力状态加载失败，请稍后重新检查");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api]);

  const chooseFile = (file: File | null) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".mcpkg")) {
      setSelectedFile(null);
      setError("请选择 .mcpkg 离线完整包");
      return;
    }
    setSelectedFile(file);
    setError(null);
  };

  const importPackage = async () => {
    if (!selectedFile || !api.importLocalCapabilityPackage || importing) return;
    setImporting(true);
    setError(null);
    try {
      const imported = await api.importLocalCapabilityPackage(selectedFile);
      setStatus(imported);
      if (api.getLocalCapabilities) {
        try {
          setStatus(await api.getLocalCapabilities());
        } catch {
          setError("能力包已导入，当前运行状态检查失败，请重新打开此页面检查");
        }
      }
      setSelectedFile(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch {
      setError("离线能力包导入失败，请检查能力包并重试");
    } finally {
      setImporting(false);
    }
  };

  const downloadUrl = safeDownloadUrl(status?.downloadPageUrl);
  const installed = status?.installed === true;
  const importReady = status?.importAvailable === true && Boolean(api.importLocalCapabilityPackage);
  const runtime = status?.runtimeCapabilities;
  const isUsable = (item: LocalRuntimeCapability) => item.available === true && ["ready", "available"].includes(item.state);
  const usable = runtime && [runtime.realtimeAsr, runtime.fileAsr].every(isUsable);
  const someUsable = runtime && [runtime.realtimeAsr, runtime.fileAsr].some(isUsable);
  const runtimeHasProblem = runtime && [runtime.realtimeAsr, runtime.fileAsr, runtime.offlineRefinement].some((item) => ["failed", "unknown"].includes(item.state));
  const stateLabel = loading ? "正在检查" : !status ? "状态检查失败"
    : runtime ? [runtime.realtimeAsr, runtime.fileAsr].every((item) => item.state === "unknown") ? "状态未知"
      : usable && !runtimeHasProblem ? "本地能力可用" : someUsable ? "部分本地能力可用" : "本地能力未就绪"
    : installed ? "完整能力已安装" : status.baseAppReady ? "基础版可用" : "基础运行时未就绪";
  const legacyLabel = (ready: boolean | undefined) => !status ? "状态未知" : ready
    ? status.restartRequired ? "重启后启用" : "已安装" : "未安装";
  const rows = [
    { name: "实时转写", description: "会议进行中的中文语音识别", capability: runtime?.realtimeAsr, legacyReady: status?.realtimeAsrReady },
    { name: "录音转写", description: "导入本地录音并生成会议文字", capability: runtime?.fileAsr, legacyReady: status?.fileAsrReady },
    ...(runtime ? [{ name: "离线精修", description: "在本机修正实时识别结果", capability: runtime.offlineRefinement, legacyReady: undefined }] : []),
  ];

  return (
    <div className="product-app product-app--capabilities">
      <ProductNavigation
        active="capabilities"
        onOpenMeetings={onOpenMeetings}
        onOpenNotes={onOpenNotes} onOpenKnowledge={onOpenKnowledge}
        activeMeeting={activeMeeting}
        onOpenActiveMeeting={onOpenActiveMeeting}
      />
      <main className="capabilities-shell">
        <header className="capabilities-header">
          <div>
            <span className="eyebrow">本地运行时</span>
            <h1>离线能力</h1>
            <p>查看实际运行能力，管理本地语音识别所需的完整能力包。</p>
          </div>
          <div className={`capabilities-state${installed && !runtimeHasProblem ? " is-installed" : ""}`}>
            {loading ? <LoaderCircle className="spin" size={20} aria-hidden="true" />
              : runtimeHasProblem ? <TriangleAlert size={20} aria-hidden="true" />
              : installed ? <CheckCircle2 size={20} aria-hidden="true" />
                : <HardDrive size={20} aria-hidden="true" />}
            <div>
              <strong>{stateLabel}</strong>
              <span>{runtime ? status?.deploymentMode === "source" ? "当前使用源码配置的本地运行时" : "当前本机运行时"
                : installed ? status?.packageVersion || "版本已校验" : status ? "等待导入完整包" : "请重新打开此页面检查"}</span>
            </div>
          </div>
        </header>

        {status?.restartRequired ? (
          <div className="capabilities-notice capabilities-notice--restart" role="status">
            <RefreshCw size={19} aria-hidden="true" />
            <div>
              <strong>重启后启用完整能力</strong>
              <span>请关闭并重新打开言迹 Talktrace。</span>
            </div>
          </div>
        ) : null}

        {status?.errors.includes("active_runtime_invalid") ? (
          <div className="capabilities-notice capabilities-notice--warning" role="alert">
            <TriangleAlert size={19} aria-hidden="true" />
            <div>
              <strong>已回退到基础运行时</strong>
              <span>原能力包状态无效，请重新导入完整包。</span>
            </div>
          </div>
        ) : null}

        <section className="capabilities-overview" aria-labelledby="capability-overview-title">
          <div className="capabilities-section-heading">
            <div>
              <span className="section-kicker">当前状态</span>
              <h2 id="capability-overview-title">本地处理能力</h2>
            </div>
            <span className="capabilities-local-badge"><ShieldCheck size={15} />仅保存在本机</span>
          </div>
          <div className="capability-rows">
            {rows.map((row) => {
              const ready = row.capability ? ["ready", "available"].includes(row.capability.state) : row.legacyReady === true && !status?.restartRequired;
              const formats = row.capability?.formats && Object.entries(row.capability.formats).filter(([, available]) => available).map(([format]) => format.toUpperCase());
              const reason = runtimeReasonMessage(row.capability);
              return <article key={row.name}>
                {ready ? <CheckCircle2 size={20} /> : <CircleDashed size={20} />}
                <div><strong>{row.name}</strong><span>{row.description}</span>
                  {formats && formats.length > 0 ? <span>支持格式：{formats.join("、")}</span> : null}
                  {reason ? <span role="status">{reason}</span> : null}</div>
                <b data-ready={ready}>{loading ? "正在检查" : row.capability ? runtimeLabels[row.capability.state] : legacyLabel(row.legacyReady)}</b>
              </article>;
            })}
          </div>
        </section>

        <section className="capabilities-import" aria-labelledby="capability-import-title">
          <div className="capabilities-section-heading">
            <div>
              <span className="section-kicker">离线安装</span>
              <h2 id="capability-import-title">导入完整能力包</h2>
              <p>{installed ? `完整能力包已安装${status?.packageVersion ? ` · ${status.packageVersion}` : ""}`
                : status?.deploymentMode === "source" ? "未导入能力包；源码配置的运行能力已在上方独立检查" : "尚未导入完整能力包"}</p>
            </div>
            {downloadUrl ? (
              <a className="secondary-button" href={downloadUrl} target="_blank" rel="noreferrer">
                <ExternalLink size={16} aria-hidden="true" />打开下载页
              </a>
            ) : null}
          </div>

          <div
            className={`capability-dropzone${dragging ? " is-dragging" : ""}${selectedFile ? " has-file" : ""}`}
            onDragEnter={(event) => { event.preventDefault(); if (!importing) setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              if (!importing) chooseFile(event.dataTransfer.files.item(0));
            }}
          >
            <input
              ref={inputRef}
              className="sr-only"
              type="file"
              accept=".mcpkg,application/octet-stream"
              aria-label="选择离线能力包"
              disabled={!importReady || importing}
              onChange={(event) => chooseFile(event.target.files?.item(0) ?? null)}
            />
            <span className="capability-dropzone-icon" aria-hidden="true">
              {selectedFile ? <FileArchive size={24} /> : <Upload size={24} />}
            </span>
            <div className="capability-dropzone-copy">
              <strong>{selectedFile ? selectedFile.name : "选择 .mcpkg 完整包"}</strong>
              <span>{selectedFile ? formatFileSize(selectedFile.size) : "也可以将文件拖放到这里"}</span>
            </div>
            <button
              className="secondary-button"
              type="button"
              disabled={!importReady || importing}
              onClick={() => inputRef.current?.click()}
            >
              <FolderOpen size={16} aria-hidden="true" />选择文件
            </button>
          </div>

          <div className="capability-import-actions">
            <span aria-live="polite">
              {importing ? "正在校验并安装，完成前请勿关闭应用" : !importReady
                ? status?.deploymentMode === "source" ? "源码运行时通过本地开发配置管理" : "请在桌面客户端中导入"
                : "导入过程不会上传文件"}
            </span>
            <button
              className="primary-button"
              type="button"
              disabled={!selectedFile || !importReady || importing}
              onClick={() => void importPackage()}
            >
              {importing ? <LoaderCircle className="spin" size={17} aria-hidden="true" /> : <HardDrive size={17} aria-hidden="true" />}
              {importing ? "正在导入" : "校验并导入"}
            </button>
          </div>
          {error ? <p className="capability-import-error" role="alert"><TriangleAlert size={16} />{error}</p> : null}
        </section>
      </main>
    </div>
  );
}
