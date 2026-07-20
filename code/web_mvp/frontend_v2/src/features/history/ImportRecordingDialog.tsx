import { Check, FileAudio, HardDrive, LoaderCircle, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ImportRecordingResult } from "../../api/client";
import type { ImportJob, ImportJobStage } from "../../domain/events";

interface ImportRecordingDialogProps {
  open: boolean;
  onClose(): void;
  onImport(file: File, title: string): Promise<ImportRecordingResult>;
  onReadImportJob(meetingId: string): Promise<ImportJob | null>;
  onRetryImport(meetingId: string): Promise<ImportJob>;
  onOpenMeeting(meetingId: string): void;
}

const MAX_FILE_BYTES = 500 * 1024 * 1024;
const SUPPORTED_EXTENSIONS = ["wav", "mp3", "m4a", "aac", "flac", "mp4", "mov"];
const ACCEPTED_FILES = SUPPORTED_EXTENSIONS.map((extension) => `.${extension}`).join(",");

function formatFileSize(bytes: number): string {
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(bytes >= 100 * 1024 ** 2 ? 0 : 1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function extension(file: File): string {
  return file.name.split(".").pop()?.toLowerCase() ?? "";
}

function titleFromFilename(filename: string): string {
  return filename.replace(/\.[^.]+$/, "").trim().slice(0, 200) || "录音导入";
}

function friendlyImportError(error: unknown): string {
  const message = error instanceof Error ? error.message : "录音导入失败";
  if (/offline batch path|funasr.*not ready|component.*not installed/i.test(message)) {
    return "本地文件转写组件未安装或尚未就绪，请先完成本地转写组件安装后重试。";
  }
  if (/format|decode|ffmpeg|codec|无法转换|无法解析/i.test(message)) {
    return "录音格式无法解析，请确认文件未损坏，并改用 WAV、MP3、M4A、AAC、FLAC、MP4 或 MOV。";
  }
  if (/timeout|超时/i.test(message)) return "本地转写超时，原文件未丢失，可以稍后重试。";
  if (/503|persistent data|directory|disk|空间/i.test(message)) return "本地会议服务或存储暂不可用，请检查服务状态和磁盘空间。";
  return message;
}

const stages: Array<{ stage: ImportJobStage; label: string }> = [
  { stage: "reading", label: "读取文件" },
  { stage: "normalizing", label: "标准化转换" },
  { stage: "transcribing", label: "本地中文转写" },
  { stage: "correcting", label: "文字校正" },
  { stage: "reviewing", label: "会后整理" },
];

function activeStageIndex(stage: ImportJobStage | undefined): number {
  if (stage === "completed") return stages.length;
  return Math.max(0, stages.findIndex((item) => item.stage === stage));
}

export function ImportRecordingDialog({
  open,
  onClose,
  onImport,
  onReadImportJob,
  onRetryImport,
  onOpenMeeting,
}: ImportRecordingDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportRecordingResult | null>(null);

  const meetingId = result?.meetingId;
  const jobStatus = result?.job?.status;
  useEffect(() => {
    if (!open || !meetingId || !jobStatus || ["succeeded", "failed", "cancelled"].includes(jobStatus)) return;
    let disposed = false;
    const read = async () => {
      try {
        const job = await onReadImportJob(meetingId);
        if (!disposed && job) setResult((current) => current ? { ...current, job } : current);
      } catch (readError) {
        if (!disposed) setError(friendlyImportError(readError));
      }
    };
    void read();
    const timer = window.setInterval(() => void read(), 2_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [jobStatus, meetingId, onReadImportJob, open]);

  if (!open) return null;

  const chooseFile = (nextFile: File | undefined) => {
    if (!nextFile) return;
    setResult(null);
    setError(null);
    if (!SUPPORTED_EXTENSIONS.includes(extension(nextFile))) {
      setFile(null);
      setError("暂不支持此格式，请选择 WAV、MP3、M4A、AAC、FLAC、MP4 或 MOV。");
      return;
    }
    if (nextFile.size <= 0) {
      setFile(null);
      setError("录音文件为空");
      return;
    }
    if (nextFile.size > MAX_FILE_BYTES) {
      setFile(null);
      setError("文件超过 500MB 限制，请缩短录音或分段导入。");
      return;
    }
    setFile(nextFile);
    setTitle(titleFromFilename(nextFile.name));
  };

  const submit = async () => {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const imported = await onImport(file, title.trim() || titleFromFilename(file.name));
      setResult(imported);
      const backgroundActive = imported.job && !["succeeded", "failed", "cancelled"].includes(imported.job.status);
      if (imported.meetingId && !backgroundActive) onOpenMeeting(imported.meetingId);
    } catch (importError) {
      setError(friendlyImportError(importError));
    } finally {
      setBusy(false);
    }
  };

  const progress = result?.job?.progress;
  const currentStage = activeStageIndex(result?.job?.stage);
  const retry = async () => {
    if (!result?.meetingId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const job = await onRetryImport(result.meetingId);
      setResult((current) => current ? { ...current, job } : current);
    } catch (retryError) {
      setError(friendlyImportError(retryError));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="drawer-layer import-recording-layer" role="presentation">
      <button className="drawer-scrim" type="button" aria-label="关闭录音导入" onClick={onClose} />
      <section className="import-recording-dialog" role="dialog" aria-modal="true" aria-labelledby="import-recording-title">
        <header className="drawer-header">
          <div>
            <span className="eyebrow">本地处理</span>
            <h2 id="import-recording-title">导入录音</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭录音导入" title="关闭">
            <X size={18} />
          </button>
        </header>

        <div className="import-recording-body">
          <div className="import-boundary-note">
            <HardDrive size={18} />
            <p>录音只在本机读取、转换和转写，不新增远程 ASR 费用。AI 会后整理继续遵循当前 Provider 设置。</p>
          </div>

          <button
            className="import-drop-zone"
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              chooseFile(event.dataTransfer.files[0]);
            }}
            disabled={busy}
          >
            <Upload size={22} />
            <strong>{file ? "更换录音文件" : "选择或拖入录音文件"}</strong>
            <span>WAV、MP3、M4A、AAC、FLAC、MP4、MOV · 最大 500MB</span>
          </button>
          <input
            ref={inputRef}
            className="sr-only"
            type="file"
            accept={ACCEPTED_FILES}
            onChange={(event) => chooseFile(event.target.files?.[0])}
            aria-label="选择要导入的录音文件"
          />

          {file ? (
            <div className="import-file-summary">
              <FileAudio size={18} />
              <div><strong>{file.name}</strong><span>{formatFileSize(file.size)}</span></div>
              <Check size={17} />
            </div>
          ) : null}

          {file ? (
            <label className="import-title-field">
              <span>会议名称</span>
              <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} disabled={busy} />
            </label>
          ) : null}

          <ol className="import-stage-list" aria-label="录音导入步骤">
            {stages.map(({ stage, label }, index) => (
              <li
                key={stage}
                className={result?.job
                  ? index < currentStage ? "is-complete" : index === currentStage ? "is-active" : ""
                  : busy && index === 0 ? "is-active" : ""}
              >
                <span>{index + 1}</span>{label}
              </li>
            ))}
          </ol>

          {busy ? (
            <div className="import-progress" role="status">
              <LoaderCircle className="spin" size={17} />
              <div><strong>正在读取文件并启动本地任务</strong><span>窗口可关闭，已提交的后台任务会继续处理。</span></div>
            </div>
          ) : null}
          {result?.job ? (
            <div className="import-progress import-progress--ready" role="status">
              {["failed", "cancelled"].includes(result.job.status)
                ? <FileAudio size={17} />
                : result.job.status === "succeeded" ? <Check size={17} /> : <LoaderCircle className="spin" size={17} />}
              <div>
                <strong>
                  {result.job.status === "succeeded"
                    ? "录音导入完成"
                    : ["failed", "cancelled"].includes(result.job.status)
                      ? "录音导入需要处理"
                      : `${stages[currentStage]?.label ?? "准备处理"}${typeof progress === "number" ? ` · ${Math.round(progress)}%` : ""}`}
                </strong>
                <span>
                  {result.job.errorMessage
                    || (result.job.status === "succeeded"
                      ? "录音、文字和会后内容已经保存。"
                      : "后台任务会继续运行，关闭窗口不会中断处理。")}
                </span>
              </div>
            </div>
          ) : null}
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
        </div>

        <footer className="import-recording-actions">
          <button className="secondary-button" type="button" onClick={onClose}>{result?.job ? "返回会议列表" : "取消"}</button>
          {result?.meetingId ? (
            <>
              {result.job?.retryable && ["failed", "cancelled"].includes(result.job.status) ? (
                <button className="secondary-button" type="button" onClick={() => void retry()} disabled={busy}>
                  {busy ? <LoaderCircle className="spin" size={16} /> : null}
                  重试导入
                </button>
              ) : null}
              <button className="primary-button" type="button" onClick={() => onOpenMeeting(result.meetingId!)}>打开会议</button>
            </>
          ) : (
            <button className="primary-button" type="button" onClick={() => void submit()} disabled={!file || busy}>
              {busy ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
              {busy ? "正在导入" : "开始导入"}
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}
