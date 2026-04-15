import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from directive_store import get_directive_store
from llm_client import LLMClient

logger = logging.getLogger("quenbot.code_operator")

REPO_ROOT = Path(os.getenv("QUENBOT_CODE_OPERATOR_ROOT") or Path(__file__).resolve().parents[1])
DATA_DIR = Path(__file__).resolve().parent / "code_operator_data"
TASKS_FILE = DATA_DIR / "tasks.json"
BACKUP_DIR = DATA_DIR / "backups"
MAX_SELECTED_FILES = int(os.getenv("QUENBOT_CODE_MAX_FILES", "4"))
MAX_CONTEXT_CHARS = int(os.getenv("QUENBOT_CODE_MAX_CONTEXT_CHARS", "14000"))
MAX_TASKS = int(os.getenv("QUENBOT_CODE_MAX_TASKS", "60"))
PLAN_TIMEOUT_SECONDS = int(os.getenv("QUENBOT_CODE_PLAN_TIMEOUT", "45"))
EDIT_TIMEOUT_SECONDS = int(os.getenv("QUENBOT_CODE_EDIT_TIMEOUT", "80"))
STALE_TASK_TIMEOUT_SECONDS = int(os.getenv("QUENBOT_CODE_STALE_TIMEOUT", "180"))
SYSTEM_CONTEXT_MAX_CHARS = int(os.getenv("QUENBOT_CODE_SYSTEM_CONTEXT_CHARS", "12000"))
WEB_CONTEXT_MAX_CHARS = int(os.getenv("QUENBOT_CODE_WEB_CONTEXT_CHARS", "6000"))
WEB_FETCH_ENABLED = os.getenv("QUENBOT_CODE_WEB_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
WEB_SEARCH_RESULTS = int(os.getenv("QUENBOT_CODE_WEB_RESULTS", "5"))

CODE_OPERATOR_PLAN_PROMPT = """Sen QuenBot Code Operator Planner'sın.

GÖREVİN:
- Kullanıcının doğal dil kod isteğini repo yollarına çevir.
- En fazla 4 mevcut dosya seç.
- İstek çok geniş veya güvensizse clarification iste.
- Sadece JSON döndür.
 - QuenBot'un strateji, orchestrator, dashboard ve ajan mimarisini koru.

JSON ŞEMASI:
{
  "summary": "kisa plan",
  "needs_clarification": false,
  "clarification": "",
  "paths": ["python_agents/main.py"],
  "validation_commands": ["python3 -m py_compile python_agents/main.py"]
}

KURALLAR:
- Sadece verilen repo yollarından seç.
- Yeni dosya gerekmiyorsa mevcut dosyaları seç.
- Çok geniş isteklerde needs_clarification=true yap.
- paths boş kalabilir ama JSON daima geçerli olsun.
"""

CODE_OPERATOR_EDIT_PROMPT = """Sen QuenBot Code Operator'sün.

GÖREVİN:
- Verilen dosya bağlamlarını okuyup küçük, güvenli kod değişiklikleri üret.
- Sadece verilen dosyalar üzerinde çalış.
- Tam olarak eşleşecek old_snippet kullan.
- JSON dışında hiçbir şey döndürme.
 - QuenBot'un çok ajanlı trading mimarisini, risk kapılarını ve dashboard veri akışını bozmadan çalış.

JSON ŞEMASI:
{
  "summary": "kisa sonuc",
  "needs_clarification": false,
  "clarification": "",
  "edits": [
    {
      "path": "python_agents/main.py",
      "old_snippet": "eski kod",
      "new_snippet": "yeni kod",
      "reason": "neden"
    }
  ],
  "validation_commands": ["python3 -m py_compile python_agents/main.py"]
}

KURALLAR:
- old_snippet bağlamdan aynen kopyalanmalı.
- Değişiklikler minimal olmalı.
- Dosya bütünü yerine küçük replacement üret.
- Güvenli uygulama mümkün değilse needs_clarification=true yap.
"""

ALLOWED_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".css", ".yml", ".yaml", ".sh"
}
SYSTEM_KNOWLEDGE_FILES = [
    "README.md",
    "QWEN_ORCHESTRATOR_ARCHITECTURE.md",
    "SYSTEM_ENHANCEMENTS.md",
    "python_agents/architecture.md",
    "python_agents/README.md",
]
EXCLUDED_PARTS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__", ".venv", "logs", ".chroma", "code_operator_data"
}


