/* ─────────────────────────────────────────────────────────────
 * PromptEditor — Two-tab editor for stage prompts and objection
 * playbooks. Fetches current values from /api/prompts and
 * allows live editing with save-to-backend.
 * ─────────────────────────────────────────────────────────── */
import { useState, useEffect, useCallback } from "react";
import type { StagePromptData, ObjectionPlaybook, PromptsUpdateRequest } from "../types";
import { fetchPrompts, updatePrompts } from "../api";

type Tab = "stages" | "objections";

export default function PromptEditor() {
  const [activeTab, setActiveTab] = useState<Tab>("stages");
  const [stagePrompts, setStagePrompts] = useState<StagePromptData[]>([]);
  const [objectionPlaybooks, setObjectionPlaybooks] = useState<ObjectionPlaybook[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasChanges, setHasChanges] = useState(false);

  // Load prompts from backend
  const loadPrompts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPrompts();
      setStagePrompts(data.stage_prompts);
      setObjectionPlaybooks(data.objection_playbooks);
      setHasChanges(false);
    } catch (err) {
      setError(`Failed to load prompts: ${err}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadPrompts(); }, [loadPrompts]);

  // Update a stage prompt locally
  const updateStagePrompt = (stage: string, newPrompt: string) => {
    setStagePrompts(prev =>
      prev.map(p => p.stage === stage ? { ...p, prompt: newPrompt } : p)
    );
    setHasChanges(true);
    setSaveMessage(null);
  };

  // Update an objection playbook locally
  const updateObjectionScript = (type: string, newScript: string) => {
    setObjectionPlaybooks(prev =>
      prev.map(p => p.type === type ? { ...p, script: newScript } : p)
    );
    setHasChanges(true);
    setSaveMessage(null);
  };

  // Save changes to backend
  const handleSave = async () => {
    setSaving(true);
    setSaveMessage(null);
    setError(null);

    const payload: PromptsUpdateRequest = {};

    if (activeTab === "stages") {
      payload.stage_prompts = {};
      stagePrompts.forEach(p => {
        payload.stage_prompts![p.stage] = p.prompt;
      });
    } else {
      payload.objection_playbooks = {};
      objectionPlaybooks.forEach(p => {
        payload.objection_playbooks![p.type] = p.script;
      });
    }

    try {
      await updatePrompts(payload);
      setSaveMessage("✓ Saved successfully! Changes are active for the current session.");
      setHasChanges(false);
    } catch (err) {
      setError(`Save failed: ${err}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="glass rounded-xl p-6 border border-white/70 flex items-center justify-center h-64">
        <div className="text-gray-600 text-sm animate-pulse">Loading prompts...</div>
      </div>
    );
  }

  return (
    <div className="glass rounded-xl border border-white/70 flex flex-col h-full">
      {/* Header with tabs */}
      <div className="flex items-center justify-between border-b border-white/70 px-4 py-3">
        <div className="flex gap-1">
          <button
            onClick={() => setActiveTab("stages")}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
              activeTab === "stages"
                ? "bg-neutral-900 text-white"
                : "text-gray-600 hover:text-gray-800 hover:bg-black/5"
            }`}
          >
            📋 Stage Prompts
          </button>
          <button
            onClick={() => setActiveTab("objections")}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
              activeTab === "objections"
                ? "bg-neutral-900 text-white"
                : "text-gray-600 hover:text-gray-800 hover:bg-black/5"
            }`}
          >
            🛡️ Objection Playbooks
          </button>
        </div>

        <div className="flex items-center gap-2">
          {hasChanges && (
            <span className="text-xs text-amber-600">● Unsaved</span>
          )}
          <button
            onClick={handleSave}
            disabled={saving || !hasChanges}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-green-400 text-gray-900 hover:bg-green-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? "Saving..." : "💾 Save"}
          </button>
          <button
            onClick={loadPrompts}
            className="px-2 py-1.5 text-xs text-gray-600 hover:text-gray-900 rounded-lg hover:bg-black/5 transition-colors"
            title="Reload from backend"
          >
            🔄
          </button>
        </div>
      </div>

      {/* Status messages */}
      {error && (
        <div className="mx-4 mt-3 px-3 py-2 bg-red-100/30 border border-red-200 rounded-lg text-xs text-red-700">
          {error}
        </div>
      )}
      {saveMessage && (
        <div className="mx-4 mt-3 px-3 py-2 bg-green-100/30 border border-green-200 rounded-lg text-xs text-green-700">
          {saveMessage}
        </div>
      )}

      {/* Scrollable prompt content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {activeTab === "stages" ? (
          /* Stage Prompts Tab */
          stagePrompts.map((sp) => (
            <div key={sp.stage} className="space-y-1.5">
              <div className="flex items-center gap-2">
                <label className="text-xs font-semibold text-violet-700 uppercase tracking-wide">
                  {sp.stage.replace(/_/g, " ")}
                </label>
              </div>
              {/* Read-only guidance from stages.py */}
              <div className="text-[11px] text-gray-500 italic bg-white/60 px-3 py-1.5 rounded border-l-2 border-lime-200">
                💡 {sp.guidance}
              </div>
              {/* Editable prompt textarea */}
              <textarea
                value={sp.prompt}
                onChange={(e) => updateStagePrompt(sp.stage, e.target.value)}
                rows={4}
                className="w-full bg-white/60 text-gray-800 text-sm rounded-lg px-3 py-2 border border-gray-300 focus:border-lime-500 focus:ring-1 focus:ring-lime-500 resize-y outline-none placeholder:text-gray-400 leading-relaxed"
                placeholder={`Enter ${sp.stage} stage prompt...`}
              />
            </div>
          ))
        ) : (
          /* Objection Playbooks Tab */
          objectionPlaybooks.map((op) => (
            <div key={op.type} className="space-y-1.5">
              <label className="text-xs font-semibold text-amber-700 uppercase tracking-wide">
                {op.type} Objection
              </label>
              <textarea
                value={op.script}
                onChange={(e) => updateObjectionScript(op.type, e.target.value)}
                rows={3}
                className="w-full bg-white/60 text-gray-800 text-sm rounded-lg px-3 py-2 border border-gray-300 focus:border-amber-500 focus:ring-1 focus:ring-amber-500 resize-y outline-none placeholder:text-gray-400 leading-relaxed"
                placeholder={`Enter ${op.type} objection response...`}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
