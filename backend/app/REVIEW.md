---
phase: code-review
reviewed: 2026-08-06T18:30:00Z
depth: deep
files_reviewed: 34
review_modes:
  java_backend: false
  vue3_frontend: false
skills_used: []
findings:
  critical: 9
  warning: 14
  info: 8
  total: 31
status: issues_found
---

# Phase: Code Review Report - AskInsight NL2SQL Pipeline

**Reviewed:** 2026-08-06T18:30:00Z
**Depth:** deep (cross-file call chain analysis)
**Files Reviewed:** 34
**Status:** issues_found - 9 BLOCKER, 14 WARNING, 8 INFO

## Summary

Deep adversarial review of the AskInsight NL2SQL pipeline covering 4 core areas: agent nodes (26 LangGraph nodes), security guards, API layer, and bootstrap scripts. The codebase demonstrates solid architecture, but reveals **9 blocking issues** that must be fixed before production deployment.

Most critical: (1) RCE in code_executor via triple-quote breakout, (2) hardcoded JWT secret + plaintext passwords in auth, (3) global mutable state causing request cross-contamination in query_tracer, (4) table whitelist bypass in validate_sql_safety.

---

## Critical Issues (BLOCKER)
### CR-01: Remote Code Injection via Triple-Quote Breakout in Code Executor
**File:** agent/nodes/code_executor.py:118-127
**Issue:** _execute_python injects data_json using f-string with triple-quoted string delimiters:
  wrapper = f..._data = json.loads("""{data_json}""")...
If data_json contains three consecutive single-quotes, it breaks out of the string literal and enables arbitrary Python code execution on the server. A crafted LLM response or upstream data payload can achieve full RCE.
**Fix:** Use json.dumps to produce a properly escaped JSON literal: escaped = json.dumps(data_json). Add ctypes, builtins, inspect to AST FORBIDDEN_IMPORTS. Add resource.setrlimit for memory limits on the subprocess.

### CR-02: Hardcoded JWT Secret in Source Code
**File:** core/auth.py:18
**Issue:** JWT_SECRET = os.getenv("JWT_SECRET", "data-agent-secret-change-me") exposes a default secret. Anyone with codebase access (public repo mirror, CI logs, decompiled dist) can forge valid JWTs for any user and bypass all authentication.
**Fix:** Remove the default value entirely: JWT_SECRET = os.getenv("JWT_SECRET"). If unset, auto-generate: import secrets; JWT_SECRET = secrets.token_hex(32)

### CR-03: Plaintext Password Storage and Comparison
**File:** core/auth.py:14, api/routers/auth_router.py:15
**Issue:** Passwords stored as plaintext in USERS dict, compared with user["password"] != body.password. Default "admin123" is a weak well-known password.
**Fix:** Hash with bcrypt: import bcrypt; password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()). Use bcrypt.checkpw() for comparison.
### CR-04: Global Mutable State - Request Cross-Contamination
File: core/query_tracer.py:96-102
Issue: _current_trace module-level global. Concurrent requests overwrite traces.
Fix: Use ContextVar instead of global.

### CR-05: Cache Key Collisions from Unstable hash() Function
**File:** core/cache.py:50,56,68,75
**Issue:** RedisCache uses Python hash(key) to generate cache keys. Python hash() is randomized per process (PYTHONHASHSEED) and explicitly unstable across interpreter restarts. After restart, previously cached entries become permanently orphaned but still consume Redis memory. Additionally, hash() collisions mean different queries could overwrite each other responses.
**Fix:** Use a cryptographic hash: import hashlib; cache_key = f{self.prefix}{hashlib.sha256(str(key).encode()).hexdigest()[:16]}

### CR-06: Table Whitelist Bypass -- Backtick-Quoted Identifiers
**File:** agent/nodes/validate_sql_safety.py:82-86
**Issue:** The table whitelist regex uses \w+ which matches [a-zA-Z0-9_] but does NOT match backtick-quoted table names. In Doris/MySQL, backtick-quoting is a valid identifier syntax. Malicious SQL with backtick-quoted table names slips past the whitelist check because the regex fails to capture the identifier.
**Fix:** Strip backtick quoting before matching: sql_normalized = re.sub(r-backtick-pattern-, r-replacement-, sql) then apply the existing whitelist regex.

