"""Lokaler QA-Aufzeichnungsserver fuer Anthropic /v1/messages (nur 127.0.0.1). Zeichnet jede Anfrage (Pfad, Header-
Namen, Body) in <out>/requests.jsonl auf und antwortet mit einer SYNTHETISCHEN Antwort, die die erhaltenen Platzhalter
wiederverwendet (prueft die lokale Rueckfuehrung). Kein Weiterleiten, keine echte KI.
Usage: qa_recorder.py <port> <out_dir>"""
import json, os, re, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT, OUT = int(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "requests.jsonl"
PLACEHOLDER = re.compile(r"\[(?:PERSON|MANDANT|GEGNER|ORGANISATION|ORT|ADRESSE)_\d{2,}\]")


def answer_for(body: dict) -> str:
    text = json.dumps(body, ensure_ascii=False)
    found = list(dict.fromkeys(PLACEHOLDER.findall(text)))
    person = next((p for p in found if p.startswith(("[PERSON", "[MANDANT", "[GEGNER"))), "[PERSON_01]")
    return ("QA-Recorder (synthetische Antwort, keine echte KI): Entwurf eines Schreibens an das Finanzamt. "
            f"Bezugnehmend auf das Schreiben von {person} bitten wir um Aussetzung der Vollziehung. "
            f"Verwendete Platzhalter in der Anfrage: {len(found)}." + chr(10) + chr(10) + "Offene Fragen:" + chr(10) +
            "1. Liegt der Bescheid im Original vor?" + chr(10) + "2. Wann wurde der Bescheid zugestellt?")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # ruhig
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except Exception:
            body = {"_unparsed": raw.decode("utf-8", "replace")}
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "path": self.path, "header_names": sorted(self.headers.keys()),
                                 "api_key_prefix": (self.headers.get("x-api-key") or "")[:12], "body": body},
                                ensure_ascii=False) + "\n")
        time.sleep(float(os.environ.get("QA_REC_DELAY", "0")))
        if not self.path.startswith("/v1/messages"):
            self.send_response(404); self.end_headers(); return
        text = answer_for(body)
        model = body.get("model", "qa-recorder")
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            def ev(name, data):
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()); self.wfile.flush()
            ev("message_start", {"type": "message_start", "message": {"id": "msg_qa", "type": "message", "role": "assistant",
                "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1}}})
            ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            for i in range(0, len(text), 40):
                ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[i:i + 40]}})
            ev("content_block_stop", {"type": "content_block_stop", "index": 0})
            ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                 "usage": {"output_tokens": len(text) // 4}})
            ev("message_stop", {"type": "message_stop"})
            return
        payload = {"id": "msg_qa", "type": "message", "role": "assistant", "model": model,
                   "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                   "usage": {"input_tokens": 10, "output_tokens": len(text) // 4}}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
