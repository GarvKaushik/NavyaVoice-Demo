"""Launcher script for the Interactive Hinglish AI Voice Agent Frontend."""

import sys
import uvicorn

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  STARTING HINGLISH AI SALES AGENT — WEB MICROPHONE CONSOLE")
    print("=" * 70)
    print("  Open your browser to:  http://localhost:8000")
    print("  Interactive features:  Microphone capture, Audio visualizer,")
    print("                         Live Sales Stage pipeline, Output Guard,")
    print("                         Bulbul TTS audio playback, and Cost Ledger.")
    print("=" * 70 + "\n")

    uvicorn.run(
        "campaign.api:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )

