-- Migration (2026-08): clarify_session 关联原会话 request_id
-- 使旧版 /api/v1/clarify/{id}/resume 恢复流的事件也能归入原会话时间线。
-- 幂等:重复执行报"列已存在"可忽略。

ALTER TABLE clarify_session ADD COLUMN request_id VARCHAR(64) NULL COMMENT '原会话 request_id(session_event.session_id)';