class CodeOperator:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        self.repo_root = REPO_ROOT
        self._store = get_directive_store()
        self._client = LLMClient(
            model=os.getenv("QUENBOT_CODE_MODEL", os.getenv("QUENBOT_CHAT_MODEL", os.getenv("QUENBOT_LLM_MODEL", "supergemma-26b"))),
            timeout=int(os.getenv("QUENBOT_CODE_TIMEOUT", "90")),
            max_tokens=int(os.getenv("QUENBOT_CODE_MAX_TOKENS", "900")),
            max_retries=0,
        )
        self._client.num_ctx = int(os.getenv("QUENBOT_CODE_NUM_CTX", "8192"))
        self._client.num_thread = int(os.getenv("QUENBOT_CODE_NUM_THREAD", os.getenv("QUENBOT_LLM_NUM_THREAD", "14")))
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._active_task_id: Optional[str] = None

    async def start(self):
        await self._recover_pending_tasks()
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self):
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        await self._client.close()

    async def get_status(self) -> Dict[str, Any]:
        tasks = await self.list_tasks(limit=8)
        return {
            "enabled": True,
            "repo_root": str(self.repo_root),
            "model": self._client.model,
            "active_task_id": self._active_task_id,
            "queued": self._queue.qsize(),
            "recent_tasks": tasks,
            "available": await self._client.health_check(),
        }

    async def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        tasks = await self._load_tasks()
        return list(reversed(tasks[-max(1, limit):]))

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        tasks = await self._load_tasks()
        for task in tasks:
            if task.get("id") == task_id:
                return task
        return None

    async def submit_task(
        self,
        prompt: str,
        *,
        requested_by: str = "dashboard",
        mode: str = "preview",
        source: str = "dashboard",
    ) -> Dict[str, Any]:
        task_id = datetime.now(timezone.utc).strftime("code-%Y%m%d%H%M%S%f")
        task = {
            "id": task_id,
            "prompt": str(prompt or "").strip(),
            "requested_by": requested_by,
            "source": source,
            "mode": "apply" if str(mode).lower() == "apply" else "preview",
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "selected_files": [],
            "edits": [],
            "validation": [],
        }
        tasks = await self._load_tasks()
        tasks.append(task)
        tasks = tasks[-MAX_TASKS:]
        await self._save_tasks(tasks)
        logger.info("Code operator task queued: %s mode=%s source=%s", task_id, task["mode"], source)
        await self._queue.put({"type": "run", "task_id": task_id})
        return task

    async def apply_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = await self.get_task(task_id)
        if not task or not task.get("edits"):
            return None
        await self._queue.put({"type": "apply_existing", "task_id": task_id})
        return task

    async def _worker_loop(self):
        while True:
            item = await self._queue.get()
            try:
                kind = item.get("type")
                task_id = item.get("task_id")
                if kind == "run":
                    await self._run_task(task_id)
                elif kind == "apply_existing":
                    await self._apply_existing_task(task_id)
            except Exception as exc:
                logger.error("Code operator worker error: %s", exc)
            finally:
                self._queue.task_done()

    async def _run_task(self, task_id: str):
        task = await self.get_task(task_id)
        if not task:
            return
        self._active_task_id = task_id
        await self._update_task(task_id, {"status": "planning"})
        logger.info("Code operator planning started: %s", task_id)

        try:
            plan = await asyncio.wait_for(self._plan_request(task["prompt"]), timeout=PLAN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("Code operator planning timeout: %s", task_id)
            await self._update_task(task_id, {
                "status": "failed",
                "error": f"Planning timeout after {PLAN_TIMEOUT_SECONDS}s",
            })
            self._active_task_id = None
            return
        except Exception as exc:
            logger.exception("Code operator planning failed: %s", task_id)
            await self._update_task(task_id, {
                "status": "failed",
                "error": f"Planning failed: {exc}",
            })
            self._active_task_id = None
            return

        logger.info("Code operator planning finished: %s paths=%s clarification=%s", task_id, len(plan.get("paths", [])), bool(plan.get("needs_clarification")))

        if plan.get("needs_clarification"):
            await self._update_task(task_id, {
                "status": "needs_clarification",
                "plan": plan,
                "summary": plan.get("summary", ""),
                "clarification": plan.get("clarification", ""),
            })
            self._active_task_id = None
            return

        paths = [p for p in plan.get("paths", []) if self._is_allowed_relpath(p)]
        if not paths:
            await self._update_task(task_id, {
                "status": "failed",
                "error": "No editable files selected",
                "plan": plan,
            })
            self._active_task_id = None
            return

        await self._update_task(task_id, {
            "status": "planning",
            "plan": plan,
            "summary": plan.get("summary", ""),
            "selected_files": paths,
        })

        contexts = {path: self._read_file_context(path, task["prompt"]) for path in paths}
        logger.info("Code operator edit generation started: %s files=%s", task_id, ",".join(paths))
        try:
            proposal = await asyncio.wait_for(
                self._generate_edits(task["prompt"], paths, contexts, plan),
                timeout=EDIT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("Code operator edit generation timeout: %s", task_id)
            await self._update_task(task_id, {
                "status": "failed",
                "plan": plan,
                "selected_files": paths,
                "error": f"Edit generation timeout after {EDIT_TIMEOUT_SECONDS}s",
            })
            self._active_task_id = None
            return
        except Exception as exc:
            logger.exception("Code operator edit generation failed: %s", task_id)
            await self._update_task(task_id, {
                "status": "failed",
                "plan": plan,
                "selected_files": paths,
                "error": f"Edit generation failed: {exc}",
            })
            self._active_task_id = None
            return

        logger.info("Code operator edit generation finished: %s edits=%s clarification=%s", task_id, len(proposal.get("edits", [])), bool(proposal.get("needs_clarification")))

        if proposal.get("needs_clarification"):
            await self._update_task(task_id, {
                "status": "needs_clarification",
                "plan": plan,
                "selected_files": paths,
                "clarification": proposal.get("clarification", ""),
                "summary": proposal.get("summary", ""),
            })
            self._active_task_id = None
            return

        edits = proposal.get("edits", []) or []
        if not edits:
            await self._update_task(task_id, {
                "status": "failed",
                "error": "Model did not produce editable changes",
                "plan": plan,
                "selected_files": paths,
            })
            self._active_task_id = None
            return

        preview = [self._summarize_edit(edit) for edit in edits]
        await self._update_task(task_id, {
            "status": "preview_ready" if task.get("mode") != "apply" else "applying",
            "plan": plan,
            "summary": proposal.get("summary", plan.get("summary", "")),
            "selected_files": paths,
            "edits": edits,
            "preview": preview,
            "validation_commands": proposal.get("validation_commands") or plan.get("validation_commands") or self._default_validation_commands(paths),
        })

        if task.get("mode") == "apply":
            await self._apply_existing_task(task_id)
        self._active_task_id = None

    async def _apply_existing_task(self, task_id: str):
        task = await self.get_task(task_id)
        if not task:
            return
        self._active_task_id = task_id
        await self._update_task(task_id, {"status": "applying"})
        apply_result = self._apply_edits(task_id, task.get("edits", []))
        validation = await self._run_validation(task.get("validation_commands") or self._default_validation_commands(task.get("selected_files") or []))
        final_status = "applied" if apply_result.get("ok") else "failed"
        if apply_result.get("ok") and any(item.get("status") == "failed" for item in validation):
            final_status = "applied_with_warnings"
        await self._update_task(task_id, {
            "status": final_status,
            "apply_result": apply_result,
            "validation": validation,
        })
        self._active_task_id = None

    async def _plan_request(self, prompt: str) -> Dict[str, Any]:
        direct_replace_path = self._extract_direct_replace_target_path(prompt)
        if direct_replace_path:
            return {
                "summary": f"Direct replace path selected for {direct_replace_path}",
                "needs_clarification": False,
                "clarification": "",
                "paths": [direct_replace_path],
                "validation_commands": self._default_validation_commands([direct_replace_path]),
            }

        heuristic_paths = self._heuristic_candidate_paths(prompt)
        if heuristic_paths:
            return {
                "summary": "Heuristic file selection used",
                "needs_clarification": False,
                "clarification": "",
                "paths": heuristic_paths,
                "validation_commands": self._default_validation_commands(heuristic_paths),
            }

        manifest = self._build_repo_manifest(limit=220)
        directives = await self._store.get_master_directive()
        system_context = self._build_system_knowledge_context()
        web_context = self._build_web_context(prompt)
        response = await self._client.generate(
            prompt=(
                f"Kullanici istegi:\n{prompt}\n\n"
                f"Ana direktifler:\n{directives[:1200]}\n\n"
                f"Sistem bilgi paketi:\n{system_context}\n\n"
                f"Web arastirma baglami:\n{web_context or 'yok'}\n\n"
                f"Repo dosya listesi:\n" + "\n".join(manifest)
            ),
            system=CODE_OPERATOR_PLAN_PROMPT,
            temperature=0.1,
            json_mode=True,
            timeout_override=40,
            model_override=self._client.model,
            max_tokens_override=500,
            max_retries_override=0,
        )
        data = response.as_json() if response.success else None
        if isinstance(data, dict):
            normalized = {
                "summary": str(data.get("summary", "")).strip(),
                "needs_clarification": bool(data.get("needs_clarification", False)),
                "clarification": str(data.get("clarification", "")).strip(),
                "paths": [str(p) for p in list(data.get("paths") or [])[:MAX_SELECTED_FILES]],
                "validation_commands": [str(c) for c in list(data.get("validation_commands") or [])[:4]],
            }
            if normalized["paths"]:
                return normalized

        return {
            "summary": "Planlama daha dar kapsam gerektiriyor",
            "needs_clarification": True,
            "clarification": "İstek daha dar bir dosya kapsamı gerektiriyor.",
            "paths": [],
            "validation_commands": [],
        }

    async def _generate_edits(self, prompt: str, paths: List[str], contexts: Dict[str, str], plan: Dict[str, Any]) -> Dict[str, Any]:
        direct_replace = self._build_direct_replace_edit(prompt, paths)
        if direct_replace:
            return direct_replace

        system_context = self._build_system_knowledge_context()
        web_context = self._build_web_context(prompt)
        payload = {
            "request": prompt,
            "plan": plan,
            "system_context": system_context,
            "web_context": web_context,
            "files": [{"path": path, "context": contexts[path]} for path in paths],
        }
        response = await self._client.generate(
            prompt=json.dumps(payload, ensure_ascii=False),
            system=CODE_OPERATOR_EDIT_PROMPT,
            temperature=0.05,
            json_mode=True,
            timeout_override=65,
            model_override=self._client.model,
            max_tokens_override=1400,
            max_retries_override=0,
        )
        data = response.as_json() if response.success else None
        if not isinstance(data, dict):
            return {
                "needs_clarification": True,
                "clarification": "Model güvenli edit JSON'u üretemedi. İsteği daraltın.",
                "summary": "Edit generation failed",
                "edits": [],
            }

        edits: List[Dict[str, Any]] = []
        for raw in list(data.get("edits") or [])[:20]:
            path = str(raw.get("path", "")).strip()
            if path not in paths:
                continue
            old_snippet = str(raw.get("old_snippet", ""))
            new_snippet = str(raw.get("new_snippet", ""))
            if not path or not old_snippet or old_snippet == new_snippet:
                continue
            edits.append({
                "path": path,
                "old_snippet": old_snippet,
                "new_snippet": new_snippet,
                "reason": str(raw.get("reason", "")).strip(),
            })

        return {
            "summary": str(data.get("summary", "")).strip(),
            "needs_clarification": bool(data.get("needs_clarification", False)),
            "clarification": str(data.get("clarification", "")).strip(),
            "edits": edits,
            "validation_commands": [str(c) for c in list(data.get("validation_commands") or [])[:4]],
        }

    def _build_direct_replace_edit(self, prompt: str, paths: List[str]) -> Optional[Dict[str, Any]]:
        lower = prompt.lower()
        replace_keywords = ("replace", "degistir", "değiştir", "yazisini", "metnini")
        if not any(keyword in lower for keyword in replace_keywords):
            return None

        quoted_parts = re.findall(r"['\"`](.*?)['\"`]", prompt)
        if len(quoted_parts) < 2:
            return None

        target_path = next((path for path in paths if path in prompt), None)
        if not target_path and len(paths) == 1:
            target_path = paths[0]
        if not target_path:
            return None

        old_snippet = quoted_parts[0]
        new_snippet = quoted_parts[1]
        if not old_snippet or old_snippet == new_snippet:
            return None

        file_path = self.repo_root / target_path
        try:
            original = file_path.read_text(encoding="utf-8")
        except Exception:
            return {
                "needs_clarification": True,
                "clarification": f"Dosya okunamadi: {target_path}",
                "summary": "Direct replace failed",
                "edits": [],
            }

        occurrences = original.count(old_snippet)
        if occurrences != 1:
            return {
                "needs_clarification": True,
                "clarification": f"{target_path} icinde degisecek metin {occurrences} kez bulundu. Tekil bir ifade verin.",
                "summary": "Direct replace needs a unique snippet",
                "edits": [],
                "validation_commands": self._default_validation_commands([target_path]),
            }

        return {
            "summary": f"Deterministic replace prepared for {target_path}",
            "needs_clarification": False,
            "clarification": "",
            "edits": [{
                "path": target_path,
                "old_snippet": old_snippet,
                "new_snippet": new_snippet,
                "reason": "Prompt requested an exact text replacement.",
            }],
            "validation_commands": self._default_validation_commands([target_path]),
        }

    def _extract_direct_replace_target_path(self, prompt: str) -> Optional[str]:
        lower = prompt.lower()
        if not any(keyword in lower for keyword in ("replace", "degistir", "değiştir", "yazisini", "metnini")):
            return None

        manifest = self._build_repo_manifest(limit=500)
        for relpath in manifest:
            if relpath in prompt:
                return relpath
        return None

    def _apply_edits(self, task_id: str, edits: List[Dict[str, Any]]) -> Dict[str, Any]:
        changed_files: List[str] = []
        backup_root = BACKUP_DIR / task_id
        backup_root.mkdir(parents=True, exist_ok=True)
        for edit in edits:
            relpath = edit["path"]
            file_path = self.repo_root / relpath
            if not file_path.exists():
                return {"ok": False, "error": f"Missing file: {relpath}", "changed_files": changed_files}
            original = file_path.read_text(encoding="utf-8")
            occurrences = original.count(edit["old_snippet"])
            if occurrences != 1:
                return {
                    "ok": False,
                    "error": f"Snippet match count for {relpath} is {occurrences}, expected 1",
                    "changed_files": changed_files,
                }
            backup_path = backup_root / relpath
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, backup_path)
            updated = original.replace(edit["old_snippet"], edit["new_snippet"], 1)
            file_path.write_text(updated, encoding="utf-8")
            changed_files.append(relpath)
        return {"ok": True, "changed_files": changed_files, "backup_dir": str(backup_root)}

    async def _run_validation(self, commands: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for command in commands[:4]:
            try:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=str(self.repo_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
                output = (stdout or b"").decode("utf-8", errors="replace")[-6000:]
                results.append({
                    "command": command,
                    "status": "ok" if proc.returncode == 0 else "failed",
                    "returncode": proc.returncode,
                    "output": output,
                })
            except asyncio.TimeoutError:
                results.append({"command": command, "status": "failed", "returncode": -1, "output": "Validation timeout"})
            except Exception as exc:
                results.append({"command": command, "status": "failed", "returncode": -1, "output": str(exc)})
        return results

    def _read_file_context(self, relpath: str, prompt: str) -> str:
        file_path = self.repo_root / relpath
        text = file_path.read_text(encoding="utf-8")
        if len(text) <= MAX_CONTEXT_CHARS:
            return text
        keywords = [token for token in re.findall(r"[A-Za-z_]{3,}", prompt.lower()) if len(token) >= 3][:12]
        lines = text.splitlines()
        windows: List[tuple[int, int]] = [(0, min(len(lines), 80))]
        for idx, line in enumerate(lines):
            lower = line.lower()
            if any(keyword in lower for keyword in keywords):
                windows.append((max(0, idx - 20), min(len(lines), idx + 21)))
        windows.append((max(0, len(lines) - 120), len(lines)))
        merged: List[tuple[int, int]] = []
        for start, end in sorted(windows):
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        chunks: List[str] = []
        for start, end in merged:
            chunk = "\n".join(lines[start:end])
            chunks.append(f"# lines {start + 1}-{end}\n{chunk}")
        context = "\n\n...\n\n".join(chunks)
        return context[:MAX_CONTEXT_CHARS]

    def _build_repo_manifest(self, limit: int = 220) -> List[str]:
        paths: List[str] = []
        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_PARTS]
            for name in files:
                path = Path(root) / name
                rel = path.relative_to(self.repo_root)
                if any(part in EXCLUDED_PARTS for part in rel.parts):
                    continue
                if path.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                paths.append(str(rel))
        paths.sort()
        return paths[:limit]

    def _heuristic_candidate_paths(self, prompt: str) -> List[str]:
        lower = prompt.lower()
        preferred: List[str] = []
        if "dashboard" in lower or "panel" in lower or "ui" in lower:
            preferred.extend([
                "dashboard/src/components/CodeOperatorPanel.tsx",
                "dashboard/src/components/ChatPanel.tsx",
                "dashboard/src/components/ActiveSignals.tsx",
                "dashboard/src/components/StrategyControl.tsx",
                "dashboard/src/app/page.tsx",
                "dashboard/src/lib/api.ts",
            ])
        if "aktif sinyal" in lower or "sinyal kart" in lower or "target kart" in lower:
            preferred.extend([
                "dashboard/src/components/ActiveSignals.tsx",
                "artifacts/api-server/src/index.ts",
                "python_agents/database.py",
                "python_agents/strategist_agent.py",
            ])
        if "chat" in lower or "konus" in lower or "cevap" in lower or "yanit" in lower:
            preferred.extend([
                "python_agents/chat_engine.py",
                "python_agents/main.py",
                "dashboard/src/components/ChatPanel.tsx",
                "dashboard/src/lib/api.ts",
            ])
        if "strateji" in lower or "strateg" in lower or "signal" in lower:
            preferred.extend([
                "python_agents/strategist_agent.py",
                "python_agents/main.py",
                "python_agents/gemma_decision_core.py",
                "python_agents/chat_engine.py",
            ])
        if "api" in lower or "endpoint" in lower or "backend" in lower:
            preferred.extend([
                "artifacts/api-server/src/index.ts",
                "python_agents/main.py",
            ])
        if "kod" in lower or "code" in lower or "dosya" in lower or "file" in lower:
            preferred.extend([
                "python_agents/main.py",
                "python_agents/chat_engine.py",
                "artifacts/api-server/src/index.ts",
                "dashboard/src/lib/api.ts",
            ])

        manifest = self._build_repo_manifest(limit=500)
        tokens = [token for token in re.findall(r"[A-Za-z_]{3,}", lower) if len(token) >= 3]
        scored: List[tuple[int, str]] = []
        for rel in manifest:
            score = 0
            rel_lower = rel.lower()
            if rel in preferred:
                score += 10
            for token in tokens:
                if token in rel_lower:
                    score += 3
            if score > 0:
                scored.append((score, rel))
        scored.sort(key=lambda item: (-item[0], item[1]))
        picked: List[str] = []
        for _, rel in scored:
            if rel not in picked:
                picked.append(rel)
            if len(picked) >= MAX_SELECTED_FILES:
                break
        for rel in preferred:
            if rel not in picked and self._is_allowed_relpath(rel):
                picked.append(rel)
            if len(picked) >= MAX_SELECTED_FILES:
                break
        return picked[:MAX_SELECTED_FILES]

    def _default_validation_commands(self, paths: List[str]) -> List[str]:
        commands: List[str] = []
        py_files = [path for path in paths if path.endswith(".py")]
        if py_files:
            quoted = " ".join(py_files[:8])
            commands.append(f"python3 -m py_compile {quoted}")
        if any(path.startswith("dashboard/") for path in paths):
            commands.append("cd dashboard && pnpm build")
        elif any(path.startswith("artifacts/api-server/") for path in paths):
            commands.append("cd artifacts/api-server && pnpm exec tsc -p tsconfig.json --noEmit")
        return commands[:3]

    def _is_allowed_relpath(self, relpath: str) -> bool:
        path = (self.repo_root / relpath).resolve()
        try:
            path.relative_to(self.repo_root.resolve())
        except ValueError:
            return False
        if not path.exists() or path.suffix.lower() not in ALLOWED_SUFFIXES:
            return False
        return not any(part in EXCLUDED_PARTS for part in Path(relpath).parts)

    def _summarize_edit(self, edit: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "path": edit.get("path"),
            "reason": edit.get("reason", ""),
            "old_preview": str(edit.get("old_snippet", ""))[:220],
            "new_preview": str(edit.get("new_snippet", ""))[:220],
        }

    async def _load_tasks(self) -> List[Dict[str, Any]]:
        async with self._lock:
            if not TASKS_FILE.exists():
                return []
            try:
                return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            except Exception:
                return []

    def _build_system_knowledge_context(self) -> str:
        chunks: List[str] = []
        for relpath in SYSTEM_KNOWLEDGE_FILES:
            file_path = self.repo_root / relpath
            if not file_path.exists():
                continue
            try:
                text = file_path.read_text(encoding="utf-8")[:2600]
            except Exception:
                continue
            chunks.append(f"# {relpath}\n{text}")

        repo_memory_dir = self.repo_root / "memories" / "repo"
        if repo_memory_dir.exists():
            for memfile in sorted(repo_memory_dir.glob("*.md"))[:8]:
                try:
                    text = memfile.read_text(encoding="utf-8")[:1400]
                except Exception:
                    continue
                chunks.append(f"# memories/repo/{memfile.name}\n{text}")

        combined = "\n\n".join(chunks)
        return combined[:SYSTEM_CONTEXT_MAX_CHARS]

    def _build_web_context(self, prompt: str) -> str:
        if not WEB_FETCH_ENABLED:
            return ""

        contexts: List[str] = []
        for url in self._extract_public_urls(prompt)[:2]:
            fetched = self._fetch_public_url(url)
            if fetched:
                contexts.append(f"URL: {url}\n{fetched}")

        search_query = self._extract_search_query(prompt)
        if search_query:
            search_result = self._search_public_web(search_query)
            if search_result:
                contexts.append(f"SEARCH: {search_query}\n{search_result}")

        return "\n\n".join(contexts)[:WEB_CONTEXT_MAX_CHARS]

    def _extract_public_urls(self, prompt: str) -> List[str]:
        urls = re.findall(r"https?://[^\s)\]>\"]+", prompt)
        return [url for url in urls if self._is_safe_public_url(url)]

    def _extract_search_query(self, prompt: str) -> Optional[str]:
        patterns = [
            r"webde ara[:\-]?\s*(.+)",
            r"internette ara[:\-]?\s*(.+)",
            r"search[:\-]?\s*(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, prompt, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()[:180]
        return None

    def _is_safe_public_url(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        host = (parsed.hostname or "").lower()
        if not host or host in {"localhost", "0.0.0.0"}:
            return False
        if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
            return False
        if host.startswith("172."):
            parts = host.split(".")
            if len(parts) > 1 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
                return False
        return True

    def _fetch_public_url(self, url: str) -> str:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QuenBot-CodeOperator/1.0"})
            with urllib.request.urlopen(req, timeout=8) as response:
                raw = response.read(WEB_CONTEXT_MAX_CHARS).decode("utf-8", errors="replace")
        except Exception:
            return ""
        text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:WEB_CONTEXT_MAX_CHARS]

    def _search_public_web(self, query: str) -> str:
        try:
            url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
            req = urllib.request.Request(url, headers={"User-Agent": "QuenBot-CodeOperator/1.0"})
            with urllib.request.urlopen(req, timeout=8) as response:
                raw = response.read(WEB_CONTEXT_MAX_CHARS).decode("utf-8", errors="replace")
        except Exception:
            return ""

        items = re.findall(r'<a[^>]*class="result__a"[^>]*>(.*?)</a>', raw, flags=re.IGNORECASE)
        cleaned: List[str] = []
        for item in items[:WEB_SEARCH_RESULTS]:
            text = re.sub(r"<[^>]+>", " ", item)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                cleaned.append(f"- {text}")
        return "\n".join(cleaned)
    async def _save_tasks(self, tasks: List[Dict[str, Any]]):
        async with self._lock:
            TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _update_task(self, task_id: str, patch: Dict[str, Any]):
        tasks = await self._load_tasks()
        for task in tasks:
            if task.get("id") == task_id:
                task.update(patch)
                task["updated_at"] = datetime.now(timezone.utc).isoformat()
                break
        await self._save_tasks(tasks)

    async def _recover_pending_tasks(self):
        tasks = await self._load_tasks()
        if not tasks:
            return

        now = datetime.now(timezone.utc)
        changed = False
        resumed = 0
        recovered = 0

        for task in tasks:
            status = str(task.get("status", "")).lower()
            updated_at = task.get("updated_at") or task.get("created_at")
            try:
                updated_dt = datetime.fromisoformat(str(updated_at))
                if updated_dt.tzinfo is None:
                    updated_dt = updated_dt.replace(tzinfo=timezone.utc)
            except Exception:
                updated_dt = now

            age_seconds = (now - updated_dt).total_seconds()
            if status == "queued":
                await self._queue.put({"type": "run", "task_id": task.get("id")})
                resumed += 1
                continue

            if status in {"planning", "applying"} and age_seconds >= STALE_TASK_TIMEOUT_SECONDS:
                task["status"] = "failed"
                task["error"] = f"Recovered stale {status} task after restart ({int(age_seconds)}s idle)"
                task["updated_at"] = now.isoformat()
                recovered += 1
                changed = True

        if changed:
            await self._save_tasks(tasks)
        if resumed or recovered:
            logger.info("Code operator recovery: resumed=%s recovered=%s", resumed, recovered)


_code_operator: Optional[CodeOperator] = None


def get_code_operator() -> CodeOperator:
    global _code_operator
    if _code_operator is None:
        _code_operator = CodeOperator()
    return _code_operator