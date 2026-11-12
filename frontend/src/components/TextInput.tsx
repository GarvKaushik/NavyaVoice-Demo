/* ─────────────────────────────────────────────────────────────
 * TextInput — Fallback text input for typing messages when
 * microphone is unavailable or for quick testing.
 * ─────────────────────────────────────────────────────────── */
import { useState, type KeyboardEvent } from "react";

interface Props {
  onSend: (text: string) => void;
  disabled: boolean;
  placeholder?: string;
}

export default function TextInput({ onSend, disabled, placeholder }: Props) {
  const [text, setText] = useState("");

  const handleSend = () => {
    if (text.trim() && !disabled) {
      onSend(text.trim());
      setText("");
    }
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex items-center gap-2 p-3 glass border-t border-white/70">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder={placeholder || "Type a message (or speak into mic)..."}
        className="flex-1 bg-black/5 text-gray-800 text-sm rounded-lg px-4 py-2.5 border border-gray-300 focus:border-lime-500 focus:ring-1 focus:ring-lime-500 outline-none disabled:opacity-50 placeholder:text-gray-400"
      />
      <button
        onClick={handleSend}
        disabled={disabled || !text.trim()}
        className="px-4 py-2.5 bg-neutral-900 text-white text-sm font-medium rounded-lg hover:bg-lime-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        Send
      </button>
    </div>
  );
}