### CR-07: SQL Comment Pattern -- False Positive in Safety Gateway
**File:** agent/nodes/validate_sql_safety.py:33,56
**Issue:** The DANGEROUS_PATTERNS list includes r-- which matches -- inside string literals (e.g., WHERE name = test--data), causing false-positive rejections of valid SQL queries.
**Fix:** Target statement-level SQL comments only: r^\s*-- (comment at line start) and r;\s*-- (comment after statement terminator).

### CR-08: Login Endpoint Returns HTTP 200 on Authentication Failure
**File:** api/routers/auth_router.py:13-17
**Issue:** Login returns {error: Invalid username or password} with implicit HTTP 200. Auth failures must return 401.
**Fix:** Return JSONResponse({error: ...}, status_code=401).

### CR-09: Thread Lock Blocks Event Loop in Async Cache
**File:** core/cache.py:14-32,62-63
**Issue:** TTLCache uses threading.Lock for synchronization. When RedisCache.get() (async) falls back to self._fallback.get(key), it acquires a threading.Lock synchronously inside an async coroutine, blocking the event loop. Under high load with Redis unavailable, this creates a cascading failure.
**Fix:** Replace threading.Lock with asyncio.Lock and make TTLCache get/set methods async (async def with async with self._lock).

---

## Warnings
### WR-01: Path Traversal via Case-Insensitive Windows Paths
**File:** core/path_guard.py:67-70
**Issue:** resolved.startswith(must_abs) is case-sensitive. On Windows, C:\SafeDirile does NOT start with c:\safedir. A case-mismatched path could bypass directory containment checks.
**Fix:** Normalize case with .lower() on Windows before startswith comparison.

### WR-02: Single Quote Global Replacement Corrupts JSON with Apostrophes
**File:** core/json_guard.py:109-112
**Issue:** fixed = fixed.replace(chr(39), chr(34)) blindly replaces ALL single quotes with double quotes, corrupting legitimate apostrophes in JSON string values (e.g., userchr(39)s data).
**Fix:** Remove this heuristic or scope it to key/value delimiter detection only.

### WR-03: LLM Fallback SQL SELECT 1 AS message Silent Execution
**File:** agent/nodes/generate_sql.py:107
**Issue:** When _clean_sql receives empty/non-SQL LLM output, it returns SELECT 1 AS message which passes all safety checks and executes successfully with fabricated data. The user sees results but they are meaningless.
**Fix:** Return a distinguishable sentinel string and have execute_sql detect and report the error to the user.

### WR-04: Population (Not Sample) Standard Deviation in Anomaly Detection
**File:** agent/nodes/anomaly_detection.py:56-57
**Issue:** Z-score uses population std dev (/N) not sample std dev (/(N-1)). For small baseline sizes (common in cold-start scenarios), this systematically underestimates variance, causing false-positive anomaly alerts.
**Fix:** Divide by (n - 1) and require n >= 2.

### WR-05: Prompt Injection via Sequential String Replacement
**File:** agent/nodes/decision_insight.py:67-72
**Issue:** .replace({query}, query).replace({data}, data_str)... uses sequential replacement. If user query contains {data} or {anomaly}, these placeholders in the user question are overwritten by subsequent .replace() calls.
**Fix:** Use unique sentinel placeholders like __QUERY__, __DATA__ instead of {query}, {data}.

### WR-06: IndexError in Few-Shot Generator When Alias Lists Are Empty
**File:** scripts/fewshot_generator.py:60-65
**Issue:** m[alias][0] and d[alias][0] assume non-empty alias lists. If a column has alias: [] in meta_config, raises IndexError and crashes the entire generation.
**Fix:** m[alias][0] if m.get(alias) else m[name].replace(_,  )

### WR-07: N+1 Database Queries in RRF Merge Node
**File:** agent/nodes/merge_retrieved_info.py:80-87
**Issue:** Each iteration in metric/value loops calls meta_repo.get_column_info_by_id(rc) individually. 20 metrics x 3 columns = 60 sequential DB queries causing significant latency.
**Fix:** Collect all missing column IDs and batch-fetch them in a single get_column_infos_by_ids call.
### WR-08: Sequential Embedding Calls Instead of Parallel
**File:** agent/nodes/recall_column.py:29-36, recall_metric.py:27-33
**Issue:** Keywords iterated sequentially, each generating an embedding API call followed by a Milvus search. For N keywords, this is 2N sequential network round-trips.
**Fix:** Use aembed_documents(list(keywords)) to batch all embeddings, then asyncio.gather for parallel Milvus searches.

