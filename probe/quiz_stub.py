# -*- coding: utf-8 -*-
"""本地自测联调桩：把 reading.html 真文件跑起来，接口用假数据。

用途：不连 CloudBase、不花 DeepSeek 额度，验证前端自测流程（出题→作答→讲解→结束）
以及 JS 是否能正常解析执行。不属于交付代码，放在 probe/ 下。
运行：python probe/quiz_stub.py  →  http://127.0.0.1:8899/reading.html
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STATIC = Path(__file__).resolve().parent.parent / "server" / "static"
PORT = 8899

QUESTIONS = [
    {"index": 0, "kind": "judgment", "focus": "knowledge", "stem": "注意力机制的权重是用 softmax 归一化得到的。"},
    {"index": 1, "kind": "choice", "focus": "concept", "stem": "下面哪个说法符合原文对交叉熵的描述？",
     "options": ["它是一种距离度量，满足对称性", "它不满足对称性，减去 p 的熵才是 KL 散度",
                 "它恒等于 KL 散度", "它可以为负数，意义不明"]},
    {"index": 2, "kind": "judgment", "focus": "concept", "stem": "多头注意力里每个头在训练前就被指定了各自负责的功能。"},
]
ANSWERS = {0: True, 1: 1, 2: False}
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".webp": "image/webp"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/oauth/status":
            return self._json({"ok": True, "authorized": True, "self_mode": False,
                               "name": "演示账号"})
        if path == "/api/reading/quiz":
            return self._json({"ok": True, "article": {"total": 3},
                               "summary": "这篇文章从注意力机制出发，先给出 Query/Key/Value 的计算方式，"
                                          "再说明它相比 RNN 的并行优势，最后交代交叉熵与 KL 散度的关系。",
                               "questions": QUESTIONS, "answered": 0})
        if path.startswith("/api/"):
            return self._json({"ok": False, "error": {"code": "STUB", "message": "桩未实现"}})
        return self._file(path)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if path == "/api/reading/review":
            mode = body.get("mode")
            if mode == "personal":
                return self._json({"ok": True, "mode": "personal", "cached": False,
                                   "text": "这篇讲的是注意力机制怎么把「查什么」和「有什么」对齐。"
                                           "你这次在交叉熵那里判断反了：它不满足对称性，所以不是距离；"
                                           "正确理解是先有 p 的熵，再谈用 q 编码的代价。"
                                           "另一处你跳过了多头注意力的分工问题——各头一开始并没有分工，"
                                           "分工是训练中自己形成的。"})
            return self._json({"ok": True, "mode": "general", "cached": False,
                               "text": "这篇从注意力机制出发：先把 Query 与每个 Key 做点积得到相似度，"
                                       "用 softmax 归一化后对 Value 加权求和，说明它相比 RNN 好在可并行；"
                                       "再把注意力放进信息论视角，说清交叉熵与 KL 散度的关系。"
                                       "最容易卡住的是把交叉熵当成距离——它不满足对称性。"})
        if path == "/api/reading/quiz/answer":
            idx = int(body.get("index", -1))
            pick = body.get("answer")
            kind = QUESTIONS[idx]["kind"]
            correct = (pick == ANSWERS.get(idx))
            right_index = ANSWERS[idx] if kind == "choice" else None
            right_bool = ANSWERS[idx] if kind == "judgment" else None
            answer_text = ("对" if right_bool else "错") if kind == "judgment" \
                else QUESTIONS[idx]["options"][right_index]
            payload = {"ok": True, "correct": correct, "chosen": str(pick),
                       "answer": answer_text,
                       "right_index": right_index, "right_bool": right_bool,
                       "explain": "" if correct else
                       "你选的那个把它当成了距离度量。距离要满足对称性，"
                       "而交叉熵 H(p,q) 与 H(q,p) 一般不相等，所以它不是距离。"}
            return self._json(payload)
        return self._json({"ok": True})       # 埋点等一律接受

    def _file(self, path):
        # 根路径给首页（和线上一致）；其余按文件路径找，找不到再退回精读页
        if path in ("/", "/index.html"):
            rel = "index.html"
        elif path == "/reading.html":
            rel = "reading.html"
        else:
            rel = path.lstrip("/")
        target = (STATIC / rel).resolve()
        if not str(target).startswith(str(STATIC)) or not target.is_file():
            target = STATIC / "reading.html"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"自测联调桩：http://127.0.0.1:{PORT}/reading.html")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
