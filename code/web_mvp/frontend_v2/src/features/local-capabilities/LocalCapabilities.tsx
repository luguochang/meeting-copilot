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
import type { LocalCapabilityStatus, MeetingApi } from "../../api/client";
import { ProductNavigation } from "../../components/ProductNavigation";

interface LocalCapabilitiesProps {
  api: MeetingApi;
  onOpenMeetings: () => void;
  onOpenNotes: () => void;
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

export function LocalCapabilities({ api, onOpenMeetings, onOpenNotes }: LocalCapabilitiesProps) {
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
      .catch((loadError) => {
        if (!controller.signal.aborted) {
          setError(loadError instanceof Error ? loadError.message : "离线能力状态加载失败");
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
      setStatus(await api.importLocalCapabilityPackage(selectedFile));
      setSelectedFile(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "离线能力包导入失败");
    } finally {
      setImporting(false);
    }
  };

  const downloadUrl = safeDownloadUrl(status?.downloadPageUrl);
  const installed = status?.installed === true;
  const importReady = status?.importAvailable === true && Boolean(api.importLocalCapabilityPackage);

  return (
    <div className="product-app product-app--capabilities">
      <ProductNavigation
        active="capabilities"
        onOpenMeetings={onOpenMeetings}
        onOpenNotes={onOpenNotes}
      />
      <main className="capabilities-shell">
        <header className="capabilities-header">
          <div>
            <span className="eyebrow">本地运行时</span>
            <h1>离线能力</h1>
            <p>管理本地实时转写与录音转写所需的完整能力包。</p>
          </div>
          <div className={`capabilities-state${installed ? " is-installed" : ""}`}>
            {loading ? <LoaderCircle className="spin" size={20} aria-hidden="true" />
              : installed ? <CheckCircle2 size={20} aria-hidden="true" />
                : <HardDrive size={20} aria-hidden="true" />}
            <div>
              <strong>{loading ? "正在检查" : installed ? "完整能力已安装" : "基础版可用"}</strong>
              <span>{installed ? status?.packageVersion || "版本已校验" : "等待导入完整包"}</span>
            </div>
          </div>
        </header>

        {status?.restartRequired ? (
          <div className="capabilities-notice capabilities-notice--restart" role="status">
            <RefreshCw size={19} aria-hidden="true" />
            <div>
              <strong>重启后启用完整能力</strong>
              <span>请关闭并重新打开 Meeting Copilot。</span>
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
            <article>
              {status?.realtimeAsrReady ? <CheckCircle2 size={20} /> : <CircleDashed size={20} />}
              <div><strong>实时转写</strong><span>会议进行中的中文语音识别</span></div>
              <b data-ready={status?.realtimeAsrReady === true}>{status?.realtimeAsrReady ? "已就绪" : "未安装"}</b>
            </article>
            <article>
              {status?.fileAsrReady ? <CheckCircle2 size={20} /> : <CircleDashed size={20} />}
              <div><strong>录音转写</strong><span>导入本地录音并生成会议文字</span></div>
              <b data-ready={status?.fileAsrReady === true}>{status?.fileAsrReady ? "已就绪" : "未安装"}</b>
            </article>
          </div>
        </section>

        <section className="capabilities-import" aria-labelledby="capability-import-title">
          <div className="capabilities-section-heading">
            <div>
              <span className="section-kicker">离线安装</span>
              <h2 id="capability-import-title">导入完整能力包</h2>
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
              {importing ? "正在校验并安装，完成前请勿关闭应用" : !importReady ? "请在桌面客户端中导入" : "导入过程不会上传文件"}
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
