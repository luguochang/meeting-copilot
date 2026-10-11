import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FileText, LoaderCircle, Paperclip, X } from "lucide-react";
import type { AttachmentDetail, AttachmentSpan, MaterialsApi, MaterialSet, MeetingAttachment } from "../../api/materials";
import type { MeetingAttachmentSourceEvidence } from "../../domain/events";
import { useModalDialog } from "../../components/useModalDialog";
import { forgetMaterialDraft, readMaterialDraftPointer, rememberMaterialDraft } from "./materialDraftPointer";
import "./meetingAttachments.css";

const WARNINGS: Record<string, string> = {
  docx_images_headers_footers_not_extracted: "图片、页眉和页脚未转换成文字，请核对是否缺少关键内容。",
  pdf_partial_ocr_required: "部分 PDF 页面没有可提取文字；当前仅包含已提取部分，请补充文本版。",
  docx_complex_tables_review_required: "复杂表格已按文字顺序提取，合并或嵌套关系需要人工核对。",
};
export function MeetingAttachments({ api, meetingId, materialSet, onSetChange, onChanged, source, onSourceClosed, onRecoveryPending }: {
  api: MaterialsApi; meetingId: string | null; materialSet?: MaterialSet | null; onSetChange?(set: MaterialSet | null): void;
  onChanged?(): void; source?: MeetingAttachmentSourceEvidence | null; onSourceClosed?(): void; onRecoveryPending?(pending: boolean): void;
}) {
  const serverScope = api.storageScope ?? window.location.origin;
  const [open, setOpen] = useState(false); const [set, setSet] = useState<MaterialSet | null>(materialSet ?? null);
  const [items, setItems] = useState<MeetingAttachment[]>([]); const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<AttachmentDetail | null>(null); const [spans, setSpans] = useState<AttachmentSpan[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null); const [content, setContent] = useState(""); const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false); const [loading, setLoading] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0); const scope = useRef<object>({}); const uploading = useRef(false);
  const recoveryPending = useRef(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [confirmDraftRemoval, setConfirmDraftRemoval] = useState(false);
  const [rangeMode, setRangeMode] = useState<"all" | "selected">("all"); const [selectedSpans, setSelectedSpans] = useState<string[]>([]);
  const sourceAnchor = useRef<HTMLElement | null>(null);
  useEffect(() => { if (open && source) sourceAnchor.current?.scrollIntoView?.({ block: "nearest" }); }, [open, source, spans]);
  const dirty = edit && content !== spans.map((s) => s.text).join("\n\n");
  const selectedItem = items.find((item) => item.id === selected);
  const close = () => { if (busy || dirty) { setError(dirty ? "正文有未保存修改，请保存或取消编辑后关闭。" : "正在保存，请稍候。"); return; } setOpen(false); onSourceClosed?.(); };
  const dialog = useModalDialog(open, close);
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previousOverflow; };
  }, [open]);
  // A set created by this drawer is echoed back through props. That echo must
  // not invalidate the upload it just started; only changing meetings does.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { scope.current = {}; setSet(materialSet ?? null); setItems([]); setDetail(null); setSelected(null); setSpans([]); setEdit(false); setBusy(false); setConfirmRemove(false); setError(""); return () => { scope.current = {}; }; }, [meetingId]);
  useEffect(() => { if (materialSet) { setSet(materialSet); if (materialSet.state !== "draft") forgetMaterialDraft(serverScope, materialSet.id); } }, [materialSet, serverScope]);
  useEffect(() => { if (source?.meetingId === meetingId) { setSelected(source.attachmentId); setOpen(true); } }, [source, meetingId]);
  const install = (value: MaterialSet) => { setSet(value); onSetChange?.(value); if (value.state === "draft" && value.meeting_id === null) rememberMaterialDraft(serverScope, value.id); else forgetMaterialDraft(serverScope, value.id); };
  useEffect(() => {
    if (meetingId || materialSet) return;
    const id = readMaterialDraftPointer(serverScope);
    if (!id) return;
    const controller = new AbortController();
    let recovered = false;
    recoveryPending.current = true;
    setLoading(true);
    onRecoveryPending?.(true);
    let timer: ReturnType<typeof setTimeout>;
    const timeout = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("读取会前资料超时，草稿编号仍保留，可稍后刷新资料状态")), 6_000); });
    void Promise.race([api.get(id, controller.signal), timeout]).then((value) => {
      if (controller.signal.aborted) return;
      if (value.material_set.id !== id) throw new Error("返回的资料编号与恢复记录不同，未创建或绑定任何会议。");
      if (value.material_set.state === "draft" && value.material_set.meeting_id === null) { install(value.material_set); setItems(value.attachments.filter((item) => item.status !== "deleted")); recovered = true; }
      else { forgetMaterialDraft(serverScope, id); setError("原会前资料已经绑定会议或不再可用，请到会议列表查看原会议。未创建或绑定任何会议。"); }
    }).catch((reason) => { if (!controller.signal.aborted) setError(`会前资料未恢复：${reason instanceof Error ? reason.message : "读取失败"}。未创建或绑定任何会议，请保留原文件后重试。`); })
      .finally(() => { clearTimeout(timer); if (!controller.signal.aborted) { recoveryPending.current = false; setLoading(false); onRecoveryPending?.(false); if (recovered) setRefresh((value) => value + 1); } controller.abort(); });
    return () => { clearTimeout(timer); recoveryPending.current = false; controller.abort(); onRecoveryPending?.(false); };
    // Echoing this recovered set through the host must not refetch it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, meetingId, serverScope]);
  useEffect(() => {
    if (!open || recoveryPending.current) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    const load = async () => {
      try {
        let id = source?.meetingId === meetingId ? source.setId : set?.id;
        if (!id && meetingId) id = (await api.getForMeeting(meetingId, controller.signal))?.id;
        if (!id && !meetingId) id = readMaterialDraftPointer(serverScope) ?? undefined;
        if (!id) { if (!controller.signal.aborted) setLoading(false); return; }
        const value = await api.get(id, controller.signal);
        if (controller.signal.aborted) return;
        if (value.material_set.id !== id || (meetingId && value.material_set.meeting_id !== meetingId) || (!meetingId && (value.material_set.state !== "draft" || value.material_set.meeting_id !== null))) throw new Error("这份资料不属于当前准备范围。未创建或绑定任何会议，请到会议列表核对原会议。");
        install(value.material_set); setItems(value.attachments.filter((a) => a.status !== "deleted")); setLoading(false);
        if (value.attachments.some((a) => ["pending", "running", "processing"].includes(a.import_status))) timer = setTimeout(() => void load(), 1500);
      } catch (reason) { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "本场资料读取失败"); setLoading(false); } }
    };
    void load();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
    // Scope is immutable while open; explicit mutations advance refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, open, meetingId, refresh, source?.sourceId, serverScope]);
  useEffect(() => {
    if (!open || !set || !selected || dirty) return;
    const controller = new AbortController(); setDetail(null); setSpans([]); setContent(""); setEdit(false); setConfirmRemove(false);
    void (async () => {
      const value = await api.detail(set.id, selected, controller.signal);
      if (controller.signal.aborted) return;
      setDetail(value);
      const versionId = source?.attachmentId === selected ? source.versionId : value.latest_version_id ?? value.version?.id;
      if (!versionId) return;
      const page = await api.spans(set.id, selected, versionId, source?.attachmentId === selected ? source.ordinal ?? 0 : 0, controller.signal);
      if (controller.signal.aborted) return;
      if (page.version) setDetail({ ...value, version: page.version });
      const allowed = value.attachment.active_version_id === versionId ? value.attachment.selected_span_ids : null;
      setRangeMode(allowed === null ? "all" : "selected"); setSelectedSpans(allowed ?? []);
      setSpans(page.items); setNextOffset(page.has_more ? page.next_offset : null); setContent(page.items.map((s) => s.text).join("\n\n"));
    })().catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "正文读取失败"); });
    return () => controller.abort();
    // Do not discard the user's unsaved correction when another conversion ends.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, open, set?.id, selected, refresh, source?.versionId, selectedItem?.revision, selectedItem?.import_status]);
  const upload = async (file: File | undefined) => {
    if (!file || uploading.current || loading || dirty) return;
    uploading.current = true; setBusy(true); setError(""); const owner = scope.current;
    try {
      let target = set;
      if (!target) target = meetingId ? await api.forMeeting(meetingId) : await api.create();
      if (scope.current !== owner) return;
      install(target);
      const result = await api.upload(target.id, file);
      if (scope.current !== owner) return;
      const freshSet = result.material_set ?? (await api.get(target.id)).material_set;
      if (scope.current !== owner) return;
      install(freshSet);
      setSelected(result.attachment.id); setNotice("文件已上传，转换后检查正文，再用于本场背景。"); setRefresh((n) => n + 1);
    } catch (reason) { if (scope.current === owner) setError(reason instanceof Error ? reason.message : "上传失败"); }
    finally { if (scope.current === owner) setBusy(false); uploading.current = false; }
  };
  const mutate = async (operation: "save" | "use" | "disable") => {
    if (!set || !detail || busy) return; setBusy(true); setError(""); const owner = scope.current;
    try {
      if (operation === "save") {
        if (!detail.version || nextOffset !== null) throw new Error("请先加载完整正文，再保存新版本。");
        const value = await api.save(set.id, detail.attachment.id, content, detail.attachment.revision, detail.version.id);
        if (scope.current !== owner) return;
        setDetail({ ...value, latest_version_id: value.version.id, job: null }); setEdit(false); setNotice("新版本已保存；点击用于本场背景后才替换旧版。");
      } else {
        const versionId = detail.latest_version_id ?? detail.version?.id ?? null;
        if (operation === "use" && !versionId) throw new Error("转换尚未完成，请先检查正文。");
        if (operation === "use" && rangeMode === "selected" && !selectedSpans.length) throw new Error("请至少选择一段正文，或选择允许使用整个文件。");
        const value = await api.use(set.id, detail.attachment.id, versionId, operation === "use", detail.attachment.revision, operation === "use" && rangeMode === "selected" ? selectedSpans : null);
        if (scope.current !== owner) return;
        install(value.material_set); setNotice(operation === "use" ? "这一版已用于本场背景，后续建议会参考它。" : "已停用，后续建议不再使用；历史引用保留供核对。"); onChanged?.();
      }
      setRefresh((n) => n + 1);
    } catch (reason) { if (scope.current === owner) setError(reason instanceof Error ? reason.message : "保存失败，请刷新状态后重试。"); }
    finally { if (scope.current === owner) setBusy(false); }
  };
  const more = async () => {
    if (!set || !detail || nextOffset === null || busy) return; setBusy(true); const owner = scope.current;
    try { const page = await api.spans(set.id, detail.attachment.id, source?.attachmentId === selected ? source.versionId : detail.latest_version_id ?? detail.version!.id, nextOffset); if (scope.current !== owner) return; setSpans((old) => [...old, ...page.items]); setContent((old) => `${old}\n\n${page.items.map((s) => s.text).join("\n\n")}`); setNextOffset(page.has_more ? page.next_offset : null); }
    catch (reason) { if (scope.current === owner) setError(reason instanceof Error ? reason.message : "正文加载失败"); } finally { if (scope.current === owner) setBusy(false); }
  };
  const manage = async (action: "retry" | "cancel" | "remove") => {
    if (!set || !detail || busy || dirty) return;
    setBusy(true); setError(""); const owner = scope.current;
    try {
      if (action === "remove") {
        await api.remove(set.id, detail.attachment.id, detail.attachment.revision);
        if (scope.current !== owner) return;
        setSelected(null); setDetail(null); setSpans([]); setConfirmRemove(false); onChanged?.();
      } else {
        if (!detail.job) throw new Error("未找到转换任务，请刷新后重试。");
        await api[action](set.id, detail.job.id);
        if (scope.current !== owner) return;
      }
      setNotice(action === "retry" ? "转换已重新排队，不影响录音和转写。" : action === "cancel" ? "已取消转换；可以稍后重试或移除文件。" : "附件已移除，后续分析不再使用；正在清理本地文件。");
      setRefresh((n) => n + 1);
    } catch (reason) { if (scope.current === owner) setError(reason instanceof Error ? reason.message : "资料处理失败，请刷新后重试。"); }
    finally { if (scope.current === owner) setBusy(false); }
  };
  const removeDraft = async () => {
    if (!set || set.state !== "draft" || busy || dirty) return;
    setBusy(true); setError(""); const owner = scope.current;
    try {
      await api.removeDraft(set.id, set.revision);
      if (scope.current !== owner) return;
      forgetMaterialDraft(serverScope, set.id);
      setSet(null); onSetChange?.(null); setItems([]); setSelected(null); setDetail(null); setSpans([]); setConfirmDraftRemoval(false); setNotice("会前草稿资料已删除；可以不带资料开始，也可以重新添加文件。");
    } catch (reason) { if (scope.current === owner) setError(reason instanceof Error ? reason.message : "草稿资料删除失败，请刷新后重试。"); }
    finally { if (scope.current === owner) setBusy(false); }
  };
  const sections = detail?.version?.structure.sections;
  const sectionTitles = Array.isArray(sections) ? sections.flatMap((section) => section && typeof section === "object" && typeof section.title === "string" ? [section.title] : []).slice(0, 12) : [];
  const usingPreviewRange = Boolean(detail?.version && detail.attachment.enabled && detail.attachment.active_version_id === detail.version.id && (rangeMode === "all" ? detail.attachment.selected_span_ids === null : JSON.stringify([...selectedSpans].sort()) === JSON.stringify([...(detail.attachment.selected_span_ids ?? [])].sort())));
  const historical = Boolean(source && detail && source.versionId !== (detail.latest_version_id ?? detail.version?.id));
  const isSourceSpan = (span: AttachmentSpan) => Boolean(source && span.version_id === source.versionId && (source.ordinal !== undefined ? span.ordinal === source.ordinal : source.sourceId === span.id || source.sourceId === `material_span_${span.id}`));
  return <>
    <button className="secondary-button" type="button" onClick={() => setOpen(true)}><Paperclip size={15} />本场资料{items.length ? ` · ${items.length}` : "（可选）"}</button>
    {open ? createPortal(<div className="drawer-layer meeting-attachments-layer"><button className="drawer-scrim" aria-label="关闭本场资料" onClick={close} /><section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="meeting-attachments-title" className="meeting-attachments-dialog" tabIndex={-1}>
      <header><div><h2 id="meeting-attachments-title">本场资料</h2><p>{meetingId ? "仅用于这场讨论，长期整理可在会后进行。" : "先准备文件；无需建项目，转换不阻塞开始录音。"}</p></div><button type="button" className="icon-button" onClick={close} aria-label="关闭资料抽屉"><X size={18} /></button></header>
      <label className="attachment-upload"><span className="secondary-button">添加 DOCX、文本 PDF、TXT 或 MD</span><small>文件在后台转换，先核对正文再用于本场；录音无需等待转换。</small><input type="file" accept=".docx,.pdf,.txt,.md" disabled={busy || loading || dirty} onChange={(event) => { void upload(event.target.files?.[0]); event.target.value = ""; }} /></label>
      {loading || busy ? <p role="status"><LoaderCircle size={15} className="spin" />{busy ? "正在处理资料…" : "正在读取资料…"}</p> : null}
      {error ? <p role="alert" className="inline-error">{error}<button className="secondary-button" type="button" onClick={() => { setError(""); setRefresh((n) => n + 1); }}>刷新资料状态</button></p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {set?.state === "draft" ? <div className="attachment-draft-tools"><small>{items.length} 个会前文件 · 原件约 {(items.reduce((bytes, item) => bytes + item.file_size_bytes, 0) / 1024 / 1024).toFixed(2)} MiB · 草稿保留到你删除或绑定会议</small>{confirmDraftRemoval ? <><p>删除这些会前文件、正文和转换任务，不会删除任何会议。</p><button className="danger-button" type="button" disabled={busy || dirty} onClick={() => void removeDraft()}>确认删除草稿资料</button><button className="secondary-button" type="button" disabled={busy} onClick={() => setConfirmDraftRemoval(false)}>保留草稿资料</button></> : <button className="secondary-button" type="button" disabled={busy || dirty} onClick={() => setConfirmDraftRemoval(true)}>删除会前草稿资料</button>}</div> : null}
      <div className="meeting-attachments-content"><nav aria-label="本场附件">{items.map((item) => <button key={item.id} type="button" aria-pressed={selected === item.id} disabled={busy || dirty} onClick={() => { setSelected(item.id); onSourceClosed?.(); }}><FileText size={15} /><span>{item.title}<small>{item.enabled ? "用于本场" : item.latest_version_id ? "可预览 · 未启用" : item.import_status === "needs_ocr" ? "未提取文字" : item.error_message ?? "转换中"}</small></span></button>)}{!items.length && !loading ? <p>无需资料也能使用现场建议。有相关文件时再添加。</p> : null}</nav>
      <section aria-label="附件正文">{detail ? <><div className="attachment-body-scroll"><h3>{detail.attachment.title}</h3><p>{source?.attachmentId === selected ? `来源第 ${source.version} 版` : detail.version ? `第 ${detail.version.number} 版` : "等待转换"}{historical ? " · 历史版本，仅供核对" : ""}</p>{detail.version?.warnings.map((warning, index) => <p className="inline-warning" key={index}>{WARNINGS[warning] ?? warning}</p>)}{detail.attachment.error_message ? <p className="inline-error">{detail.attachment.error_message}</p> : null}
        {!source && detail.version ? <fieldset className="attachment-range"><legend>本场允许读取的范围</legend><label><input type="radio" name={`attachment-range-${detail.attachment.id}`} checked={rangeMode === "all"} disabled={busy || edit} onChange={() => setRangeMode("all")} />允许使用整个文件</label><label><input type="radio" name={`attachment-range-${detail.attachment.id}`} checked={rangeMode === "selected"} disabled={busy || edit} onChange={() => setRangeMode("selected")} />仅使用选中的正文片段</label>{rangeMode === "selected" ? <small>已选 {selectedSpans.length} 段；加载后续正文可继续选择，跨页选择会保留。</small> : null}<p>用于背景表示允许按相关片段取用，不代表 AI 已通读全文。</p>{detail.attachment.enabled ? <small>已启用{detail.attachment.active_version_id === detail.version.id ? `第 ${detail.version.number} 版` : "较早版本"} · {detail.attachment.selected_span_ids === null ? "允许整个文件" : `仅允许 ${detail.attachment.selected_span_ids.length} 段`}</small> : <small>尚未用于本场；上传和预览不会自动启用。</small>}</fieldset> : null}
        {sectionTitles.length ? <details><summary>原文结构 · {detail?.version?.content_characters ?? 0} 字符</summary><ol>{sectionTitles.map((title, index) => <li key={index}>{title}</li>)}</ol><small>来自原文标题和页段；完整正文按页段加载。</small></details> : null}
        {edit ? <textarea aria-label="修正附件正文" value={content} maxLength={2_000_000} onChange={(event) => setContent(event.target.value)} /> : <div className="attachment-spans">{spans.map((span) => <article key={span.id} ref={isSourceSpan(span) ? sourceAnchor : null} className={isSourceSpan(span) ? "attachment-span-highlight" : undefined}><small>{!source && rangeMode === "selected" ? <label><input type="checkbox" disabled={busy} aria-label={`选用第 ${span.ordinal + 1} 段`} checked={selectedSpans.includes(span.id)} onChange={(event) => setSelectedSpans((old) => event.target.checked ? [...new Set([...old, span.id])] : old.filter((id) => id !== span.id))} />选用此段 · </label> : null}{typeof span.locator.page === "number" ? `第 ${span.locator.page} 页` : `第 ${span.ordinal + 1} 段`}</small><p>{span.text}</p></article>)}</div>}
        {nextOffset !== null ? <button className="secondary-button" type="button" disabled={busy} onClick={() => void more()}>加载后续正文</button> : null}
        {source ? <p>当前显示引用附近的原文，不代表全文。<button className="secondary-button" type="button" onClick={() => { onSourceClosed?.(); setRefresh((n) => n + 1); }}>查看当前完整正文</button></p> : null}
        </div>{detail.version && !historical && !source ? <footer>{edit ? <><button className="secondary-button" type="button" disabled={busy || !dirty} onClick={() => void mutate("save")}>保存新版本</button><button className="secondary-button" type="button" disabled={busy} onClick={() => { setEdit(false); setContent(spans.map((s) => s.text).join("\n\n")); }}>取消编辑</button></> : <button className="secondary-button" type="button" disabled={busy || nextOffset !== null} onClick={() => setEdit(true)}>修正正文</button>}<button className="primary-button" type="button" disabled={busy || dirty || (rangeMode === "selected" && !selectedSpans.length) || (usingPreviewRange)} onClick={() => void mutate("use")}>{usingPreviewRange ? `正在使用第 ${detail.version.number} 版` : "用于本场背景"}</button>{detail.attachment.enabled ? <button className="secondary-button" type="button" disabled={busy || dirty} onClick={() => void mutate("disable")}>停用本场资料</button> : null}</footer> : null}
        {!source ? <footer className="attachment-management">{detail.job && ["failed", "cancelled"].includes(detail.job.status) ? <button className="secondary-button" type="button" disabled={busy || dirty} onClick={() => void manage("retry")}>重试转换</button> : null}{detail.job && ["queued", "pending", "running"].includes(detail.job.status) ? <button className="secondary-button" type="button" disabled={busy || dirty} onClick={() => void manage("cancel")}>取消转换</button> : null}{confirmRemove ? <><span>移除后不再用于本场，原文也会清理。</span><button className="danger-button" type="button" disabled={busy || dirty} onClick={() => void manage("remove")}>确认移除</button><button className="secondary-button" type="button" disabled={busy} onClick={() => setConfirmRemove(false)}>保留附件</button></> : <button className="secondary-button" type="button" disabled={busy || dirty} onClick={() => setConfirmRemove(true)}>移除此附件</button>}</footer> : null}
      </> : <p>选择文件查看转换正文和警告，然后决定是否用于本场。</p>}</section></div>
    </section></div>, document.body) : null}
  </>;
}
