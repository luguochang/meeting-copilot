import { Check, LoaderCircle, Pencil, X } from "lucide-react";
import { useEffect, useState } from "react";
import { meetingDisplayTitle } from "../app/meetingTitle";

interface MeetingTitleEditorProps {
  meetingId: string;
  title: string | null;
  timestamp?: number | null;
  onSave(title: string): Promise<void>;
}

export function MeetingTitleEditor({ meetingId, title, timestamp, onSave }: MeetingTitleEditorProps) {
  const displayTitle = meetingDisplayTitle(title, timestamp, meetingId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(displayTitle);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!editing) setDraft(displayTitle);
  }, [displayTitle, editing]);

  const save = async () => {
    const normalized = draft.trim();
    if (!normalized) {
      setError("会议名称不能为空");
      return;
    }
    if (normalized.length > 200) {
      setError("会议名称不能超过 200 个字符");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave(normalized);
      setEditing(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "会议名称保存失败");
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <div className="meeting-title-display">
        <h1 title={displayTitle}>{displayTitle}</h1>
        <button
          className="icon-button icon-button--small meeting-title-edit"
          type="button"
          onClick={() => setEditing(true)}
          aria-label="编辑会议名称"
          title="编辑会议名称"
        >
          <Pencil size={14} />
        </button>
      </div>
    );
  }

  return (
    <div className="meeting-title-editor">
      <label className="sr-only" htmlFor="meeting-title-input">会议名称</label>
      <input
        id="meeting-title-input"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") void save();
          if (event.key === "Escape") {
            setDraft(displayTitle);
            setEditing(false);
            setError(null);
          }
        }}
        maxLength={200}
        autoFocus
        disabled={saving}
      />
      <button className="icon-button icon-button--small" type="button" onClick={() => void save()} disabled={saving} aria-label="保存会议名称" title="保存">
        {saving ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}
      </button>
      <button
        className="icon-button icon-button--small"
        type="button"
        onClick={() => {
          setDraft(displayTitle);
          setEditing(false);
          setError(null);
        }}
        disabled={saving}
        aria-label="取消编辑会议名称"
        title="取消"
      >
        <X size={14} />
      </button>
      {error ? <span className="meeting-title-error" role="alert">{error}</span> : null}
    </div>
  );
}