### WR-09: Rate Limiter Race Condition (Incr-Expire Gap)
**File:** core/auth.py:47-51
**Issue:** Between incr(rate_key) and expire(rate_key, 60), if process crashes or event loop yields, key persists without TTL. On next request, incr from stale key (count > 1), expire not called again. Key lives forever, permanently locking out user.
**Fix:** Use Redis Lua script for atomic INCR + EXPIRE

### WR-10: Audit Log Entries with Silent Truncation and No PII Sanitization
**File:** core/audit.py:51-52
**Issue:** Query and SQL truncated silently at 500/2000 chars. If user query accidentally contains PII (email, phone), it is persisted without warning or redaction.
**Fix:** Add PII pattern detection (email, phone regex) and redact or hash sensitive fields in audit entries.

### WR-11: validate_scope Not Called from validate_sql_safety
**File:** core/scope_guard.py:38-46, agent/nodes/validate_sql_safety.py
**Issue:** validate_table_scope exists with docstring saying Used by validate_sql_safety but never called there. User-scope policy is not enforced at SQL safety layer.
**Fix:** Add validate_table_scope call in validate_sql_safety after the whitelist check.

### WR-12: First-Row-Only Type Detection in Chart Recommender
**File:** agent/nodes/chart_recommender.py:28-35
**Issue:** _detect_chart_heuristic checks _is_numeric(data[0].get(c)) only on first row. If first row has None for a numeric column but subsequent rows have values, column misclassified.
**Fix:** Sample first 5 rows for null-safe type detection.

### WR-13: query_service.py Catches Generic Error Without Stack Trace
**File:** services/query_service.py:59-63
**Issue:** except Exception as e: logger.error(f...{e}) logs only the message, losing the traceback needed for debugging production failures.
**Fix:** Use logger.exception(f...) which includes the full traceback.

### WR-14: Silent Exception Suppression in audit.py Fallback
**File:** core/audit.py:58-61
**Issue:** When both Kafka and Redis are unavailable, except Exception: pass silently drops audit logs with no logging or alerting.
**Fix:** Add logger.error(Audit log fallback failed, exc_info=True) in the except block.

---

## Info
### IN-01: Missing Timezone Awareness
**Files:** core/audit.py:43, agent/nodes/anomaly_detection.py
**Issue:** Uses datetime.now() without timezone. For distributed systems, datetime.now(timezone.utc) is preferred.

### IN-02: chr(92) Instead of Backslash Literal
**File:** core/path_guard.py:7,40
**Issue:** Uses chr(92) for backslash detection where idiomatic Python uses raw strings or escaped backslash.

### IN-03: Kafka acks=0 Fire-and-Forget
**File:** core/audit.py:25
**Issue:** Kafka producer with acks=0 and retries=0 means audit entries silently lost on transient broker failures.

### IN-04: _cached_report Global Shared Without Lock
**File:** api/routers/readiness_router.py:17
**Issue:** Module-level global read/written by concurrent get_readiness and refresh_readiness endpoints without synchronization.

### IN-05: Kafka Producer None/False Sentinel Confusion
**File:** core/audit.py:20-30
**Issue:** _kafka_producer uses None for uninitialized and False for unavailable. Fragile convention.

### IN-06: Linear (Not Exponential) Backoff in LLM Retry
**File:** core/llm_retry.py:27
**Issue:** asyncio.sleep(2 * (attempt + 1)) produces linear delays (2s,4s,6s). Convention is exponential backoff (2s,4s,8s,16s).

### IN-07: validate_sql Depends on Unreviewed Repository Method
**File:** agent/nodes/validate_sql.py:13
**Issue:** repo.validate_sql() implementation not in scope. Verify it uses pure syntax check (EXPLAIN without execution) with no database side effects.

### IN-08: CTE Aliases in ALLOWED_TABLES
**File:** agent/nodes/validate_sql_safety.py:43-47
**Issue:** CTE aliases (t, sub, tmp, cte, ranked, filtered) in whitelist. If real table named t is added, it skips validation. Maintenance concern.

---

_Reviewed: 2026-08-06T18:30:00Z_
_Reviewer: gsd-code-reviewer_
_Depth: deep_