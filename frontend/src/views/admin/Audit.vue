<template>
  <div class="page">
    <h2>Audit Log</h2>
    <p class="desc">
      Security audit trail. Every query records a request ID, username, SQL, latency and result count
      via <code>app/core/audit.py</code> (Redis/Kafka backed). Table-level access is enforced by scope_guard.
    </p>
    <div class="card">
      <h3>Guarded paths</h3>
      <ul>
        <li>JWT auth + Redis session revocation on all <code>/api/*</code> routes</li>
        <li>Rate limit: 10 req/min per user (core/auth.py)</li>
        <li>SQL injection interception: 15 forbidden keywords (validate_sql_safety)</li>
        <li>Role-based table filtering (scope_guard.py)</li>
        <li>Python sandbox: AST validation + module whitelist + 30s subprocess timeout (code_executor.py)</li>
      </ul>
    </div>
    <p class="hint">Full audit query UI requires the audit service (Redis/Kafka) to be enabled.</p>
  </div>
</template>

<style scoped>
.page { max-width: 800px; margin: 0 auto; padding: 20px; }
.desc { color: #555; line-height: 1.6; }
.card { background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 16px; margin: 12px 0; }
.card h3 { margin: 0 0 12px; font-size: 15px; }
.card ul { margin: 0; padding-left: 20px; color: #444; line-height: 1.9; }
.hint { color: #999; font-size: 13px; }
</style>
